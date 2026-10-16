"""Size-review exceptions never exempt content checks or another file version."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_scan", ROOT / "scripts/check_public.py")
SCAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCAN)


class PublicSizeReview(unittest.TestCase):
    def inspect(self, content, approved=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "measured.json").write_text(content)
            with patch.object(SCAN, "ROOT", root), patch.object(SCAN, "MAX_UNREVIEWED_BYTES", 3), \
                    patch.object(SCAN, "REVIEWED_LARGE_FILES", approved or {}):
                return list(SCAN.problems())

    def test_exact_reviewed_content_passes_size_gate(self):
        text = '{"count":10}'
        approved = {"measured.json": hashlib.sha256(text.encode()).hexdigest()}
        self.assertEqual(self.inspect(text, approved), [])

    def test_reviewed_hash_does_not_skip_full_content_scan(self):
        text = '{"note":"' + "private-needle" + '"}'
        approved = {"measured.json": hashlib.sha256(text.encode()).hexdigest()}
        with patch.object(SCAN, "PATTERNS", ["private-needle"]):
            failures = self.inspect(text, approved)
        self.assertTrue(any("potential private content" in row for row in failures))

    def test_changed_hash_or_different_path_requires_review(self):
        text = '{"count":10}'
        digest = hashlib.sha256(text.encode()).hexdigest()
        for approved in ({}, {"measured.json": "0" * 64}, {"other.json": digest}):
            with self.subTest(approved=approved):
                self.assertEqual(self.inspect(text, approved),
                                 ["measured.json: large file requires manual review"])

    def test_committed_allowlist_matches_exact_artifacts(self):
        for name, digest in SCAN.REVIEWED_LARGE_FILES.items():
            with self.subTest(name=name):
                path = ROOT / name
                self.assertGreater(path.stat().st_size, SCAN.MAX_UNREVIEWED_BYTES)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
