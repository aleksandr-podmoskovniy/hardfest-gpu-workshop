import copy
from pathlib import Path
import subprocess
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_manifests as checker


class PRO6000Profiles(unittest.TestCase):
    def profile(self, stage):
        return checker.load(ROOT / "values/pro6000" / f"gemma-{stage}.yaml")

    def test_profiles_render_stopped_with_distinct_services_and_matching_claims(self):
        names = set()
        for stage in ("a", "cache", "b"):
            with self.subTest(stage=stage):
                profile = self.profile(stage)
                objects = checker.render(ROOT / "values/pro6000" / f"gemma-{stage}.yaml")
                self.assertEqual(checker.validate_objects(objects), [])
                name = f"selectel-gemma-{stage}"
                self.assertEqual(objects["Service"]["metadata"]["name"], name)
                self.assertEqual(objects["Deployment"]["metadata"]["name"], name)
                names.add(name)
                config = yaml.safe_load(objects["ConfigMap"]["data"]["profile.yaml"])
                self.assertEqual(config, profile["vllm"])
                self.assertEqual(config["served-model-name"], name)
                self.assertEqual(config["max-num-seqs"], 10)
                self.assertEqual(config["sse-keep-alive-interval"], 15)
                self.assertEqual(config["tensor-parallel-size"], profile["dra"]["count"])
                self.assertEqual(profile["image"], "REPLACE_RUNTIME_IMAGE@sha256:"
                    "f7cb9820c7e92eb78a4b3f60033e240c87a16bf99117471aba347fdd69eaca2d")
        self.assertEqual(len(names), 3)

    def test_auto_baseline_and_cache_do_not_invent_scheduler_flags(self):
        base, cache = (self.profile(stage)["vllm"] for stage in ("a", "cache"))
        self.assertEqual(base["max-model-len"], -1)
        self.assertEqual(base["kv-cache-dtype"], "auto")
        self.assertTrue(base["no-enable-prefix-caching"])
        self.assertNotIn("kv-offloading-size", base)
        for config in (base, cache):
            self.assertTrue(config["enforce-eager"])
            for key in ("enable-chunked-prefill", "no-enable-chunked-prefill",
                        "max-num-batched-tokens", "speculative-config"):
                self.assertNotIn(key, config)

    def test_cache_and_tune_preserve_ram_budget_and_assistant_delivery(self):
        for stage in ("cache", "b"):
            profile = self.profile(stage)
            config = profile["vllm"]
            self.assertEqual(config["max-model-len"], 262144)
            self.assertEqual(config["kv-cache-dtype"], "fp8")
            self.assertEqual(config["attention-backend"], "TRITON_ATTN")
            self.assertEqual(config["kv-offloading-size"], 80)
            self.assertEqual(config["kv-offloading-backend"], "native")
            self.assertEqual(profile["extraEnv"], [{"name": "VLLM_USE_SIMPLE_KV_OFFLOAD", "value": "0"}])
            self.assertEqual(profile["shmSize"], "81Gi")
            for bound in ("requests", "limits"):
                self.assertEqual(profile["resources"][bound]["memory"], "92Gi")
        profile = self.profile("b")
        config = profile["vllm"]
        self.assertTrue(config["enable-chunked-prefill"])
        self.assertFalse(config["enforce-eager"])
        self.assertEqual(config["max-num-batched-tokens"], 4096)
        self.assertEqual(config["speculative-config"], {
            "method": "mtp", "model": "/data/modelcache/models/gemma-4-31b-assistant",
            "num_speculative_tokens": 1})
        self.assertEqual(profile["modelRefs"], ["gemma-4-31b", "gemma-4-31b-assistant"])

    def test_max_model_len_accepts_auto_or_positive_integer_only(self):
        for value, valid in (("-1", True), ("167520", True), ("0", False),
                             ("-2", False), ("1.5", False)):
            with self.subTest(value=value):
                result = subprocess.run(["helm", "template", "pro6000", str(checker.CHART),
                    "-f", str(ROOT / "values/pro6000/gemma-a.yaml"),
                    "--set", f"vllm.max-model-len={value}"], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_unresolved_placement_cannot_start_a_profile(self):
        result = subprocess.run(["helm", "template", "pro6000", str(checker.CHART),
            "-f", str(ROOT / "values/pro6000/gemma-a.yaml"), "--set", "replicaCount=1"],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("replace site placeholders", result.stderr)

    def test_argo_manual_sync_preserves_only_ui_replicas(self):
        paths = sorted((ROOT / "argocd/pro6000").glob("*.yaml"))
        self.assertEqual({path.stem for path in paths}, {"gemma-a", "gemma-cache", "gemma-b"})
        for path in paths:
            profile = checker.load(ROOT / "values/pro6000" / path.name)
            app = checker.load(path)
            self.assertEqual(checker.validate_pro6000_application(app, profile), [])
            for mutation in ("remove-respect", "other-deployment", "autosync"):
                changed = copy.deepcopy(app)
                if mutation == "remove-respect":
                    changed["spec"]["syncPolicy"]["syncOptions"].remove("RespectIgnoreDifferences=true")
                elif mutation == "other-deployment":
                    changed["spec"]["ignoreDifferences"][0]["name"] = "unrelated"
                else:
                    changed["spec"]["syncPolicy"]["automated"] = {}
                self.assertTrue(checker.validate_pro6000_application(changed, profile), mutation)

    def test_platform_reference_orders_use_actual_schema_without_argo_owner(self):
        for filename, name, model in (
                ("gemma.yaml", "selectel-gemma31", "gemma-4-31b"),
                ("qwen.yaml", "selectel-qwen38-tp2", "qwen3-8-flash-next-nvfp4")):
            order = checker.load(ROOT / "platform/pro6000" / filename)
            self.assertEqual(order["apiVersion"], "ai.deckhouse.io/v1alpha1")
            self.assertEqual(order["kind"], "InferenceService")
            self.assertEqual(order["metadata"], {"name": name, "namespace": "hardfest-demo"})
            spec = order["spec"]
            self.assertEqual(set(spec), {"inferenceServiceClassName", "model", "launchStrategy", "resources"})
            self.assertEqual(spec["launchStrategy"], "Latency")
            self.assertEqual(spec["model"], {"src": "ai-models", "ref": {"kind": "Model", "name": model}})
            self.assertEqual(spec["inferenceServiceClassName"],
                             {"gemma.yaml": "hardfest-selectel-gemma31",
                              "qwen.yaml": "hardfest-selectel-qwen38-tp2"}[filename])
            self.assertEqual(spec["resources"]["accelerator"]["deviceClasses"],
                             ["hardfest-selectel-blackwell"])
        for path in (ROOT / "argocd/pro6000").glob("*.yaml"):
            self.assertTrue(checker.load(path)["spec"]["source"]["path"].endswith("/charts/vllm-runtime"))


if __name__ == "__main__":
    unittest.main()
