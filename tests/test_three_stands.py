"""Shared narrative and Console-operation contracts for all three stands.

Offline only: rendering and public source checks are not live acceptance.
"""
from pathlib import Path
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("README.md", "RTX5060.md", "RTXPRO6000.md")


class ThreeStands(unittest.TestCase):
    def test_same_visible_navigation_and_eight_story_headings(self):
        reference = None
        for name in PAGES:
            text = (ROOT / name).read_text()
            headings = re.findall(r"^## (\d+\. .+)$", text, re.M)
            reference = reference or headings
            self.assertEqual(headings, reference, name)
            self.assertEqual(len(headings), 8)
            self.assertLess(text.index('id="contents"'), text.index("## 1."))
            self.assertNotIn("InferenceService", text.split('id="platform"')[0])
            for link in PAGES:
                self.assertIn(f"]({link})", text)
            for asset in ("29-chat-path", "03-kv-history", "05-kv-reuse", "06-scheduler"):
                self.assertIn(f"assets/{asset}.svg", text)
            self.assertIn("32K", text)

    def test_pro6000_diagrams_and_memory_numbers_match_its_hardware(self):
        root = ET.parse(ROOT / "assets/28-pro6000-tp2.svg").getroot()
        labels = " ".join(root.itertext())
        for term in ("RTX PRO 6000 / rank 0", "RTX PRO 6000 / rank 1", "VRAM", "NCCL"):
            self.assertIn(term, labels)
        for wrong in ("H100", "HBM", "NVLink"):
            self.assertNotIn(wrong, labels)
        from kv_math import calculate_gemma
        labels = " ".join(ET.parse(ROOT / "assets/27-pro6000-kv-capacity.svg").getroot().itertext())
        for length in (131072, 262144):
            for size in (1, 2):
                amount = calculate_gemma(length, 10, size)["all_sessions_gib"]
                self.assertIn(f"{amount:.2f}".replace(".", ",") + " GiB", labels)
        text = (ROOT / "RTXPRO6000.md").read_text().split('id="tp2"', 1)[1]
        self.assertLess(text.index("Если подходящего рецепта нет"), text.index("Перед запуском все ручные Gemma"))

    def test_reader_docs_do_not_embed_run_diaries(self):
        paths = [ROOT / name for name in PAGES] + list((ROOT / "docs").glob("*.md"))
        stale = re.compile(r"Историческ|Прежняя серия|прежним площадкам|"
                           r"заморожен после прогона|после этих прогонов|"
                           r"Три серии по десять одновременно поданных|"
                           r"Медиана видимого TTFT для одинаковой нагрузки|"
                           r"\d{1,2} октября|\| Метрика предельной серии")
        for path in paths:
            self.assertIsNone(stale.search(path.read_text()), path.name)

    def test_each_stand_has_three_independent_stopped_console_workloads(self):
        groups = (
            ("argocd", "values", ("gemma-a", "gemma-cache", "gemma-b")),
            ("argocd/rtx", "values/rtx", ("rtx-gemma-base", "rtx-gemma-cache", "rtx-gemma-tune")),
            ("argocd/pro6000", "values/pro6000", ("gemma-a", "gemma-cache", "gemma-b")),
        )
        for directory, profiles, files in groups:
            resources = set()
            for filename in files:
                app = yaml.safe_load((ROOT / directory / (filename + ".yaml")).read_text())
                spec = app["spec"]
                helm = spec["source"]["helm"]
                profile = ROOT / profiles / Path(helm["valueFiles"][0]).name
                name = helm["releaseName"]
                self.assertNotIn("automated", spec["syncPolicy"])
                self.assertNotIn("finalizers", app["metadata"])
                self.assertIn("RespectIgnoreDifferences=true", spec["syncPolicy"]["syncOptions"])
                self.assertIn("FailOnSharedResource=true", spec["syncPolicy"]["syncOptions"])
                self.assertEqual(spec["ignoreDifferences"], [{"group": "apps", "kind": "Deployment",
                    "name": name, "jsonPointers": ["/spec/replicas"]}])
                flags = ["helm", "template", name, str(ROOT / "charts/vllm-runtime"),
                         "-n", spec["destination"]["namespace"], "-f", str(profile)]
                for parameter in helm.get("parameters", []):
                    flags += ["--set-string", parameter["name"] + "=" + parameter["value"]]
                run = subprocess.run(flags, capture_output=True, text=True, check=True)
                objects = {o["kind"]: o for o in yaml.safe_load_all(run.stdout) if o}
                self.assertEqual(objects["Deployment"]["spec"]["replicas"], 0)
                for kind in ("Deployment", "Service", "ResourceClaimTemplate", "ConfigMap"):
                    key = (kind, objects[kind]["metadata"]["name"])
                    self.assertNotIn(key, resources, f"Shared ownership in {directory}")
                    resources.add(key)
                self.assertEqual(objects["Deployment"]["metadata"]["name"], name)
                self.assertEqual(objects["Service"]["metadata"]["name"], name)

    def test_readability_guards_cover_pro6000_too(self):
        from test_readability import main_path
        for name in PAGES:
            raw = (ROOT / name).read_text()
            visible = main_path(raw)
            self.assertNotIn("```mermaid", raw, "Use checked SVGs consistently across these guides")
            self.assertGreaterEqual(visible.count("\n---\n"), 7)
            self.assertLessEqual(len(re.findall(r"^> \[!\w+\]", raw, re.M)), 2)
            self.assertEqual(raw.count("<details>"), raw.count("</details>"))
            prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", visible)
            for block in re.split(r"\n\s*\n", prose):
                if re.match(r"^\s*(?:[#|<>!]|---$|[-*] |\d+\. )", block):
                    continue
                self.assertLessEqual(len(block.split()), 75, f"Long paragraph in {name}: {block}")

    def test_explanation_precedes_attention_diagram(self):
        for name in PAGES:
            text = (ROOT / name).read_text().split('id="memory"', 1)[1]
            image_at = text.index("assets/03-kv-history.svg")
            self.assertLess(text.lower().index("**attention**"), image_at, name)
            self.assertIn("sliding-window attention", text[:image_at].lower(), name)

    def test_reader_does_not_receive_editorial_instructions(self):
        for name in PAGES:
            text = (ROOT / name).read_text()
            for wording in ("Один и тот же путь на трёх стендах", "не индикатор его текущей доступности",
                            "не выдаётся за проверку", "Проверочный вопрос", "данный проход"):
                self.assertNotIn(wording, text, name)

    def test_headings_name_the_topic_without_decorative_transitions(self):
        # A narrow guard for a specifically rejected pattern, not a score
        # for prose quality. Causality and tone still need human review.
        paths = [ROOT / name for name in PAGES] + list((ROOT / "docs").glob("*.md"))
        for path in paths:
            prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", path.read_text())
            headings = re.findall(r"(?m)^#{1,6} (.+)$|<summary>(.*?)</summary>", prose)
            for markdown, disclosure in headings:
                with self.subTest(page=path.name, heading=markdown or disclosure):
                    self.assertNotRegex(markdown or disclosure, r"[→⇒↔↑↓]")


if __name__ == "__main__":
    unittest.main()
