import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_manifests import render


class Observability(unittest.TestCase):
    def test_native_connector_directions_and_units_are_preserved(self):
        text = (ROOT / "observability/dashboard.yaml").read_text()
        self.assertIn("vllm:kv_offload_total_bytes_total", text)
        self.assertIn("sum by(namespace,service,transfer_type)", text)
        self.assertIn('"unit": "Bps"', text)
        self.assertIn('"unit": "bytes"', text)
        self.assertIn("CPU_to_GPU", text)
        self.assertIn("vllm:kv_offload_cpu_cache_usage_perc", text)

    @classmethod
    def setUpClass(cls):
        raw = (ROOT / "observability/dashboard.yaml").read_text()
        cls.dashboard = json.loads(raw.split("  definition: |\n", 1)[1])
        cls.panels = {p["id"]: p for p in cls.dashboard["panels"]}
        cls.queries = [t["expr"] for p in cls.panels.values() for t in p.get("targets", [])]

    def test_services_are_not_collapsed_by_model_name(self):
        variables = {v["name"]: v for v in self.dashboard["templating"]["list"]}
        for name in ("namespace", "inference_service", "model", "pod", "node"):
            self.assertEqual(variables[name]["type"], "query")
            self.assertTrue(variables[name]["includeAll"])
        for query in self.queries:
            self.assertNotIn("ai_inference_platform_", query)
            self.assertNotIn('namespace="ai-demo', query)
            self.assertNotIn("on(model_name)", query)
            self.assertNotIn("or vector(0)", query)
        self.assertIn("namespace, service", self.panels[12]["targets"][0]["expr"])

    def test_queue_prefill_decode_means_are_not_percentiles(self):
        queries = [t["expr"] for t in self.panels[201]["targets"]]
        self.assertEqual(len(queries), 3)
        for query, phase in zip(queries, ("queue", "prefill", "decode")):
            self.assertIn("request_" + phase + "_time_seconds_sum", query)
            self.assertIn("_count", query)
            self.assertIn("> 0", query)
            self.assertNotIn("histogram_quantile", query)
        self.assertIn("histogram_quantile", self.panels[202]["targets"][0]["expr"])

    def test_offload_semantics_and_node_scope_are_explicit(self):
        self.assertIn("pending_store_blocks", self.panels[244]["targets"][0]["expr"])
        self.assertIn("capacity_blocks", self.panels[245]["targets"][0]["expr"])
        for query in self.queries:
            self.assertNotIn("kv_offload_store_bytes_total", query)
            self.assertNotIn("kv_offload_load_bytes_total", query)
        self.assertIn("не атрибуция сервису", self.panels[104]["title"])
        for target in self.panels[221]["targets"]:
            self.assertIn("$gateway_namespace", target["expr"])
            self.assertNotIn("$inference_service", target["expr"])

    def test_named_service_ports(self):
        paths = list((ROOT / "values").glob("*.yaml"))
        self.assertEqual(len(paths), 4)
        for path in paths:
            self.assertEqual(render(path)["Service"]["spec"]["ports"][0]["name"], "http")

    def test_simple_cpu_offload_uses_block_metrics_without_faking_byte_counts(self):
        for panel_id, metric in ((241, "simple_kv_offload_load_blocks_total"),
                                 (242, "simple_kv_offload_used_blocks"),
                                 (243, "simple_kv_offload_save_outcomes_total")):
            panel = self.panels[panel_id]
            self.assertIn(metric, panel["targets"][0]["expr"])
            self.assertNotIn("bytes", panel["fieldConfig"]["defaults"]["unit"])
            self.assertIn("$inference_service", panel["targets"][0]["expr"])
        self.assertIn("ноль не означает пустой", self.panels[242]["description"])
        self.assertIn("outcome", self.panels[243]["targets"][0]["expr"])

    def test_scoped_monitoring_policy(self):
        monitor = (ROOT / "observability/monitoring.yaml").read_text()
        self.assertIn("port: http", monitor)
        self.assertIn("scrapeTimeout: 5s", monitor)
        self.assertIn("podSelector:", monitor)
        self.assertIn("prometheus: main", monitor)
        self.assertNotIn("podSelector: {}", monitor)
        app = (ROOT / "argocd/observability.yaml").read_text()
        self.assertNotIn("automated:", app)
        self.assertNotIn("finalizers:", app)

    def test_inventory_survives_missing_runtime_metrics(self):
        variables = {v["name"]: v for v in self.dashboard["templating"]["list"]}
        for name in ("namespace", "inference_service", "pod"):
            self.assertIn("kube_pod_labels", variables[name]["definition"])
            self.assertIn("label_ai_inference_deckhouse_io_managed", variables[name]["definition"])
        inventory = self.panels[1]
        self.assertIn("kube_pod_labels", inventory["targets"][0]["expr"])
        self.assertIn("-1 *", inventory["targets"][0]["expr"])
        mapping = inventory["fieldConfig"]["defaults"]["mappings"][0]["options"]
        self.assertEqual(mapping["-1"]["text"], "NO SCRAPE")
        self.assertEqual(mapping["-1"]["color"], "gray")

    def test_gpu_diagnostics_do_not_depend_on_runtime_health(self):
        for panel_id in (27, 28, 29, 30, 31, 32, 231):
            for target in self.panels[panel_id]["targets"]:
                self.assertNotIn("cache_config_info", target["expr"])
                self.assertIn("$node", target["expr"])
        for target in self.panels[31]["targets"]:
            self.assertIn("GPU_I_ID", target["expr"])
            self.assertIn("GPU_I_PROFILE", target["expr"])
        self.assertIn("DCGM_FI_PROF_GR_ENGINE_ACTIVE", self.panels[31]["targets"][2]["expr"])
        self.assertIn("не счётчик ECC", self.panels[231]["description"])

    def test_gateway_is_discoverable_when_only_errors_exist(self):
        variables = {v["name"]: v for v in self.dashboard["templating"]["list"]}
        for name in ("gateway_namespace", "gateway_model"):
            self.assertIn("error_requests_total", variables[name]["definition"])
            self.assertIn("upstream_requests_total", variables[name]["definition"])

    def test_dashboard_is_shared_across_clusters_and_model_families(self):
        self.assertEqual(self.dashboard["title"], "AI Inference / Service performance")
        for query in self.queries:
            for site in ("hardfest", "h100-pair", "hf-qwen", "hf-embedding", "192.168."):
                self.assertNotIn(site, query)
        variables = {v["name"]: v for v in self.dashboard["templating"]["list"]}
        self.assertIn("num_requests_running", variables["model"]["definition"])
        self.assertIn('model_name=~"$model"', self.panels[12]["targets"][0]["expr"])
        config_query = self.panels[37]["targets"][0]["expr"]
        self.assertIn("and on(namespace,service,pod,engine)", config_query)
        cache_selector = config_query.split("cache_config_info{", 1)[1].split("}", 1)[0]
        self.assertNotIn("model_name", cache_selector)

    def test_speculative_panels_measure_tokens_not_speedup(self):
        query = self.panels[252]["targets"][0]["expr"]
        self.assertIn("spec_decode_num_accepted_tokens_total", query)
        self.assertIn("spec_decode_num_draft_tokens_total", query)
        self.assertIn("> 0", query)
        self.assertNotIn("speedup", self.panels[252]["title"].lower())

    def test_latency_filters_include_selected_runtime_pod(self):
        for panel_id in (9, 10, 13, 17, 19, 201, 202, 203, 204):
            for target in self.panels[panel_id]["targets"]:
                self.assertIn('pod=~"$pod"', target["expr"])

    def test_panels_have_unique_ids_and_do_not_overlap(self):
        self.assertEqual(len(self.panels), len(self.dashboard["panels"]))
        panels = list(self.panels.values())
        for i, a in enumerate(panels):
            x = a["gridPos"]
            for b in panels[i + 1:]:
                y = b["gridPos"]
                overlaps = (x["x"] < y["x"] + y["w"] and y["x"] < x["x"] + x["w"]
                            and x["y"] < y["y"] + y["h"] and y["y"] < x["y"] + x["h"])
                self.assertFalse(overlaps, (a["id"], b["id"]))
