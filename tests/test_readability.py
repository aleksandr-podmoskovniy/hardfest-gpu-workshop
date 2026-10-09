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
    def test_closed_route_keeps_six_stories_and_every_practical_stage(self):
        for path in GUIDES:
            with self.subTest(guide=path.name):
                visible = main_path(path.read_text())
                headings = re.findall(r"^## История (\d+)\. (.+)$", visible, re.M)
                self.assertEqual([n for n, _ in headings], list("123456"))
                parts = re.split(r"(?m)^## История \d+\. .+$", visible)[1:]
                parts[0] = visible.split("## История 1.", 1)[0] + parts[0]
                stages = ("Gemma A", "Cache", "Tune", "InferenceService", "A30", "Qwen")
                for stage, part in zip(stages, parts):
                    self.assertIn(stage, part)
                # The listener reaches the first experiment before the terms
                # needed for sharing a card or automating an allocation.
                for later_term in ("MIG", "MPS", "DRA", "OIDC", "ResourceClaim"):
                    self.assertNotRegex(parts[0], rf"\b{later_term}\b")

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
                        self.assertNotIn(" open", token, "Answers must not be revealed in advance")
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

    def test_session_symbol_is_unambiguous_in_both_routes(self):
        for path in GUIDES:
            text = path.read_text()
            with self.subTest(guide=path.name):
                self.assertIn("`N`", text)
                self.assertNotRegex(text, r"(?:число|количество) [^\n]*истори[йя][^\n]*`B`")
                self.assertNotRegex(text, r"KV_bytes\s*=.*\bB\b")


if __name__ == "__main__":
    unittest.main()
