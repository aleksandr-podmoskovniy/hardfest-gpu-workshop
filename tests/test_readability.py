"""GitHub reading affordances; these checks do not replace visual review."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUIDES = (ROOT / "README.md", ROOT / "RTX5060.md")
TALK_TITLE = "Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере"


def main_path(text):
    """Closed disclosures leave their labels, not the supporting material.

    This is a structural regression check, not a measure of comprehension.
    The separate disclosure test rejects nested blocks.
    """
    return re.sub(r"<details>\s*<summary>(.*?)</summary>.*?</details>",
                  r"\1", text, flags=re.S)


class Readability(unittest.TestCase):
    def test_closed_route_keeps_eight_distinct_stories_in_the_requested_order(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                visible = main_path(path.read_text())
                headings = re.findall(r"^## (\d+)\. (.+)$", visible, re.M)
                self.assertEqual([n for n, _ in headings], list("12345678"))
                parts = re.split(r"(?m)^## \d+\. .+$", visible)[1:]
                parts[0] = visible.split("## 1.", 1)[0] + parts[0]
                stages = ("Gemma A", "Cache", "Tune", "A30", "InferenceService", "GPU", "Bifrost", "Qwen")
                for stage, (_, title), part in zip(stages, headings, parts):
                    self.assertIn(stage, title + part)
                # Base behavior is explained before the terms
                # needed for sharing a card or automating an allocation.
                for later_term in ("MIG", "MPS", "DRA", "OIDC", "ResourceClaim"):
                    self.assertNotRegex(parts[0], rf"\b{later_term}\b")

    def test_automation_is_introduced_after_memory_compute_and_placement(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                text = path.read_text()
                manual = text.split('<a id="platform"></a>', 1)[0]
                self.assertNotIn("InferenceService", manual)
                self.assertNotRegex(main_path(manual), r"(?i)платформенн\w+ (?:сервис|запуск)")
                positions = [text.index(f'<a id="{stage}"></a>') for stage in
                             ("ab", "monitoring", "memory", "ram", "speculation",
                              "latency", "placement", "platform", "conclusion", "tp2")]
                self.assertEqual(positions, sorted(positions))
                baseline = main_path(text).split('<a id="memory"></a>', 1)[0]
                for symptom in ("Waiting", "памят", "GPU"):
                    self.assertIn(symptom, baseline)

    def test_guides_are_manuals_not_audience_or_presenter_scripts(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", path.read_text())
                # Narrow regression guard for the rejected narrative. It does
                # not pretend to judge clarity or replace editorial review.
                direction = re.search(
                    r"(?i)договоритесь|соседями|проверьте себя|"
                    r"администратор (?:открывает|показывает)|"
                    r"(?:посмотрите|смотрите|попросите|предскажите)\b",
                    prose,
                )
                self.assertIsNone(direction, f"Stage direction: {direction.group() if direction else ''}")

    def test_long_context_arithmetic_is_not_hidden_with_operator_commands(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                visible = main_path(path.read_text())
                memory = visible.split('id="memory"', 1)[1].split('id="speculation"', 1)[0]
                self.assertIn("128K", memory)
                self.assertRegex(memory, r"(?:GiB|MiB)")
                self.assertIn("8", memory)
                self.assertIn("KV", memory)
                if path.name == "README.md":
                    self.assertIn("256K", memory)
                else:
                    # The layer-by-layer derivation remains in its disclosure,
                    # not between the short formula and its worked example.
                    self.assertNotIn("26-rtx-kv.svg", memory)

    def test_both_hardware_routes_keep_the_official_talk_title(self):
        for path in (*GUIDES, ROOT / "WORKSHOP.md"):
            with self.subTest(guide=path.name):
                prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", path.read_text())
                headings = re.findall(r"^# (.+)$", prose, re.M)
                self.assertEqual(headings, [TALK_TITLE])
        self.assertEqual((ROOT / "NOTICE").read_text().splitlines()[0], TALK_TITLE)

    def test_disclosures_are_balanced_named_and_not_nested(self):
        for path in GUIDES:
            text = path.read_text()
            tokens = re.findall(r"<details\b[^>]*>|</details>|<summary>.*?</summary>", text)
            opened = False
            named = False
            count = 0
            with self.subTest(guide=path.name):
                for token in tokens:
                    if token.startswith("<details"):
                        self.assertFalse(opened, "Nested disclosures hide the reading path")
                        self.assertNotIn(" open", token, "Supporting details are collapsed by default")
                        opened, named = True, False
                    elif token.startswith("<summary>"):
                        self.assertTrue(opened)
                        self.assertFalse(named)
                        label = token.removeprefix("<summary>").removesuffix("</summary>")
                        self.assertGreater(len(label.strip()), 10)
                        self.assertNotIn("**", label, "Summary is HTML, not Markdown")
                        named = True
                    else:
                        self.assertTrue(opened and named)
                        opened = False
                        count += 1
                self.assertFalse(opened)
                self.assertGreater(count, 0)
                self.assertNotRegex(text, r"</summary>\n[^\n]")
                self.assertNotRegex(text, r"[^\n]\n</details>")

    def test_reading_flow_uses_headings_not_alerts_for_every_paragraph(self):
        for path in GUIDES:
            text = path.read_text()
            with self.subTest(guide=path.name):
                # GitHub recommends one or two alerts per article; terms and
                # ordinary questions use prose/headings instead of warnings.
                self.assertLessEqual(len(re.findall(r"^> \[!\w+\]", text, re.M)), 2)
                prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", text)
                self.assertNotRegex(prose, r"(?m)^#{1,4} [^\n]+\n[^\n]")
                for tag in ("<script", "<style", "<iframe", "onmouseover="):
                    self.assertNotIn(tag, text.lower())

    def test_main_stories_have_visible_separators_and_short_prose_blocks(self):
        # Size is only a guard against the rejected wall of text. Causality
        # and usefulness still require reading the complete rendered guide.
        for path in GUIDES:
            with self.subTest(guide=path.name):
                visible = main_path(path.read_text())
                self.assertGreaterEqual(len(re.findall(r"(?m)^---$", visible)), 7)
                prose = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", visible)
                paragraphs = []
                for block in re.split(r"\n\s*\n", prose):
                    lines = [line for line in block.splitlines()
                             if line.strip() and not re.match(r"^\s*(?:[#|<>!]|---$|[-*] |\d+\. )", line)]
                    if lines:
                        paragraphs.append(" ".join(lines))
                for paragraph in paragraphs:
                    self.assertLessEqual(len(paragraph.split()), 75, paragraph)

    def test_main_memory_formula_is_a_separate_block_with_an_example(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                memory = main_path(path.read_text()).split('id="memory"', 1)[1].split('id="speculation"', 1)[0]
                formulas = re.findall(r"```text\n(.*?)```", memory, re.S)
                self.assertTrue(any("KV" in formula and ("=" in formula or "≈" in formula)
                                    for formula in formulas), "Formula must not be buried in running prose")
                self.assertIn("128K", memory)
                self.assertTrue("|" in memory or "26-rtx-kv-capacity.svg" in memory,
                                "Keep the worked long-context comparison as a table or diagram")

    def test_session_symbol_is_unambiguous_in_both_routes(self):
        for path in GUIDES:
            text = path.read_text()
            with self.subTest(guide=path.name):
                self.assertIn("`N`", text)
                self.assertNotRegex(text, r"(?:число|количество) [^\n]*истори[йя][^\n]*`B`")
                self.assertNotRegex(text, r"KV_bytes\s*=.*\bB\b")

    def test_long_context_has_a_visible_user_experiment_not_only_a_limit(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                visible = main_path(path.read_text())
                stage = visible.split('id="long-context"', 1)[1].split('id="placement"', 1)[0]
                for term in ("документ", "факт", "ответ", "журнал"):
                    self.assertIn(term, stage.lower())

    def test_chunking_explanation_is_not_preceded_by_test_parameters(self):
        visible = main_path((ROOT / "RTX5060.md").read_text())
        compute = visible.split('id="speculation"', 1)[1].split('id="long-context"', 1)[0]
        self.assertIn("Chunked prefill", compute)
        self.assertNotIn("50 мс", compute)
        self.assertNotIn("3072", compute)

    def test_it_basics_are_not_reintroduced_as_an_english_glossary(self):
        # The audience knows ordinary IT terms. This narrow guard prevents
        # the rejected acronym expansions, not useful domain explanations.
        paths = (*GUIDES, *(ROOT / "docs").glob("*.md"))
        unnecessary = re.compile(
            r"Graphics Processing Unit|Central Processing Unit|"
            r"(?:Video )?Random Access Memory|Application Programming Interface|"
            r"Large Language Model", re.I)
        for path in paths:
            with self.subTest(guide=path.name):
                self.assertIsNone(unnecessary.search(path.read_text()))

    def test_inference_mechanisms_keep_their_useful_explanations(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                text = path.read_text()
                for term in ("Key–Value", "Multi-Instance GPU", "Multi-Process Service",
                             "Dynamic Resource Allocation", "W4A16", "BF16", "FP8"):
                    self.assertIn(term, text)
                self.assertRegex(text, r"(?:два|двух) байт")
                self.assertIn("CPU→GPU", text)


if __name__ == "__main__":
    unittest.main()
