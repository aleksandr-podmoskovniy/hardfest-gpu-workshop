import argparse
import importlib.util
import pathlib
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workshop.lab import private_output, dataset
from workshop.cli import make_parser
from workshop.manifests import read_json


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bench = load("bench")
report = load("report")
maths = load("kv_math")
manifests = load("check_manifests")


class Guide(unittest.TestCase):
    def test_readme_is_workshop_with_local_qr_at_top(self):
        readme = (ROOT / "README.md").read_text()
        url = "https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop"
        title = "Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере"
        self.assertIn("# " + title + "\n", readme)
        self.assertIn("Александр Подмосковный, Флант / Deckhouse Platform", readme)
        top = readme.split("# " + title, 1)[0]
        self.assertIn('src="assets/workshop-qr.svg"', top)
        self.assertIn('<p align="center">', top)
        self.assertIn('width="250" height="250"', top)
        self.assertIn(f'href="{url}"', top)
        for section in ("## Содержание", "## Подготовка окружения", "## Стенд и подключение",
                        "## 1. Где теряется время", "## Остановка"):
            self.assertIn(section, readme)
        svg = ET.parse(ROOT / "assets/workshop-qr.svg").getroot()
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}desc").text, url)
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}title").text, title)
        self.assertNotIn("<script", (ROOT / "assets/workshop-qr.svg").read_text())

    def test_illustrations_are_local_accessible_and_self_contained(self):
        readme = (ROOT / "README.md").read_text()
        images = re.findall(r"!\[([^]]+)\]\((assets/\d[^)]+\.svg)\)", readme)
        self.assertEqual(len(images), 12)
        for alt, filename in images:
            with self.subTest(filename=filename):
                self.assertGreater(len(alt), 20)
                raw = (ROOT / filename).read_text()
                svg = ET.fromstring(raw)
                self.assertEqual(svg.attrib["viewBox"].split()[2], "1200")
                self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}title"))
                self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}desc"))
                for node in svg.iter():
                    self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))
                    self.assertFalse(any(k.rsplit("}", 1)[-1] == "href" for k in node.attrib))

    def test_expanded_context_is_separate_from_ab(self):
        readme = (ROOT / "README.md").read_text()
        ram = readme.split('id="ram"', 1)[1].split('id="placement"', 1)[0]
        self.assertIn("max-model-len: 131072", ram)
        self.assertIn("cpu_bytes_to_use: 34359738368", ram)
        self.assertIn("остановите A через Git", ram)
        self.assertIn("deploy/gemma-b-128k/configmap.yaml", ram)
        self.assertIn("deploy/gemma-b-ram/configmap.yaml", ram)
        self.assertIn("того же Application B", ram)

    def test_primary_workshop_uses_gitops_not_private_python_wrappers(self):
        readme = (ROOT / "README.md").read_text()
        for old in ("python3", "scripts/hf.py", ".local/"):
            self.assertNotIn(old, readme)
        for command in ("apply --dry-run=server -f", "git commit -S -s", "git push"):
            self.assertIn(command, readme)
        self.assertNotIn("kustomize", readme.lower())
        self.assertNotIn("```text", readme)
        gitops = (ROOT / "docs/GITOPS.md").read_text()
        for term in ("ARGO_CONTEXT", "GPU_CONTEXT", "operation:{sync", "revision:$rev", "prune:false"):
            self.assertIn(term, gitops)

    def test_native_manifests_are_safe_and_complete(self):
        profiles = list((ROOT / "deploy").glob("*/configmap.yaml"))
        self.assertEqual(len(profiles), 8)
        self.assertEqual(manifests.check(), [])
        for path in profiles:
            with self.subTest(profile=path.parent.name):
                resources = (path.parent / "deployment.yaml").read_text()
                self.assertFalse((path.parent / "kustomization.yaml").exists())
                self.assertIn("checksum/vllm-config", resources)
                self.assertIn("replicas: 0", resources)
                self.assertIn('type: "Recreate"', resources)
                claim = (path.parent / "resourceclaimtemplate.yaml").read_text()
                self.assertIn('kind: "ResourceClaimTemplate"', claim)
                self.assertNotIn("cpu-offload-gb", path.read_text())
                self.assertRegex(resources, r"@sha256:[0-9a-f]{64}")
        for name in ("gemma-a", "gemma-b"):
            self.assertIn("max-model-len: 65536", (ROOT / "deploy" / name / "configmap.yaml").read_text())
        for path in (ROOT / "argocd").glob("*.yaml"):
            self.assertNotIn("automated:", path.read_text())
            self.assertNotIn("finalizers:", path.read_text())

    def test_participant_docs_do_not_contain_speaker_directions(self):
        paths = [ROOT / "README.md", ROOT / "WORKSHOP.md"]
        paths += list((ROOT / "labs").glob("*.md"))
        paths += list((ROOT / "docs/chapters").glob("*.md"))
        paths += [ROOT / "docs" / name for name in (
            "COMMANDS.md", "SETUP.md", "THEORY.md", "REHEARSAL.md", "STATUS.md",
            "MEMORY_BUDGET.md", "DEPLOYMENT.md")]
        forbidden = re.compile(
            r"\*\*сказать|предложить аудитории|вопрос залу|предъявить аудитории|"
            r"на сцене|до сцены|перед выступлением|ведущего|live-слот|"
            r"^## \d{2}[–-]\d{2}", re.I | re.M)
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(forbidden.search(path.read_text()))

    def test_workshop_contents_targets_exist_in_readme(self):
        readme = (ROOT / "README.md").read_text()
        anchors = set(re.findall(r'<a id="([^"]+)"></a>', readme))
        targets = re.findall(r"\]\(#([^)]+)\)", readme)
        self.assertGreaterEqual(len(targets), 10)
        self.assertTrue(set(targets).issubset(anchors))
        self.assertLess(readme.index('id="platform"'), readme.index('id="ram"'))
        self.assertLess(readme.index('id="ram"'), readme.index('id="placement"'))

    def test_workshop_has_one_canonical_source(self):
        alias = (ROOT / "WORKSHOP.md").read_text()
        self.assertIn("[README.md](README.md)", alias)
        self.assertNotIn("## ", alias)
        self.assertNotIn("```", alias)

    def test_chat_path_is_present_from_manual_to_platform_stages(self):
        readme = (ROOT / "README.md").read_text()
        self.assertLess(readme.index('id="chat"'), readme.index('id="setup"'))
        self.assertIn("Gemma A — Base", readme)
        self.assertIn("Gemma B — Tune", readme)
        self.assertIn("отдельным маршрутом Bifrost", readme)
        self.assertIn("сервис через AI Inference", readme)
        self.assertIn("docs/CHAT_AND_ACCESS.md", readme)
        guide = (ROOT / "docs/CHAT_AND_ACCESS.md").read_text()
        for term in ("pending", "Virtual Key", "OIDC", "MCP", "ACL", "отзыв"):
            self.assertIn(term, guide)

    def test_architecture_precedes_setup_with_plain_navigation(self):
        readme = (ROOT / "README.md").read_text()
        ordered = ["contents", "topology", "setup", "latency"]
        positions = [readme.index(f'<a id="{name}"></a>') for name in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("[К содержанию](#contents)", readme)

    def test_readme_has_no_decorative_tagline_or_boilerplate_labels(self):
        readme = (ROOT / "README.md").read_text()
        self.assertNotIn("·", readme)
        for phrase in ("практический мастер-класс ·", "Выберите учебный документ",
                       "**Ожидаемый результат:**", "**Вывод:**", "**Задача:**",
                       "Откройте мастер-класс на телефоне"):
            self.assertNotIn(phrase, readme)

    def test_every_slide_mapped_once(self):
        text = (ROOT / "docs/SLIDES_MAP.md").read_text()
        covered = []
        for a, b in re.findall(r"^\| (\d+)(?:–(\d+))? \|", text, re.M):
            covered.extend(range(int(a), int(b or a) + 1))
        self.assertEqual(covered, list(range(1, 53)))

    def test_memory_math_from_slides(self):
        self.assertEqual(maths.calculate()["per_layer_token_bytes"], 2048)
        self.assertEqual(maths.calculate()["one_session_gib"], 4.50439453125)
        self.assertEqual(maths.calculate(sessions=4)["all_sessions_gib"], 18.017578125)
        self.assertEqual(maths.calculate(sessions=8)["all_sessions_gib"], 36.03515625)
        self.assertEqual(maths.calculate()["ideal_full_sessions"], 3)
        self.assertEqual(maths.calculate(element_bytes=1)["one_session_gib"], maths.calculate()["one_session_gib"] / 2)

    def test_gemma_math_uses_global_kv_heads_and_sliding_window(self):
        for tokens, bf16 in ((131072, 10.78125), (133120, 10.9375), (262144, 20.78125)):
            with self.subTest(tokens=tokens):
                self.assertEqual(maths.calculate_gemma(tokens=tokens)["one_session_gib"], bf16)
                self.assertEqual(maths.calculate_gemma(tokens=tokens, element_bytes=1)["one_session_gib"], bf16 / 2)
        self.assertEqual(maths.calculate_gemma(tokens=512)["sliding_attention_gib"], .390625)
        self.assertEqual(maths.calculate_gemma(tokens=262144)["sliding_attention_gib"], .78125)
        self.assertEqual(maths.calculate_gemma(tokens=133120, sessions=8, element_bytes=1)["all_sessions_gib"], 43.75)

    def test_illustrated_memory_values_match_calculator(self):
        svg = (ROOT / "assets/03-memory.svg").read_text()
        for tokens in (65536, 131072, 262144):
            for element_bytes in (1, 2):
                value = maths.calculate_gemma(tokens=tokens, element_bytes=element_bytes)["one_session_gib"]
                self.assertIn(f"{value:.2f}".replace(".", ",") + " GiB", svg)

    def test_all_theory_diagrams_are_local_svg_without_external_content(self):
        diagrams = list((ROOT / "assets").glob("[0-9][0-9]-*.svg"))
        self.assertEqual(len(diagrams), 16)
        for path in diagrams:
            svg = ET.parse(path).getroot()
            self.assertEqual(svg.attrib["viewBox"].split()[2], "1200")
            for node in svg.iter():
                self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))
        for path in (ROOT / "docs/chapters").glob("*.md"):
            self.assertNotIn("```text", path.read_text())

    def test_public_output_rejected(self):
        with self.assertRaises(ValueError):
            private_output(ROOT / "leaked-snapshot.json")

    def test_dataset_rejects_output_overflow_before_exec(self):
        k = Mock()
        k.get.return_value = {"status": {"readyReplicas": 1}}
        site = read_json(ROOT / "config/site.example.json")
        args = argparse.Namespace(input_fraction=.99, output_tokens=10000, documents=32,
                                  out=str(ROOT / ".local/test-never-created.jsonl"))
        with self.assertRaisesRegex(ValueError, "fit"):
            dataset(k, site, "a", args)
        k.ns.assert_not_called()

    def test_public_example_can_parse_new_commands(self):
        for args in (["diff", "a", "b-cache"], ["init-site"],
                     ["dataset", "a", "--out", ".local/x.jsonl"],
                     ["model-info", "a"], ["snapshot", "a", "--out", ".local/x.json"]):
            self.assertIsNotNone(make_parser().parse_args(args))

    def test_shell_examples_are_parseable(self):
        docs = load("check_docs")
        count, errors = docs.check()
        self.assertGreater(count, 40)
        self.assertEqual(errors, [])


