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


class Guide(unittest.TestCase):
    def test_readme_is_workshop_with_local_qr_at_top(self):
        readme = (ROOT / "README.md").read_text()
        url = "https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop"
        title = "Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере"
        self.assertIn("# " + title + "\n", readme)
        self.assertIn("Александр Подмосковный, Флант / Deckhouse Platform", readme)
        top = readme.split("# " + title, 1)[0]
        self.assertIn('src="assets/workshop-qr.svg"', top)
        self.assertIn(f'href="{url}"', top)
        for section in ("## Содержание", "## Подготовка окружения", "## Схема стенда",
                        "## 1. Из чего складывается время ответа", "## 10. Остановка"):
            self.assertIn(section, readme)
        svg = ET.parse(ROOT / "assets/workshop-qr.svg").getroot()
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}desc").text, url)
        self.assertEqual(svg.find("{http://www.w3.org/2000/svg}title").text, title)
        self.assertNotIn("<script", (ROOT / "assets/workshop-qr.svg").read_text())

    def test_participant_docs_do_not_contain_speaker_directions(self):
        paths = [ROOT / "README.md", ROOT / "WORKSHOP.md"]
        paths += list((ROOT / "labs").glob("*.md"))
        paths += list((ROOT / "docs/chapters").glob("*.md"))
        paths += [ROOT / "docs" / name for name in (
            "COMMANDS.md", "SETUP.md", "THEORY.md", "REHEARSAL.md", "STATUS.md")]
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
        self.assertLess(readme.index('id="placement"'), readme.index('id="platform"'))

    def test_workshop_has_one_canonical_source(self):
        alias = (ROOT / "WORKSHOP.md").read_text()
        self.assertIn("[README.md](README.md)", alias)
        self.assertNotIn("## ", alias)
        self.assertNotIn("```", alias)

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
