"""GitHub reading affordances; these checks do not replace visual review."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUIDES = (ROOT / "README.md", ROOT / "RTX5060.md")


class Readability(unittest.TestCase):
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