class Metrics(unittest.TestCase):
    def samples(self, total, count):
        return (f'vllm:request_queue_time_seconds_sum{{model_name="test"}} {total}\n'
                f'vllm:request_queue_time_seconds_count{{model_name="test"}} {count}\n')

    def test_queue_mean_from_counter_deltas(self):
        value = bench.server_timings(self.samples(100, 10), self.samples(120, 12))
        self.assertEqual(value["queue_mean_s"], 10)
        self.assertEqual(value["queue_completed"], 2)

    def test_counter_reset_not_zero_latency(self):
        value = bench.server_timings(self.samples(100, 10), self.samples(1, 1))
        self.assertIsNone(value["queue_mean_s"])

    def test_no_completions_not_zero_latency(self):
        value = bench.server_timings(self.samples(100, 10), self.samples(100, 10))
        self.assertIsNone(value["queue_mean_s"])

    def test_unavailable_series_not_inferred(self):
        self.assertNotIn("queue_mean_s", bench.server_timings(None, None))
        self.assertIsNone(bench.server_timings("", "")["queue_mean_s"])

    def test_report_warns_for_different_conditions(self):
        row = {"label": "one", "model": "test", "dataset_sha256": "test", "concurrency": 1,
               "summary": {"requests": 1, "successful": 1}}
        self.assertNotIn("WARNING", report.table([row, row]))
        self.assertIn("WARNING", report.table([row, {**row, "concurrency": 8}]))
        self.assertIn("WARNING", report.table([row, {**row, "dataset_offset": 1}]))


if __name__ == "__main__":
    unittest.main()
