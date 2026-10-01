import copy
import importlib.util
import io
import json
import pathlib
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


hf = module("hf")
bench = module("bench")
public = module("check_public")
workload = module("make_workload")
from workshop.manifests import memory_settings
from workshop.cli import main as cli_main
from workshop.lab import model_info


class WorkloadTokenizer(unittest.TestCase):
    def test_explicit_token_list_not_batch_encoding_key_count(self):
        tokenizer = Mock()
        tokenizer.apply_chat_template.side_effect = lambda *a, **kw: (
            [1, 2, 3, 4] if kw.get("return_dict") is False else
            {"input_ids": [1, 2, 3, 4], "attention_mask": [1, 1, 1, 1]})
        self.assertEqual(workload.chat_token_count(tokenizer, []), 4)

    def test_unexpected_template_return_fails_closed(self):
        for value in ({"input_ids": [1, 2]}, [[1, 2]], "text"):
            tokenizer = Mock()
            tokenizer.apply_chat_template.return_value = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                workload.chat_token_count(tokenizer, [])

    def test_remote_model_info_uses_python3(self):
        k = Mock()
        k.get.return_value = {"status": {"readyReplicas": 1}}
        model_info(k, "b-tuned")
        args = k.ns.call_args.args[0]
        self.assertEqual(args[args.index("--") + 1], "python3")


