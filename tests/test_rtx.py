import subprocess
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RTXProfiles(unittest.TestCase):
    def test_rehearsal_results_preserve_workload_counts_and_ram_evidence(self):
        report = json.loads((ROOT / "results/rtx5060-rehearsal-20261008.json").read_text())
        self.assertEqual(len(report["results"]), 19)
        self.assertEqual(sum(row["result"]["completed"] for row in report["results"]), 110)
        for row in report["results"]:
            result = row["result"]
            if row["label"] == "qwen-8-sessions":
                prompts, inputs, outputs, concurrency = 8, 32768, 512, 8
            elif row["label"].endswith("short") or row["label"].endswith("-4k"):
                prompts, inputs, outputs, concurrency = 8, 2048, 128, 2
            else:
                prompts, inputs, outputs, concurrency = 1, (57344 if row["label"].endswith("64k") else 122880), 512, 1
            self.assertEqual((result["completed"], result["failed"]), (prompts, 0))
            self.assertEqual(result["total_input_tokens"], prompts * inputs)
            self.assertEqual(result["total_output_tokens"], prompts * outputs)
            self.assertEqual(result["max_concurrency"], concurrency)
            self.assertAlmostEqual(result["output_throughput"], prompts * outputs / result["duration"])
        metrics = report["qwen_eight_sessions"]
        self.assertEqual(metrics["errors"], [])
        self.assertEqual(max(s["running"] for s in metrics["samples"]), 8)
        self.assertEqual(metrics["samples"][-1]["preemptions"] - metrics["samples"][0]["preemptions"], 0)
        replay = report["qwen_ram_replay"]
        self.assertTrue(replay["not_performance_comparison"])
        self.assertGreater(replay["delta"]["cpu_to_gpu_bytes"], 0)
        self.assertGreater(replay["delta"]["external_hits"], 0)
        self.assertIn("GPU-repeat", [r["label"] for r in replay["rows"]])
        self.assertEqual(replay["rows"][-1]["label"], "RAM-replay")

    def test_catalog_qwen_acceptance_does_not_claim_long_context(self):
        report = json.loads((ROOT / "results/rtx5060-qwen-nodecache-20261006.json").read_text())
        self.assertEqual(report["inference_service"]["model"]["src"], "ai-models")
        self.assertEqual(report["delivery"], "ai-models NodeCache")
        self.assertEqual(len({d["device"] for d in report["devices"]}), 2)
        args = report["pod"]["arguments"]
        self.assertEqual(args[args.index("--tensor-parallel-size") + 1], "2")
        self.assertEqual(args[args.index("--max-model-len") + 1], "8192")
        self.assertEqual(report["pod"]["restarts"], [0])
        self.assertEqual(len(report["results"]), 3)
        for row in report["results"]:
            result = row["result"]
            self.assertEqual((result["completed"], result["failed"]), (8, 0))
            self.assertEqual(result["total_input_tokens"], 16384)
            self.assertEqual(result["total_output_tokens"], 1024)
        self.assertEqual(report["webui"]["stream_http_status"], 200)
        self.assertTrue(report["webui"]["personal_virtual_key_reused"])
        self.assertTrue(report["webui"]["persisted_after_reload"])

    def test_long_context_results_match_actual_load_and_reservation(self):
        report = json.loads((ROOT / "results/rtx5060-long-20261006.json").read_text())
        self.assertEqual(report["profile"]["changes"]["gpu-memory-utilization"], 0.95)
        self.assertEqual(len(report["runs"]), 4)
        for run in report["runs"]:
            result = run["result"]
            count = run["max_concurrency"]
            length = 57344 if run["max_model_len"] == 65536 else 122880
            self.assertEqual((result["completed"], result["failed"]), (count, 0))
            self.assertEqual(result["total_input_tokens"], count * length)
            self.assertEqual(result["total_output_tokens"], count * 512)
            self.assertAlmostEqual(result["output_throughput"], count * 512 / result["duration"])
        self.assertIn("gpu-memory-utilization: 0.95", (ROOT / "RTX5060.md").read_text())
        ui = report["webui"]
        self.assertEqual(ui["answer"]["usage"]["prompt_tokens"], 120619)
        self.assertEqual(ui["answer"]["content"], ui["expected"])
        self.assertEqual(ui["request"]["files"], 0)

    def test_nodecache_results_and_diagnostic_are_separate(self):
        report = json.loads((ROOT / "results/rtx5060-nodecache-20261006.json").read_text())
        self.assertEqual(report["delivery"]["backend"], "ai-models NodeCache")
        self.assertEqual(report["load"]["num_warmups"], 0)
        for profile in report["profiles"]:
            self.assertEqual(profile["gpu_memory_utilization"], 0.9)
            self.assertEqual(profile["max_model_len"], 4096)
            self.assertEqual(len(profile["results"]), 3)
            for result in profile["results"]:
                self.assertEqual((result["completed"], result["failed"]), (8, 0))
                self.assertEqual(result["total_input_tokens"], 16384)
                self.assertEqual(result["total_output_tokens"], 1024)
                self.assertAlmostEqual(result["output_throughput"], 1024 / result["duration"])
        diagnostic = json.loads((ROOT / "results/rtx5060-offload-20261006.json").read_text())
        self.assertTrue(diagnostic["not_performance_comparison"])
        self.assertIn("268435456", diagnostic["profile"])
        self.assertIn('transfer_type="CPU_to_GPU"} 0.0', diagnostic["before_replay"])
        self.assertIn('transfer_type="CPU_to_GPU"} 1.0518528e+07', diagnostic["after_replay"])

    def test_distroless_benchmark_results_are_returned_on_stdout(self):
        for name in ("RTX5060.md", "README.md"):
            text = (ROOT / name).read_text()
            self.assertIn("--result-dir /dev --result-filename stdout", text)
            self.assertIn("--num-warmups 0", text)
        memory = (ROOT / "docs/MEMORY_BUDGET.md").read_text()
        self.assertNotIn("-- cat", memory)
        self.assertIn('get configmap "$PROFILE_CONFIGMAP"', memory)

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
            "--set", "nodeSelector.kubernetes\\.io/hostname=rtx-node"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("replace site placeholders in image", result.stderr)

    def test_argo_apps_are_explicit_and_not_autosynced(self):
        apps = list((ROOT / "argocd/rtx").glob("*.yaml"))
        self.assertEqual(len(apps), 6)
        for path in apps:
            spec = yaml.safe_load(path.read_text())["spec"]
            self.assertNotIn("automated", spec["syncPolicy"])
            self.assertEqual(spec["destination"]["namespace"], "hardfest-rtx")
            self.assertEqual(len(spec["source"]["helm"]["valueFiles"]), 1)

    def test_cache_and_tune_have_distinct_gitops_owners(self):
        names = set()
        for stage, filename in (("cache", "gemma-cache"), ("tune", "gemma-spec")):
            app = yaml.safe_load((ROOT / "argocd/rtx" / f"rtx-gemma-{stage}.yaml").read_text())
            profile = yaml.safe_load((ROOT / "values/rtx" / f"{filename}.yaml").read_text())
            expected = f"rtx-gemma-{stage}"
            self.assertEqual(app["metadata"]["name"], expected)
            self.assertEqual(app["spec"]["source"]["helm"]["releaseName"], expected)
            self.assertEqual(app["spec"]["source"]["helm"]["valueFiles"], [f"../../values/{filename}.yaml"])
            self.assertIn("FailOnSharedResource=true", app["spec"]["syncPolicy"]["syncOptions"])
            self.assertEqual(profile["fullnameOverride"], expected)
            self.assertEqual(profile["vllm"]["served-model-name"], expected)
            self.assertEqual(profile["replicaCount"], 0)
            names.add(profile["fullnameOverride"])
        self.assertEqual(len(names), 2)

    def test_same_weights_and_increasing_mechanisms(self):
        profiles = [yaml.safe_load((ROOT / "values/rtx" / (name + ".yaml")).read_text())
                    for name in ("gemma-base", "gemma-cache", "gemma-spec")]
        for profile in profiles:
            self.assertEqual(profile["replicaCount"], 0)
            self.assertEqual(profile["dra"]["count"], 1)
            self.assertEqual(profile["vllm"]["max-model-len"], 4096)
            self.assertEqual(profile["vllm"]["dtype"], "bfloat16")
            self.assertEqual(profile["vllm"]["model"], "/data/modelcache/models/rtx-gemma-e2b")
            self.assertEqual(profile["modelVolumes"], [])
            self.assertIn("rtx-gemma-e2b", profile["modelRefs"])
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
        self.assertIn("rtx-gemma-e2b-assistant", profiles[2]["modelRefs"])
        self.assertEqual(spec["speculative-config"]["model"],
                         "/data/modelcache/models/rtx-gemma-e2b-assistant")

    def test_catalog_orders_are_disabled_by_default_and_render_when_bound(self):
        for name, ref in (("rtx-gemma", "rtx-gemma-e2b"),
                          ("rtx-qwen", "rtx-qwen35-9b")):
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
            self.assertEqual(model, {"src": "ai-models", "ref": {"kind": "Model", "name": ref}})

    def test_rtx_catalog_is_pinned_and_matches_consumers(self):
        result = subprocess.run(["helm", "template", "rtx-models", str(ROOT / "charts/model-catalog"),
            "-n", "hardfest-rtx", "-f", str(ROOT / "catalog/rtx.yaml")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        models = {d["metadata"]["name"]: d for d in yaml.safe_load_all(result.stdout)}
        self.assertEqual(set(models), {"rtx-gemma-e2b", "rtx-gemma-e2b-assistant", "rtx-qwen35-9b"})
        for model in models.values():
            self.assertEqual(model["kind"], "Model")
            self.assertEqual(model["metadata"]["namespace"], "hardfest-rtx")
            self.assertRegex(model["spec"]["source"]["url"], r"/tree/[a-f0-9]{40}$")
        app = yaml.safe_load((ROOT / "argocd/rtx/rtx-models.yaml").read_text())["spec"]["source"]
        self.assertTrue(app["path"].endswith("charts/model-catalog"))
        self.assertEqual(app["helm"]["valueFiles"], ["../../catalog/rtx.yaml"])

    def test_manual_profiles_render_ai_models_delivery_annotations(self):
        for name, expected in (("gemma-base", "rtx-gemma-e2b"), ("gemma-cache", "rtx-gemma-e2b"),
                               ("gemma-spec", "rtx-gemma-e2b,rtx-gemma-e2b-assistant")):
            result = subprocess.run(["helm", "template", "rtx", str(ROOT / "charts/vllm-runtime"),
                "-f", str(ROOT / "values/rtx" / (name + ".yaml"))], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            deployment = next(d for d in yaml.safe_load_all(result.stdout) if d["kind"] == "Deployment")
            self.assertEqual(deployment["metadata"]["annotations"]["ai.deckhouse.io/model"], expected)

    def test_full_rtx_guide_has_ordered_stages_and_inline_commands(self):
        guide = (ROOT / "RTX5060.md").read_text()
        self.assertIn("(RTX5060.md)", (ROOT / "README.md").read_text())
        stages = ["ab", "monitoring", "memory", "ram", "speculation", "latency",
                  "placement", "platform", "conclusion", "tp2", "cleanup", "setup"]
        positions = [guide.index(f'id="{name}"') for name in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(guide.index('id="tp2"'), guide.index('id="results"'))
        self.assertLess(guide.index('id="results"'), guide.index('id="cleanup"'))
        for target in re.findall(r"\]\(#([^)]+)\)", guide):
            self.assertIn(f'<a id="{target}"></a>', guide)
        self.assertNotIn("labs/", guide)
        self.assertGreaterEqual(len(re.findall(r"!\[.+?\]\(assets/", guide)), 10)
        for term in ("ai-models", "InferenceService", "16 GiB", "assistant", "TP2", "MTP",
                     "NodeCache"):
            self.assertIn(term.lower(), guide.lower())
        self.assertNotIn("·", guide)

    def test_participant_guides_do_not_contain_preparation_status(self):
        for filename in ("RTX5060.md", "README.md"):
            text = (ROOT / filename).read_text()
            for fragment in ("Состояние проверки на", "условия полного показа",
                             "во время выступления", "графики проверены визуально",
                             "Установленная сборка пока", "В прогоне 6 октября",
                             "не бонус после показа"):
                self.assertNotIn(fragment, text, (filename, fragment))
        guide = (ROOT / "RTX5060.md").read_text()
        self.assertIn('<a id="results"></a>', guide)
        self.assertIn("rtx5060-nodecache-20261006.json", guide)
        self.assertIn("общий лимит расходов", guide)

    def test_catalog_chat_routing_uses_served_model_names(self):
        preparation = (ROOT / "docs/SETUP.md").read_text().split('id="rtx"', 1)[1]
        for service, served in (("rtx-gemma-platform", "rtx-gemma-e2b"),
                                ("rtx-qwen35-tp2", "rtx-qwen35-9b")):
            row = next(line for line in preparation.splitlines() if f"`{service}:80`" in line)
            self.assertIn(f"`vllm/{service}`", row)
            self.assertTrue(row.endswith(f"`{served}` |"))
        guide = (ROOT / "RTX5060.md").read_text()
        self.assertIn("--model rtx-qwen35-9b", guide)
        self.assertIn("рецепт не меняется", guide)
        self.assertIn("simple_kv_offload_load_blocks_total", guide)
