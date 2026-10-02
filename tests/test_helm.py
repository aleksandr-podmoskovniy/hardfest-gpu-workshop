import copy
import hashlib
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
    def test_all_profiles_validate_and_use_explicit_gpu_count(self):
        profiles = sorted((ROOT / "values").glob("*.yaml"))
        self.assertEqual(len(profiles), 8)
        for path in profiles:
            with self.subTest(profile=path.name):
                actual = checker.render(path)
                self.assertEqual(checker.validate_objects(actual), [])
                config = yaml.safe_load(actual["ConfigMap"]["data"]["profile.yaml"])
                count = actual["ResourceClaimTemplate"]["spec"]["spec"]["devices"]["requests"][0]["exactly"]["count"]
                self.assertEqual(count, config.get("tensor-parallel-size", 1))
                self.assertNotIn("cpu-offload-gb", config)
                self.assertEqual(actual["Deployment"]["spec"]["replicas"], 0)

    def test_rollout_deadline_covers_cold_start_probe(self):
        actual = checker.render(ROOT / "values/gemma-b.yaml")
        spec = actual["Deployment"]["spec"]
        probe = spec["template"]["spec"]["containers"][0]["startupProbe"]
        probe_budget = probe["periodSeconds"] * probe["failureThreshold"]
        self.assertEqual(spec["progressDeadlineSeconds"], 2400)
        self.assertGreater(spec["progressDeadlineSeconds"], probe_budget)

    def test_ab_keeps_weights_context_and_gpu_budget_equal(self):
        profiles = [yaml.safe_load(checker.render(ROOT / "values" / name)["ConfigMap"]["data"]["profile.yaml"])
                    for name in ("gemma-a.yaml", "gemma-b.yaml")]
        for field in ("model", "max-model-len", "dtype", "gpu-memory-utilization"):
            self.assertEqual(profiles[0][field], profiles[1][field])
        self.assertEqual(profiles[0]["max-model-len"], 65536)
        self.assertTrue(profiles[0]["enforce-eager"])
        self.assertTrue(profiles[0]["no-enable-prefix-caching"])
        self.assertTrue(profiles[0]["enable-chunked-prefill"])
        self.assertEqual(profiles[0]["max-num-batched-tokens"], profiles[1]["max-num-batched-tokens"])
        self.assertEqual(profiles[1]["kv-cache-dtype"], "fp8")
        self.assertTrue(profiles[1]["enable-prefix-caching"])

    def test_second_iteration_keeps_cache_and_adds_compute_optimizations(self):
        first = yaml.safe_load((ROOT / "values/gemma-b.yaml").read_text())
        second = yaml.safe_load((ROOT / "values/gemma-b-spec.yaml").read_text())
        a, b = first["vllm"], second["vllm"]
        for key in ("model", "max-model-len", "dtype", "kv-cache-dtype",
                    "attention-backend", "enable-prefix-caching", "kv-transfer-config",
                    "max-num-seqs", "gpu-memory-utilization"):
            self.assertEqual(a[key], b[key], key)
        self.assertTrue(a["enforce-eager"])
        self.assertNotIn("speculative-config", a)
        self.assertEqual(a["max-num-batched-tokens"], 4096)
        self.assertEqual(b["max-num-batched-tokens"], 2048)
        self.assertFalse(b["enforce-eager"])
        self.assertEqual(b["speculative-config"], {
            "method": "mtp", "model": "/models/assistant", "num_speculative_tokens": 1})
        self.assertEqual(first["resources"], second["resources"])
        self.assertEqual(first["shmSize"], second["shmSize"])
        self.assertEqual(a["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"],
                         32 * 1024**3)

    def test_ram_profile_accounts_for_shared_memory(self):
        actual = checker.render(ROOT / "values/gemma-b-ram.yaml")
        config = yaml.safe_load(actual["ConfigMap"]["data"]["profile.yaml"])
        self.assertEqual(config["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"], 32 * 1024**3)
        pod = actual["Deployment"]["spec"]["template"]["spec"]
        self.assertEqual(next(v for v in pod["volumes"] if v["name"] == "shm")["emptyDir"],
                         {"medium": "Memory", "sizeLimit": "40Gi"})
        resources = pod["containers"][0]["resources"]
        self.assertEqual(resources["requests"]["memory"], "56Gi")
        self.assertEqual(resources["limits"]["memory"], "80Gi")

    def test_qwen_tp2_mtp_and_ram_match_the_host_budget(self):
        actual = checker.render(ROOT / "values/qwen-tp2.yaml")
        config = yaml.safe_load(actual["ConfigMap"]["data"]["profile.yaml"])
        self.assertEqual(config["tensor-parallel-size"], 2)
        self.assertEqual(config["max-model-len"], 262144)
        self.assertEqual(config["speculative-config"], {"method": "mtp", "num_speculative_tokens": 1})
        self.assertEqual(config["tool-call-parser"], "qwen3_xml")
        self.assertTrue(config["enable-auto-tool-choice"])
        self.assertEqual(config["safetensors-load-strategy"], "lazy")
        offload = config["kv-transfer-config"]
        self.assertEqual(offload["kv_connector"], "OffloadingConnector")
        self.assertEqual(offload["kv_connector_extra_config"]["cpu_bytes_to_use"], 16 * 1024**3)
        pod = actual["Deployment"]["spec"]["template"]["spec"]
        resources = pod["containers"][0]["resources"]
        self.assertEqual(resources["requests"]["cpu"], "12")
        self.assertEqual(resources["requests"]["memory"], "80Gi")
        self.assertEqual(resources["limits"]["memory"], "104Gi")
        self.assertEqual(next(v for v in pod["volumes"] if v["name"] == "shm")["emptyDir"],
                         {"medium": "Memory", "sizeLimit": "24Gi"})

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

    def test_device_selectors_are_optional_and_change_claim_identity(self):
        before = checker.render(ROOT / "values/gemma-b.yaml")
        selectors = [{"cel": {"expression": 'device.driver == "gpu.example.com"'}}]
        after = self.render_override({"dra": {"selectors": selectors}})
        request = lambda x: x["ResourceClaimTemplate"]["spec"]["spec"]["devices"]["requests"][0]["exactly"]
        self.assertNotIn("selectors", request(before))
        self.assertEqual(request(after)["selectors"], selectors)
        self.assertEqual(request(after)["count"], request(before)["count"])
        self.assertEqual(request(after)["deviceClassName"], request(before)["deviceClassName"])
        name = after["ResourceClaimTemplate"]["metadata"]["name"]
        self.assertNotEqual(name, before["ResourceClaimTemplate"]["metadata"]["name"])
        self.assertEqual(after["Deployment"]["spec"]["template"]["spec"]["resourceClaims"][0]["resourceClaimTemplateName"], name)

    def test_device_selector_schema_rejects_invalid_shapes(self):
        for selectors in ({}, [""], [{}], [{"cel": {}}],
                          [{"cel": {"expression": ""}}],
                          [{"cel": {"expression": True}}]):
            with self.subTest(selectors=selectors):
                self.render_override({"dra": {"selectors": selectors}}, success=False)

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
