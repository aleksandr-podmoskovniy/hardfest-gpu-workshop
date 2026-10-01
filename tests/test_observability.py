import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Observability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = (ROOT / "observability/dashboard.yaml").read_text()
        cls.dashboard = json.loads(raw.split("  definition: |\n", 1)[1])
        cls.panels = {p["id"]: p for p in cls.dashboard["panels"]}
        cls.queries = [t["expr"] for p in cls.panels.values() for t in p.get("targets", [])]

    def test_services_are_not_collapsed_by_model_name(self):
        variables = {v["name"]: v for v in self.dashboard["templating"]["list"]}
        for name in ("namespace", "inference_service", "pod", "node"):
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
        self.assertIn("активными передачами", self.panels[211]["description"])
        self.assertIn("не пустой кэш", self.panels[211]["description"])
        self.assertIn("kv_offload_store_bytes_total", self.panels[212]["targets"][0]["expr"])
        self.assertIn("kv_offload_load_bytes_total", self.panels[213]["targets"][0]["expr"])
        self.assertIn("не атрибуция сервису", self.panels[104]["title"])
        for target in self.panels[221]["targets"]:
            self.assertIn("$gateway_namespace", target["expr"])
            self.assertNotIn("$inference_service", target["expr"])

    def test_named_service_ports_and_scoped_scrape_policy(self):
        paths = list((ROOT / "deploy").glob("*/service.yaml"))
        self.assertEqual(len(paths), 8)
        for path in paths:
            service = path.read_text()
            self.assertIn('name: "http"', service)
        monitor = (ROOT / "observability/monitoring.yaml").read_text()
        self.assertIn("port: http", monitor)
        self.assertIn("scrapeTimeout: 5s", monitor)
        self.assertIn("podSelector:", monitor)
        self.assertIn("prometheus: main", monitor)
        self.assertNotIn("podSelector: {}", monitor)
        app = (ROOT / "argocd/observability.yaml").read_text()
        self.assertNotIn("automated:", app)
        self.assertNotIn("finalizers:", app)
