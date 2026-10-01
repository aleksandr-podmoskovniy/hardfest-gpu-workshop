import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_manifests as checker


class HelmProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = json.loads((ROOT / "tests/fixtures/pre-helm.json").read_text())

    def test_all_profiles_preserve_pre_migration_runtime_and_allocation(self):
        self.assertEqual(len(self.before), 8)
        for name, old in self.before.items():
            with self.subTest(profile=name):
                actual = checker.render(ROOT / "values" / (name + ".yaml"))
                self.assertEqual(checker.validate_objects(actual), [])
                old_config = yaml.safe_load(old["configmap"]["data"]["profile.yaml"])
                new_config = yaml.safe_load(actual["ConfigMap"]["data"]["profile.yaml"])
                self.assertEqual(new_config, old_config)
                self.assertEqual(actual["ResourceClaimTemplate"]["spec"], old["resourceclaimtemplate"]["spec"])
                for kind, filename in (("Service", "service"), ("NetworkPolicy", "networkpolicy")):
                    self.assertEqual(actual[kind]["spec"], old[filename]["spec"])
                    self.assertEqual(actual[kind]["metadata"]["name"], old[filename]["metadata"]["name"])
                # Only derived hash/name are allowed to change in the Deployment.
                dep = copy.deepcopy(actual["Deployment"])
                pod = dep["spec"]["template"]
                old_pod = old["deployment"]["spec"]["template"]
                pod["metadata"]["annotations"]["checksum/vllm-config"] = old_pod["metadata"]["annotations"]["checksum/vllm-config"]
                pod["spec"]["resourceClaims"] = old_pod["spec"]["resourceClaims"]
                self.assertEqual(dep, old["deployment"])

    def render_override(self, overrides, success=True):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.yaml"
            path.write_text(yaml.safe_dump(overrides))
            proc = subprocess.run(["helm", "template", "hf-gemma-b", str(checker.CHART),
                                   "-n", "hardfest-demo", "-f", str(ROOT / "values/gemma-b.yaml"),
                                   "-f", str(path)], capture_output=True, text=True)
            if not success:
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                return proc.stderr
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return {o["kind"]: o for o in yaml.safe_load_all(proc.stdout) if o}

    def test_checksum_tracks_exact_mounted_config_without_manual_steps(self):
        before = checker.render(ROOT / "values/gemma-b.yaml")
        after = self.render_override({"vllm": {"max-model-len": 131072}})
        raw = after["ConfigMap"]["data"]["profile.yaml"]
        get_hash = lambda x: x["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/vllm-config"]
        self.assertNotEqual(get_hash(before), get_hash(after))
        self.assertEqual(get_hash(after), hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(before["ResourceClaimTemplate"], after["ResourceClaimTemplate"])

    def test_dra_spec_change_gets_a_new_name_and_pod_reference(self):
        before = checker.render(ROOT / "values/gemma-b.yaml")
        after = self.render_override({"dra": {"deviceClassName": "another-generated-class"}})
        name = after["ResourceClaimTemplate"]["metadata"]["name"]
        self.assertNotEqual(name, before["ResourceClaimTemplate"]["metadata"]["name"])
        self.assertEqual(after["Deployment"]["spec"]["template"]["spec"]["resourceClaims"][0]["resourceClaimTemplateName"], name)

    def test_variants_keep_one_b_service_and_selector(self):
        base = checker.render(ROOT / "values/gemma-b.yaml")
        for name in ("gemma-b-128k", "gemma-b-ram", "gemma-b-spec"):
            obj = checker.render(ROOT / "values" / (name + ".yaml"))
            self.assertEqual(base["Service"], obj["Service"])
            self.assertEqual(base["Deployment"]["spec"]["selector"], obj["Deployment"]["spec"]["selector"])

    def test_rejects_unpinned_image_bad_types_and_unsafe_flags(self):
        for values in ({"image": "vllm/vllm-openai:latest"}, {"replicaCount": 2},
                       {"dra": {"count": 1.5}}, {"vllm": {"enforce-eager": "false"}},
                       {"vllm": {"cpu-offload-gb": 0.5}}, {"dra": {"count": 2}},
                       {"vllm": {"port": 8001}}, {"unknownTopLevel": True}):
            with self.subTest(values=values):
                self.render_override(values, success=False)

    def test_running_with_placeholders_fails_closed(self):
        self.assertIn("replace site placeholders", self.render_override({"replicaCount": 1}, success=False))
        ready = self.render_override({"replicaCount": 1, "nodeSelector": {"kubernetes.io/hostname": "gpu-node"},
                                      "dra": {"deviceClassName": "h100-physical"},
                                      "modelVolumes": [{"name": "gemma", "claimName": "models",
                                                        "mountPath": "/models/gemma", "subPath": "gemma"}]})
        self.assertEqual(ready["Deployment"]["spec"]["replicas"], 1)

    def test_site_network_peers_do_not_replace_default_policy(self):
        rule = {"from": [{"namespaceSelector": {"matchLabels": {"purpose": "gateway"}},
                          "podSelector": {"matchLabels": {"app": "bifrost"}}}],
                "ports": [{"port": 8000, "protocol": "TCP"}]}
        objects = self.render_override({"networkPolicy": {"extraIngress": [rule]}})
        self.assertEqual(objects["NetworkPolicy"]["spec"]["ingress"][1], rule)
        self.assertEqual(len(objects["NetworkPolicy"]["spec"]["ingress"]), 2)

    def test_rejects_missing_assistant_mount_and_path_traversal(self):
        self.assertIn("no modelVolumes mount", self.render_override({
            "replicaCount": 1, "vllm": {"speculative-config": {"model": "/models/assistant"}},
        }, success=False))
        self.assertIn("must stay inside", self.render_override({
            "modelVolumes": [{"name": "gemma", "claimName": "models",
                              "mountPath": "/models/gemma", "subPath": "../outside"}]
        }, success=False))

    def test_configmap_reference_error_is_detected(self):
        objs = copy.deepcopy(checker.render(ROOT / "values/gemma-b.yaml"))
        objs["ConfigMap"]["metadata"]["name"] = "wrong-name"
        self.assertIn("ConfigMap reference mismatch", checker.validate_objects(objs))

    def test_multiple_yaml_documents_cannot_hide_extra_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.yaml"
            path.write_text("replicaCount: 0\n---\nreplicaCount: 1\n")
            with self.assertRaises(yaml.YAMLError):
                checker.render(path)


if __name__ == "__main__":
    unittest.main()
