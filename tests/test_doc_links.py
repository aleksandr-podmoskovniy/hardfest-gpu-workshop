from html import unescape
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".local", ".build", ".venv", "__pycache__"}


def anchors(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    result = set(re.findall(r'<a id="([^"]+)"', text))
    seen = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.M):
        heading = re.sub(r"<[^>]+>", "", unescape(heading)).lower()
        slug = re.sub(r"[^\w\-\s]", "", heading).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


class DocumentLinks(unittest.TestCase):
    def test_all_local_fragments_resolve_and_do_not_link_private_artifacts(self):
        checked = 0
        for path in ROOT.rglob("*.md"):
            if any(part in SKIP for part in path.relative_to(ROOT).parts):
                continue
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if "://" in target or " " in target:
                    continue
                file, _, fragment = target.partition("#")
                destination = (path.parent / unquote(file)).resolve() if file else path
                with self.subTest(file=path.relative_to(ROOT), target=target):
                    self.assertFalse(SKIP.intersection(destination.relative_to(ROOT).parts))
                    self.assertTrue(destination.exists())
                    if fragment and destination.suffix == ".md":
                        checked += 1
                        self.assertIn(unquote(fragment), anchors(destination.read_text()))
        self.assertGreater(checked, 20)
