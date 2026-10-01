import json
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class QwenBenchmark(unittest.TestCase):
    def setUp(self):
        self.job = yaml.safe_load((ROOT / "examples/qwen-benchmark-job.yaml").read_text())
        self.pod = self.job["spec"]["template"]["spec"]
        self.container = self.pod["containers"][0]
        self.env = {item["name"]: item for item in self.container["env"]}

    def test_client_is_suspended_bounded_and_does_not_request_gpu(self):
        self.assertEqual(self.job["kind"], "Job")
        self.assertTrue(self.job["spec"]["suspend"])
        self.assertEqual(self.job["spec"]["backoffLimit"], 0)
        self.assertGreater(self.job["spec"]["activeDeadlineSeconds"], 0)
        self.assertGreater(self.job["spec"]["ttlSecondsAfterFinished"], 0)
        self.assertFalse(self.pod["automountServiceAccountToken"])
        self.assertEqual(self.pod["nodeSelector"]["kubernetes.io/hostname"], "REPLACE_CPU_NODE")
        self.assertNotIn("resourceClaims", self.pod)
        self.assertNotIn("claims", self.container["resources"])
        for part in ("requests", "limits"):
            self.assertFalse(any("gpu" in key.lower() for key in self.container["resources"][part]))
        self.assertFalse(self.container["securityContext"]["allowPrivilegeEscalation"])

    def test_credentials_are_only_secret_references(self):
        api_key = self.env["OPENAI_API_KEY"]
        self.assertNotIn("value", api_key)
        ref = api_key["valueFrom"]["secretKeyRef"]
        self.assertTrue(ref["name"].startswith("REPLACE_"))
        self.assertTrue(ref["key"].startswith("REPLACE_"))

    def test_tokenizer_and_image_are_pinned_without_weight_download(self):
        lock = json.loads((ROOT / "models.lock.json").read_text())
        # The lock and the client must pin the same bytes, regardless of display names.
        serialized = json.dumps(lock)
        self.assertIn(self.container["image"].split("@", 1)[1], serialized)
        self.assertIn(self.env["TOKENIZER_REVISION"]["value"], serialized)
        self.assertIn(self.env["TOKENIZER_ID"]["value"], serialized)
        script = self.container["args"][0]
        self.assertIn("hf download", script)
        self.assertIn("--include", script)
        self.assertNotIn("safetensors", script)
        self.assertIn("--random-range-ratio 0", script)
        self.assertIn("RESULT_JSON_BEGIN", script)
        self.assertIn("RESULT_JSON_END", script)
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
