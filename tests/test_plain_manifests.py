import copy
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_manifests", ROOT / "scripts/check_manifests.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PlainManifests(unittest.TestCase):
    def test_profile_change_without_restart_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gemma-b"
            shutil.copytree(ROOT / "deploy/gemma-b", path)
            cm = checker.load(path / "configmap.yaml")
            cm["data"]["profile.yaml"] += "# changed\n"
            (path / "configmap.yaml").write_text(yaml.safe_dump(cm))
            self.assertIn("profile changed without updating the Pod checksum", checker.validate_profile(path))

    def test_configmap_reference_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gemma-b"
            shutil.copytree(ROOT / "deploy/gemma-b", path)
            cm = checker.load(path / "configmap.yaml")
            cm["metadata"]["name"] = "wrong-name"
            (path / "configmap.yaml").write_text(yaml.safe_dump(cm))
            self.assertIn("ConfigMap reference mismatch", checker.validate_profile(path))

    def test_yaml_profile_roundtrip_preserves_actual_engine_flags(self):
        for path in (ROOT / "deploy").glob("*/configmap.yaml"):
            raw = checker.load(path)["data"]["profile.yaml"]
            flags = yaml.safe_load(raw)
            self.assertIsInstance(flags, dict)
            self.assertIn("model", flags)
            dep = checker.load(path.parent / "deployment.yaml")
            self.assertEqual(dep["spec"]["template"]["metadata"]["annotations"]["checksum/vllm-config"],
                             hashlib.sha256(raw.encode()).hexdigest())

    @unittest.skipUnless(shutil.which("yq") and shutil.which("jq"), "optional participant CLI test")
    def test_documented_checksum_matches_exact_configmap_bytes(self):
        for path in (ROOT / "deploy").glob("*/configmap.yaml"):
            data = subprocess.check_output(["yq", "-o=json", ".", str(path)])
            raw = subprocess.check_output(["jq", "-j", '.data["profile.yaml"]'], input=data)
            self.assertEqual(raw.decode(), checker.load(path)["data"]["profile.yaml"])

    def test_ram_profile_does_not_change_weights_or_enable_weight_offload(self):
        a = yaml.safe_load(checker.load(ROOT / "deploy/gemma-b/configmap.yaml")["data"]["profile.yaml"])
        b = yaml.safe_load(checker.load(ROOT / "deploy/gemma-b-ram/configmap.yaml")["data"]["profile.yaml"])
        expected = copy.deepcopy(a)
        expected["max-model-len"] = 131072
        expected["kv-transfer-config"] = {
            "kv_connector": "OffloadingConnector", "kv_role": "kv_both",
            "kv_connector_extra_config": {"cpu_bytes_to_use": 34359738368},
        }
        self.assertEqual(b, expected)


if __name__ == "__main__":
    unittest.main()
