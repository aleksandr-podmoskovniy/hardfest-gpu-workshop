import importlib.util
import pathlib
import re
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
        self.assertGreaterEqual(len(images), 15)
        self.assertTrue({"assets/17-qwen-transition.svg", "assets/18-qwen-mtp.svg",
                         "assets/19-qwen-capacity.svg"}.issubset({p for _, p in images}))
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
        ram = readme.split('id="ram"', 1)[1].split('id="speculation"', 1)[0]
        second = readme.split('id="speculation"', 1)[1].split('id="platform"', 1)[0]
        self.assertNotIn("max-model-len: 131072", ram)
        self.assertIn("max-model-len: 131072", second)
        self.assertIn("max-model-len: 16384", second)
        self.assertIn("опыт на вместимость", second)
        self.assertIn("cpu_bytes_to_use: 34359738368", ram)
        self.assertIn("остановите A через Git", ram)
        for stale in ("gemma-b-128k.yaml", "gemma-b-ram.yaml"):
            self.assertNotIn(stale, readme)

    def test_primary_workshop_uses_gitops_not_private_python_wrappers(self):
        readme = (ROOT / "README.md").read_text()
        for old in ("python3", "scripts/hf.py", ".local/"):
            self.assertNotIn(old, readme)
        for command in ("apply --dry-run=server -f", "git commit -S -s", "git push"):
            self.assertIn(command, (ROOT / "docs/GITOPS.md").read_text())
        self.assertNotIn("kustomize", readme.lower())
        self.assertNotIn("```text", readme)
        gitops = (ROOT / "docs/GITOPS.md").read_text()
        for term in ("ARGO_CONTEXT", "GPU_CONTEXT", '--type merge --patch',
                     r'\"operation\"', r'\"revision\":\"$REVISION\"', r'\"prune\":false'):
            self.assertIn(term, gitops)

    def test_participant_commands_need_no_json_yaml_cli_or_python_wrapper(self):
        paths = [ROOT / "README.md", ROOT / "docs/GITOPS.md", ROOT / "docs/SETUP.md",
                 ROOT / "catalog/README.md"] + list((ROOT / "labs").glob("*.md"))
        for path in paths:
            shell = "\n".join(re.findall(r"```(?:bash|sh)\n(.*?)```", path.read_text(), re.S))
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(shell, r"(?m)(?:^|[|;])\s*(?:jq|yq|python3?)(?:\s|$)")

    def test_labs_expose_preconditions_numbered_steps_and_completion_checks(self):
        for path in (ROOT / "labs").glob("*.md"):
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertIn("## Перед началом", text)
                self.assertIn("## Проверка", text)
                self.assertGreaterEqual(len(re.findall(r"^## \d+\. ", text, re.M)), 3)

    def test_helm_profiles_are_safe_and_complete(self):
        profiles = list((ROOT / "values").glob("*.yaml"))
        self.assertEqual(len(profiles), 4)
        self.assertEqual(manifests.check(), [])
        for path in profiles:
            with self.subTest(profile=path.stem):
                objects = manifests.render(path)
                resources = objects["Deployment"]
                self.assertIn("checksum/vllm-config", resources["spec"]["template"]["metadata"]["annotations"])
                self.assertEqual(resources["spec"]["replicas"], 0)
                self.assertEqual(resources["spec"]["strategy"]["type"], "Recreate")
                self.assertIn("ResourceClaimTemplate", objects)
                self.assertNotIn("cpu-offload-gb", path.read_text())
                self.assertRegex(resources["spec"]["template"]["spec"]["containers"][0]["image"], r"@sha256:[0-9a-f]{64}")
        for name in ("gemma-a", "gemma-b"):
            self.assertIn("max-model-len: 16384", (ROOT / "values" / (name + ".yaml")).read_text())
        for path in (ROOT / "argocd").glob("*.yaml"):
            self.assertNotIn("automated:", path.read_text())
            self.assertNotIn("finalizers:", path.read_text())

    def test_participant_docs_do_not_contain_speaker_directions(self):
        paths = [ROOT / "README.md", ROOT / "WORKSHOP.md"]
        paths += list((ROOT / "labs").glob("*.md"))
        paths += list((ROOT / "docs").glob("*.md"))
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
        stages = ['ab', 'ram', 'speculation', 'platform', 'placement', 'tp2', 'cleanup']
        positions = [readme.index(f'id="{stage}"') for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(readme.count('<a id="speculation">'), 1)
        self.assertNotIn('Дополнительно. Черновая генерация', readme)

    def test_platform_chapter_describes_recipe_workflow(self):
        readme = (ROOT / "README.md").read_text()
        chapter = readme.split('id="platform"', 1)[1].split('id="placement"', 1)[0]
        lab = (ROOT / "labs/04-deckhouse.md").read_text()
        for term in ("Gemma 64K", "CPU KV", "assistant", "hf-platform-gemma", "Gemma A — DP"):
            self.assertIn(term, chapter)
        for term in ("charts/inference-service", "order.enabled: true", "model", "Ready",
                     "полный ответ"):
            self.assertIn(term, lab)
        self.assertIn("Не входят в этот базовый рецепт", lab)
        self.assertIn("Короткий вариант: A вручную, B через платформу", lab)

    def test_workshop_has_one_canonical_source(self):
        alias = (ROOT / "WORKSHOP.md").read_text()
        self.assertIn("[README.md](README.md)", alias)
        self.assertNotIn("## ", alias)
        self.assertNotIn("```", alias)

    def test_qwen_is_a_required_final_stage_with_operational_checks(self):
        readme = (ROOT / "README.md").read_text()
        chapter = readme.split('id="tp2"', 1)[1].split('id="cleanup"', 1)[0]
        self.assertRegex(chapter, r"## 8\. Qwen через AI Inference")
        self.assertNotIn("бонус", chapter.lower())
        self.assertLess(readme.index('id="tp2"'), readme.index('id="cleanup"'))
        for term in ("AI Inference", "DeviceClass", "tensor-parallel-size: 2",
                     "acceleratorCount=2", "/v1/chat/completions",
                     "Virtual Key", "OIDC", "MTP", "labs/06-tp2.md"):
            self.assertIn(term, chapter)
        for filename in ("17-qwen-transition.svg", "18-qwen-mtp.svg", "19-qwen-capacity.svg"):
            self.assertIn(filename, chapter)
        lab = (ROOT / "labs/06-tp2.md").read_text()
        self.assertIn("acceleratorCount=2", lab)
        self.assertIn("MTP", lab)
        self.assertIn("## 2. Освободить обе H100", lab)

    def test_platform_preflight_precedes_releasing_working_gpus(self):
        gemma = (ROOT / "labs/04-deckhouse.md").read_text()
        preflight = gemma.split("## 2. Освободить GPU и RAM", 1)[0]
        for term in ("inference-readiness", "до успешной проверки", "authentication: Token"):
            self.assertIn(term, preflight.lower() if term.startswith("до ") else preflight)
        qwen = (ROOT / "labs/06-tp2.md").read_text()
        preflight = qwen.split("## 2. Освободить обе H100", 1)[0]
        for field in ("acceleratorPolicy.maxAcceleratorCount", "scalingPolicy.minReplicas",
                      "scalingPolicy.maxReplicas", "exposurePolicy.authentication"):
            self.assertIn(field, preflight)
        self.assertIn("Не меньше `2`", preflight)
        self.assertIn("Оба `1`", preflight)

    def test_context_extension_preserves_second_iteration(self):
        lab = (ROOT / "labs/02-kv-ram.md").read_text()
        extension = lab.split("## Отдельный опыт:", 1)[1].split("## Проверка", 1)[0]
        for setting in ("vllm.max-model-len", "speculative-config", "prefill 2048",
                        "site/gemma-assistant.yaml", "65536"):
            self.assertIn(setting, extension)
        self.assertIn("Не заменяйте весь профиль", extension)

    def test_chat_path_is_present_from_manual_to_platform_stages(self):
        readme = (ROOT / "README.md").read_text()
        self.assertLess(readme.index('id="chat"'), readme.index('id="setup"'))
        self.assertIn("Gemma A — Base", readme)
        self.assertIn("Gemma B — Tune", readme)
        self.assertIn("отдельным маршрутом Bifrost", readme)
        self.assertIn("созданная через AI Inference", readme)
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

    def test_reference_docs_have_no_duplicate_workshop_or_editorial_pages(self):
        self.assertEqual({path.name for path in (ROOT / "docs").rglob("*.md")}, {
            "SETUP.md", "GITOPS.md", "MEMORY_BUDGET.md", "MEASUREMENTS.md",
            "OBSERVABILITY.md", "CHAT_AND_ACCESS.md", "TROUBLESHOOTING.md", "SOURCES.md",
        })

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
        self.assertEqual(len(diagrams), 19)
        docs = "\n".join(path.read_text() for path in [ROOT / "README.md"] +
                         list((ROOT / "docs").rglob("*.md")) +
                         list((ROOT / "labs").glob("*.md")))
        for path in diagrams:
            svg = ET.parse(path).getroot()
            self.assertEqual(svg.attrib["viewBox"], "0 0 1200 760")
            self.assertEqual(svg.attrib["data-design"], "hardfest-v2")
            self.assertIn(path.name, docs, f"Unreferenced illustration: {path.name}")
            for node in svg.iter():
                self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))

    def test_shell_examples_are_parseable(self):
        docs = load("check_docs")
        count, errors = docs.check()
        self.assertGreater(count, 40)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
