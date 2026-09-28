import copy
import importlib.util
import io
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


hf = module("hf")
bench = module("bench")
public = module("check_public")


class Manifests(unittest.TestCase):
    def setUp(self):
        self.site = hf.read_json(ROOT / "config/site.example.json")

    def bundle(self, stage):
        return hf.render(self.site, stage)["items"]

    def deploy(self, stage):
        return next(o for o in self.bundle(stage) if o["kind"] == "Deployment")

    def profile(self, stage):
        return json.loads(next(o for o in self.bundle(stage) if o["kind"] == "ConfigMap")["data"]["profile.json"])

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
        for stage in ("a", "b-tuned", "b-cache", "b-spec"):
            self.assertEqual(self.profile(stage)["max-model-len"], 98304)

    def test_baseline_not_deliberately_crippled(self):
        p = self.profile("a")
        self.assertNotIn("enforce-eager", p)
        self.assertNotIn("enable-prefix-caching", p)
        self.assertNotIn("enable-chunked-prefill", p)
        self.assertGreater(p["max-num-seqs"], 1)

    def test_no_weight_offload(self):
        for stage in hf.STAGES:
            self.assertNotIn("cpu-offload-gb", self.profile(stage))
        self.assertFalse(self.profile("tp2")["engram-config"]["cpu_offload"])

    def test_one_b_deployment_not_three(self):
        self.assertEqual({self.deploy(s)["metadata"]["name"] for s in ("b-tuned", "b-cache", "b-spec")}, {"hf-gemma-b"})

    def test_cache_budget_and_spec_are_separate(self):
        cached = self.profile("b-cache")
        self.assertEqual(cached["kv-transfer-config"]["kv_connector_extra_config"]["cpu_bytes_to_use"], 64 * 1024**3)
        self.assertNotIn("speculative-config", cached)
        self.assertNotIn("kv-transfer-config", self.profile("b-spec"))
        self.assertEqual(self.profile("b-spec")["speculative-config"]["method"], "mtp")

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
