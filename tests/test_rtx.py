import subprocess
import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RTXProfiles(unittest.TestCase):
    def test_measured_raw_files_and_image_are_explicit(self):
        summary = json.loads((ROOT / "results/rtx5060-gemma-20261006.json").read_text())
        for row in summary["base"] + summary["tune"]:
            raw = json.loads((ROOT / "results" / row["file"]).read_text())
            self.assertEqual(row["output_throughput"], raw["output_throughput"])
        for name in ("gemma-base", "gemma-cache", "gemma-spec"):
            profile = yaml.safe_load((ROOT / "values/rtx" / (name + ".yaml")).read_text())
            self.assertEqual(profile["image"], "REPLACE_RUNTIME_IMAGE@sha256:"
                "f7cb9820c7e92eb78a4b3f60033e240c87a16bf99117471aba347fdd69eaca2d")

    def test_running_profile_refuses_placeholder_image(self):
        result = subprocess.run(["helm", "template", "rtx", str(ROOT / "charts/vllm-runtime"),
            "-f", str(ROOT / "values/rtx/gemma-base.yaml"),
            "--set", "replicaCount=1", "--set", "dra.deviceClassName=rtx",
            "--set", "nodeSelector.kubernetes\\.io/hostname=rtx-node",
            "--set", "modelVolumes[0].claimName=gemma"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("replace site placeholders in image", result.stderr)

    def test_argo_apps_are_explicit_and_not_autosynced(self):
        apps = list((ROOT / "argocd/rtx").glob("*.yaml"))
        self.assertEqual(len(apps), 5)
        for path in apps:
            spec = yaml.safe_load(path.read_text())["spec"]
            self.assertNotIn("automated", spec["syncPolicy"])
            self.assertEqual(spec["destination"]["namespace"], "hardfest-rtx")
            self.assertEqual(len(spec["source"]["helm"]["valueFiles"]), 1)

    def test_same_weights_and_increasing_mechanisms(self):
        profiles = [yaml.safe_load((ROOT / "values/rtx" / (name + ".yaml")).read_text())
                    for name in ("gemma-base", "gemma-cache", "gemma-spec")]
        for profile in profiles:
            self.assertEqual(profile["replicaCount"], 0)
            self.assertEqual(profile["dra"]["count"], 1)
            self.assertEqual(profile["vllm"]["max-model-len"], 4096)
            self.assertEqual(profile["vllm"]["dtype"], "bfloat16")
            self.assertEqual(profile["vllm"]["model"], "/models/model")
        a, cache, spec = [p["vllm"] for p in profiles]
        for flag in ("enable-prefix-caching", "enable-chunked-prefill"):
            self.assertTrue(a['no-' + flag])
        self.assertTrue(a["enforce-eager"])
        self.assertNotIn("speculative-config", a)
        self.assertNotIn("kv-offloading-size", a)
        self.assertTrue(cache["no-enable-chunked-prefill"])
        for p in (cache, spec):
            self.assertTrue(p["enable-prefix-caching"])
            self.assertEqual(p["kv-offloading-size"], 4)
            self.assertEqual(p["kv-cache-dtype"], "fp8")
        self.assertTrue(spec["enable-chunked-prefill"])
        self.assertFalse(spec["enforce-eager"])
        self.assertEqual(spec["speculative-config"]["method"], "mtp")
        self.assertEqual(profiles[2]["modelVolumes"][1]["subPath"],
                         ".assistant/2d874ef7d29f9a30599a1e4b3c1cbc9595f005df")

    def test_hf_orders_are_disabled_by_default_and_render_when_bound(self):
        for name, repo in (("rtx-gemma", "google/gemma-4-E2B-it"),
                           ("rtx-qwen", "Qwen/Qwen3.5-9B")):
            command = ["helm", "template", name, str(ROOT / "charts/inference-service"),
                       "-n", "rtx", "-f", str(ROOT / "platform" / (name + ".yaml"))]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
            result = subprocess.run(command + ["--set", "order.enabled=true",
                "--set", "order.spec.inferenceServiceClassName=rtx",
                "--set", "order.spec.resources.accelerator.deviceClasses[0]=rtx"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            model = yaml.safe_load(result.stdout)["spec"]["model"]
            self.assertEqual(model, {"src": "HuggingFace", "ref": {"name": repo}})

    def test_preload_is_inert_and_refuses_unbound_images(self):
        command = ["helm", "template", "rtx", str(ROOT / "charts/model-preload"),
                   "-f", str(ROOT / "values/rtx/models.yaml")]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        result = subprocess.run(command + ["--set", "enabled=true"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set model-fetch image digest", result.stderr)

    def preload(self, *extra):
        return subprocess.run([
            "helm", "template", "rtx", str(ROOT / "charts/model-preload"),
            "-f", str(ROOT / "values/rtx/models.yaml"), "--set", "enabled=true",
            "--set", "image=registry.example/model-fetch@sha256:" + "a" * 64,
            "--set", "storageClassName=test-storage",
            "--set", "nodeSelector.kubernetes\\.io/hostname=rtx-node", *extra,
        ], capture_output=True, text=True)

    def test_preload_keeps_claims_and_changes_only_job_names(self):
        generations = []
        for generation in ("v1", "v2"):
            result = self.preload("--set", "generation=" + generation)
            self.assertEqual(result.returncode, 0, result.stderr)
            docs = list(yaml.safe_load_all(result.stdout))
            claims = [d for d in docs if d["kind"] == "PersistentVolumeClaim"]
            jobs = [d for d in docs if d["kind"] == "Job"]
            self.assertEqual((len(claims), len(jobs)), (2, 2))
            for claim in claims:
                annotations = claim["metadata"]["annotations"]
                self.assertEqual(annotations["helm.sh/resource-policy"], "keep")
                self.assertEqual(annotations["argocd.argoproj.io/sync-options"],
                                 "Prune=false,Delete=false")
            for job in jobs:
                pod = job["spec"]["template"]["spec"]
                self.assertNotIn("resourceClaims", pod)
                for container in pod["containers"]:
                    for group in ("requests", "limits"):
                        self.assertEqual(set(container["resources"][group]),
                                         {"cpu", "memory"})
                self.assertTrue(job["metadata"]["name"].endswith("-" + generation))
                job["metadata"]["name"] = job["metadata"]["name"].rsplit("-", 1)[0]
            generations.append((claims, jobs))
        self.assertEqual(generations[0], generations[1])

    def test_preload_rejects_concurrent_delivery_to_same_claim(self):
        result = self.preload("--set",
                             "models[1].claimName=storage-rtx-gemma-platform-0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("each model must have a distinct claimName", result.stderr)