class Manifests(unittest.TestCase):
    def setUp(self):
        self.site = hf.read_json(ROOT / "config/site.example.json")

    def bundle(self, stage):
        return hf.render(self.site, stage)["items"]

    def deploy(self, stage):
        return next(o for o in self.bundle(stage) if o["kind"] == "Deployment")

    def profile(self, stage):
        return json.loads(next(o for o in self.bundle(stage) if o["kind"] == "ConfigMap")["data"]["profile.yaml"])

    def test_vllm_config_has_required_yaml_extension(self):
        for stage in hf.STAGES:
            container = self.deploy(stage)["spec"]["template"]["spec"]["containers"][0]
            self.assertEqual(container["command"],
                             ["vllm", "serve", "--config", "/etc/vllm/profile.yaml"])
            config = next(o for o in self.bundle(stage) if o["kind"] == "ConfigMap")
            self.assertEqual(set(config["data"]), {"profile.yaml"})
            self.assertIsInstance(json.loads(config["data"]["profile.yaml"]), dict)

    def test_all_stages_offline_and_zero_replicas(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("No cluster access")):
            for stage in hf.STAGES:
                self.assertEqual(self.deploy(stage)["spec"]["replicas"], 0)

    def test_scope_and_ownership(self):
        for stage in hf.STAGES:
            for o in self.bundle(stage):
                self.assertEqual(o["metadata"]["namespace"], "hardfest-demo")
                self.assertEqual(o["metadata"]["labels"]["app.kubernetes.io/part-of"], hf.OWNER)
                self.assertNotIn(o["kind"], {"Secret", "Namespace", "DeviceClass", "PersistentVolumeClaim", "GPUClass"})

    def test_same_ab_model_context_precision(self):
        for field in ("model", "max-model-len", "max-num-seqs", "dtype", "gpu-memory-utilization"):
            self.assertEqual(self.profile("a")[field], self.profile("b-tuned")[field])

    def test_site_context_applies_to_both(self):
        self.site["context_tokens"] = 98304
        for stage in ("a", "a-chunked", "b-tuned", "b-cache", "b-spec"):
            self.assertEqual(self.profile(stage)["max-model-len"], 98304)

    def test_baseline_explicitly_disables_selected_optimizations(self):
        p = self.profile("a")
        self.assertTrue(p["enforce-eager"])
        self.assertTrue(p["no-enable-prefix-caching"])
        self.assertTrue(p["no-enable-chunked-prefill"])
        self.assertNotIn("enable-prefix-caching", p)
        self.assertNotIn("enable-chunked-prefill", p)
        self.assertEqual(p["max-num-batched-tokens"], p["max-model-len"])
        self.assertGreater(p["max-num-seqs"], 1)

    def test_no_weight_offload(self):
        for stage in hf.STAGES:
            self.assertNotIn("cpu-offload-gb", self.profile(stage))
        self.assertFalse(self.profile("tp2")["engram-config"]["cpu_offload"])

    def test_chunked_baseline_is_separate_and_keeps_common_limits(self):
        p = self.profile("a-chunked")
        self.assertTrue(p["enforce-eager"])
        self.assertTrue(p["no-enable-prefix-caching"])
        self.assertTrue(p["enable-chunked-prefill"])
        self.assertNotIn("no-enable-chunked-prefill", p)
        self.assertEqual(p["kv-cache-dtype"], "auto")
        self.assertNotIn("kv-transfer-config", p)
        for field in ("model", "max-model-len", "max-num-seqs", "dtype",
                      "gpu-memory-utilization", "max-num-batched-tokens"):
            self.assertEqual(p[field], self.profile("b-tuned")[field])
        self.assertEqual(self.deploy("a-chunked")["metadata"]["name"],
                         self.deploy("a")["metadata"]["name"])

    def test_gemma_fp8_profiles_pin_hopper_compatible_backend(self):
        for stage in ("b-tuned", "b-cache", "b-spec"):
            with self.subTest(stage=stage):
                self.assertEqual(self.profile(stage)["kv-cache-dtype"], "fp8")
                self.assertEqual(self.profile(stage)["attention-backend"], "TRITON_ATTN")

    def test_one_b_deployment_not_three(self):
        self.assertEqual({self.deploy(s)["metadata"]["name"] for s in ("b-tuned", "b-cache", "b-spec")}, {"hf-gemma-b"})

    def test_cache_budget_and_spec_are_separate(self):
        cached = self.profile("b-cache")
        self.assertEqual(cached["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"], 64 * 1024**3)
        self.assertNotIn("speculative-config", cached)
        self.assertNotIn("kv-transfer-config", self.profile("b-spec"))
        self.assertEqual(self.profile("b-spec")["speculative-config"]["method"], "mtp")

    def test_cpu_kv_fits_shared_memory_without_double_counting(self):
        pod = self.deploy("b-cache")["spec"]["template"]["spec"]
        shm = next(v for v in pod["volumes"] if v["name"] == "shm")
        self.assertEqual(shm["emptyDir"], {"medium": "Memory", "sizeLimit": "72Gi"})
        r = pod["containers"][0]["resources"]
        self.assertEqual(r["requests"]["memory"], "128Gi")
        self.assertEqual(r["limits"]["memory"], "144Gi")

    def test_memory_tracks_offload_budget_and_rounds_up(self):
        p = self.profile("b-cache")
        p["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"] = 32 * 1024**3 + 1
        self.assertEqual(memory_settings("b-cache", p),
                         {"request": "97Gi", "limit": "113Gi", "shm": "41Gi"})

    def test_invalid_offload_budgets_fail_closed(self):
        for value in (None, True, 0, -1, 1.5, "68719476736"):
            p = self.profile("b-cache")
            p["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory_settings("b-cache", p)

    def test_gemma_pair_limits_and_loading_strategy(self):
        def memory_limit(stage):
            r = self.deploy(stage)["spec"]["template"]["spec"]["containers"][0]["resources"]
            return int(r["limits"]["memory"].removesuffix("Gi"))
        self.assertEqual(memory_limit("a") + memory_limit("b-cache"), 224)
        for stage in ("a", "b-tuned", "b-cache", "b-spec"):
            self.assertEqual(self.profile(stage)["safetensors-load-strategy"], "lazy")

    def test_other_stage_memory_budgets_unchanged(self):
        self.assertEqual(memory_settings("tp2", self.profile("tp2")),
                         {"request": "96Gi", "limit": "200Gi", "shm": "8Gi"})
        self.assertEqual(memory_settings("embed-mps", self.profile("embed-mps")),
                         {"request": "4Gi", "limit": "12Gi", "shm": "8Gi"})

    def test_small_host_resource_budget_is_explicit(self):
        self.site["resources"] = {"gemma": {
            "cpu_request": "4", "memory_request_gib": 24, "memory_limit_gib": 48}}
        self.site["kv_offload_gib"] = 32
        for stage in ("a", "b-tuned", "b-spec"):
            c = self.deploy(stage)["spec"]["template"]["spec"]["containers"][0]
            self.assertEqual(c["resources"]["requests"]["cpu"], "4")
            self.assertEqual(c["resources"]["requests"]["memory"], "24Gi")
            self.assertEqual(c["resources"]["limits"]["memory"], "48Gi")
        pod = self.deploy("b-cache")["spec"]["template"]["spec"]
        self.assertEqual(pod["containers"][0]["resources"]["limits"]["memory"], "80Gi")
        self.assertEqual(next(v for v in pod["volumes"] if v["name"] == "shm")["emptyDir"]["sizeLimit"], "40Gi")

    def test_mps_vram_budget_is_separate_from_thread_quota(self):
        self.site["mps_gpu_memory_utilization"] = 0.25
        self.assertEqual(self.profile("embed-mps")["gpu-memory-utilization"], 0.25)
        self.site["mps_percent"] = 50
        self.assertEqual(self.profile("embed-mps")["gpu-memory-utilization"], 0.25)
        self.assertEqual(self.profile("embed-mig")["gpu-memory-utilization"], 0.75)
        self.assertEqual(self.profile("b-tuned")["gpu-memory-utilization"], 0.9)
        for bad in (False, 0, -1, 1.1, "0.25", float("nan")):
            self.site["mps_gpu_memory_utilization"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.profile("embed-mps")

    def test_bad_site_resource_budgets_rejected(self):
        for budget in ({"memory_request_gib": 50, "memory_limit_gib": 48},
                       {"memory_request_gib": True}, {"cpu_request": "0"}):
            self.site["resources"] = {"gemma": budget}
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.deploy("a")
        self.site.pop("resources")
        for bad in (False, 0, -1, "32", 0.5):
            self.site["kv_offload_gib"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.deploy("b-cache")

    def test_read_only_model_mounts(self):
        for stage in hf.STAGES:
            pod = self.deploy(stage)["spec"]["template"]["spec"]
            self.assertFalse(pod["automountServiceAccountToken"])
            for m in pod["containers"][0]["volumeMounts"]:
                if m["mountPath"].startswith("/models/"):
                    self.assertTrue(m["readOnly"])
                    self.assertIn("subPath", m)

    def test_tp_claim_count(self):
        claim = next(o for o in self.bundle("tp2") if o["kind"] == "ResourceClaimTemplate")
        self.assertEqual(claim["spec"]["spec"]["devices"]["requests"][0]["exactly"]["count"], 2)

    def test_immutable_claim_spec_gets_new_name(self):
        def claim_name():
            return next(o for o in self.bundle("embed-mps") if o["kind"] == "ResourceClaimTemplate")["metadata"]["name"]
        before = claim_name()
        self.site["mps_percent"] = 50
        self.assertNotEqual(before, claim_name())

    def test_network_not_public(self):
        for stage in hf.STAGES:
            service = next(o for o in self.bundle(stage) if o["kind"] == "Service")
            self.assertEqual(service["spec"]["type"], "ClusterIP")
            self.assertTrue(any(o["kind"] == "NetworkPolicy" for o in self.bundle(stage)))

    def test_profiles_no_generated_private_data(self):
        self.assertEqual(list(public.problems()), [])


class Guards(unittest.TestCase):
    def setUp(self):
        self.site = hf.read_json(ROOT / "config/site.example.json")
        self.site.update(kubeconfig="/tmp/workshop-config", context="test", expected_server="https://cluster.example",
                         h100_node="gpu-node", h100_device_class="generated-h100")
        for model in self.site["models"].values():
            model.update(pvc="model-pvc", sub_path="store/model")

    def test_valid_h100_binding(self):
        hf.validate_site(self.site, "a")

    def test_proxy_bypass_is_local_to_child_process(self):
        self.site["bypass_proxy"] = True
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.invalid:8888", "KEEP_ME": "yes"}), \
             patch.object(subprocess, "run") as call:
            hf.Cluster(self.site).run(["get", "nodes"])
            env = call.call_args.kwargs["env"]
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertEqual(env["KEEP_ME"], "yes")
            import os
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://proxy.invalid:8888")

    def test_placeholder_fails_before_mutation(self):
        self.site["context"] = "REPLACE_CONTEXT"
        with self.assertRaises(ValueError):
            hf.validate_site(self.site, "a")

    def test_namespace_boundary(self):
        self.site["namespace"] = "default"
        with self.assertRaises(ValueError):
            hf.validate_site(self.site, "a")

    def test_model_path_traversal_rejected(self):
        self.site["models"]["gemma"]["sub_path"] = "../other"
        with self.assertRaises(ValueError):
            hf.validate_site(self.site, "a")

    def test_image_pin_required(self):
        self.site["image"] = "vllm/vllm-openai:latest"
        with self.assertRaises(ValueError):
            hf.validate_site(self.site, "a")

    def test_invalid_context_rejected(self):
        self.site["context_tokens"] = 0
        with self.assertRaises(ValueError):
            hf.validate_site(self.site, "a")

    def test_api_mismatch_stops_before_network_check(self):
        k = hf.Cluster(self.site)
        with patch.object(k, "run", return_value="https://other.example") as call:
            with self.assertRaises(ValueError):
                k.identity()
            self.assertEqual(call.call_count, 1)

    def test_foreign_owner_rejected(self):
        with self.assertRaises(ValueError):
            hf.Cluster(self.site).ownership({"metadata": {"labels": {}}})

    def test_cordon_rejected(self):
        k = hf.Cluster(self.site)
        node = {"spec": {"unschedulable": True}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        with patch.object(k, "run", return_value=json.dumps(node)):
            with self.assertRaises(ValueError):
                k.ready_node("a")

    def test_unverified_reranker_rejected(self):
        self.site.update(mig_node="mig-node", mps_device_class="generated-mps", mps_memory_limit="2Gi")
        with self.assertRaisesRegex(ValueError, "Reranker"):
            hf.validate_site(self.site, "rerank-mps")

    def test_start_rejects_stale_memory_even_with_matching_profile(self):
        for field in ("request", "limit", "shm", "mount", "medium"):
            deployment = next(o for o in hf.render(self.site, "b-cache")["items"]
                              if o["kind"] == "Deployment")
            pod = deployment["spec"]["template"]["spec"]
            if field in {"request", "limit"}:
                pod["containers"][0]["resources"][field + "s"]["memory"] = "224Gi"
            elif field == "mount":
                next(m for m in pod["containers"][0]["volumeMounts"]
                     if m["mountPath"] == "/dev/shm")["name"] = "runtime"
            else:
                shm = next(v for v in pod["volumes"] if v["name"] == "shm")["emptyDir"]
                shm["sizeLimit" if field == "shm" else "medium"] = "8Gi" if field == "shm" else ""
            k = Mock()
            k.get.return_value = deployment
            with self.subTest(field=field), patch("sys.argv", ["hf.py", "start", "b-cache", "--ack"]), \
                 patch("workshop.cli.read_json", return_value=self.site), \
                 patch("workshop.cli.Cluster", return_value=k):
                with self.assertRaisesRegex(ValueError, "Memory or /dev/shm"):
                    cli_main()
            k.ready_node.assert_not_called()
            k.ns.assert_not_called()

    def test_start_accepts_current_memory_layout(self):
        deployment = next(o for o in hf.render(self.site, "b-cache")["items"]
                          if o["kind"] == "Deployment")
        deployment["spec"]["replicas"] = 1
        deployment["status"] = {"readyReplicas": 1, "updatedReplicas": 1, "observedGeneration": 1}
        k = Mock()
        k.get.return_value = deployment
        k.ns.side_effect = [json.dumps({"items": []}), "scaled", None]
        with patch("sys.argv", ["hf.py", "start", "b-cache", "--ack"]), \
             patch("workshop.cli.read_json", return_value=self.site), \
             patch("workshop.cli.Cluster", return_value=k), patch("sys.stdout", new_callable=io.StringIO):
            cli_main()
        k.ready_node.assert_called_once_with("b-cache")
        k.ns.assert_any_call(["scale", "deployment/hf-gemma-b", "--replicas=1"])

    def test_start_does_not_report_stopped_rollout_as_ready(self):
        deployment = next(o for o in hf.render(self.site, "a")["items"]
                          if o["kind"] == "Deployment")
        k = Mock()
        k.get.return_value = deployment
        k.ns.side_effect = [json.dumps({"items": []}), "scaled", None]
        with patch("sys.argv", ["hf.py", "start", "a", "--ack"]), \
             patch("workshop.cli.read_json", return_value=self.site), \
             patch("workshop.cli.Cluster", return_value=k), patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(ValueError, "requested replica is not ready"):
                cli_main()

    def test_chunked_baseline_cannot_start_over_running_tp2(self):
        deployment = next(o for o in hf.render(self.site, "a-chunked")["items"]
                          if o["kind"] == "Deployment")
        k = Mock()
        k.get.return_value = deployment
        k.ns.return_value = json.dumps({"items": [
            {"metadata": {"name": "hf-qwen-tp2"}, "spec": {"replicas": 1}}]})
        with patch("sys.argv", ["hf.py", "start", "a-chunked", "--ack"]), \
             patch("workshop.cli.read_json", return_value=self.site), \
             patch("workshop.cli.Cluster", return_value=k):
            with self.assertRaisesRegex(ValueError, "occupies the H100s"):
                cli_main()
        k.ns.assert_called_once_with(["get", "deployments", "-o", "json"])

    def test_start_rejects_stale_model_node_or_cpu(self):
        for field in ("cpu", "node", "pvc", "subpath"):
            deployment = next(o for o in hf.render(self.site, "a")["items"] if o["kind"] == "Deployment")
            pod = deployment["spec"]["template"]["spec"]
            if field == "cpu":
                pod["containers"][0]["resources"]["requests"]["cpu"] = "1"
            elif field == "node":
                pod["nodeSelector"]["kubernetes.io/hostname"] = "old-node"
            elif field == "pvc":
                next(v for v in pod["volumes"] if v["name"] == "gemma")["persistentVolumeClaim"]["claimName"] = "old-pvc"
            else:
                next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "gemma")["subPath"] = "old/model"
            k = Mock()
            k.get.return_value = deployment
            with self.subTest(field=field), patch("sys.argv", ["hf.py", "start", "a", "--ack"]), \
                 patch("workshop.cli.read_json", return_value=self.site), \
                 patch("workshop.cli.Cluster", return_value=k):
                with self.assertRaisesRegex(ValueError, "binding changed"):
                    cli_main()
            k.ready_node.assert_not_called()
            k.ns.assert_not_called()


class Measurements(unittest.TestCase):
    def test_first_reasoning_and_usage_are_recorded(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                           b'data: {"choices":[{"delta":{"reasoning_content":"Let"}}]}\n\n'
                           b'data: {"choices":[{"delta":{"content":"Answer"},"finish_reason":"stop"}]}\n\n'
                           b'data: {"choices":[],"usage":{"completion_tokens":42}}\n\n'
                           b'data: [DONE]\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["ttft_s"])
        self.assertEqual(result["stream_chunks"], 2)
        self.assertEqual(result["usage"]["completion_tokens"], 42)
        self.assertGreaterEqual(result["first_content_s"], result["ttft_s"])
        self.assertEqual(result["content_preview"], "Answer")

    def test_fixed_output_is_opt_in_and_sent_to_vllm(self):
        for fixed in (False, True):
            stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n')
            with patch.object(bench.urllib.request, "urlopen", return_value=stream) as call:
                bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30, fixed)
            payload = json.loads(call.call_args.args[0].data)
            if fixed:
                self.assertTrue(payload["ignore_eos"])
                self.assertEqual(payload["min_tokens"], 128)
            else:
                self.assertNotIn("ignore_eos", payload)

    def test_truncated_stream_is_error(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIn("Incomplete", result["error"])

    def test_done_without_output_is_error(self):
        stream = io.BytesIO(b'data: [DONE]\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIn("no output", result["error"])

    def test_sse_multiline_and_heartbeat(self):
        stream = io.BytesIO(b': ping\n\ndata: {"a":\ndata: 1}\n\ndata: [DONE]\n\n')
        self.assertEqual(list(bench.events(stream)), ['{"a":\n1}', '[DONE]'])

    def test_percentile_not_mean(self):
        self.assertEqual(bench.percentile([0, 10], .95), 9.5)
        self.assertIsNone(bench.percentile([], .95))

    def test_chunks_not_counted_as_tokens(self):
        rows = [{"error": None, "ttft_s": 1., "e2e_s": 2., "usage": None, "stream_chunks": 999}]
        self.assertIsNone(bench.summarize(rows, 2)["output_tokens_per_s"])

    def test_actual_usage_counts_tokens(self):
        rows = [{"error": None, "ttft_s": 1., "e2e_s": 2., "usage": {"completion_tokens": 100}}]
        self.assertEqual(bench.summarize(rows, 2)["output_tokens_per_s"], 50)

    def test_failed_requests_not_counted_successful(self):
        r = bench.summarize([{"error": "failed", "ttft_s": None}], 1)
        self.assertEqual(r["errors"], 1)
        self.assertEqual(r["successful"], 0)
        self.assertIsNone(r["client_ttft_p95_s"])


if __name__ == "__main__":
    unittest.main()
