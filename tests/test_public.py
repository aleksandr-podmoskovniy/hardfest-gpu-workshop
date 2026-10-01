import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_public", ROOT / "scripts/check_public.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


class PublicContent(unittest.TestCase):
    def test_credentials_and_secret_manifests_are_detected(self):
        samples = [
            "sk-" + "bf-" + "x" * 32,
            "hf_" + "x" * 32,
            "eyJ" + "x" * 20 + "." + "y" * 20 + "." + "z" * 20,
            "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----",
            "apiVersion: v1\nkind: " + "Secret\nmetadata: {}\n",
        ]
        for index, sample in enumerate(samples):
            with self.subTest(index=index):
                self.assertTrue(any(re.search(pattern, sample) for pattern in public.PATTERNS))


if __name__ == "__main__":
    unittest.main()
