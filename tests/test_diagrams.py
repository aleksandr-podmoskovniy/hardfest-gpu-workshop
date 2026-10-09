import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_diagrams import Box, Diagram

SVG = "{http://www.w3.org/2000/svg}"


class DiagramConnections(unittest.TestCase):
    def test_tp2_explains_its_exchange_library_without_an_unrelated_mode(self):
        for name in ("10-tp2", "24-rtx-tp2"):
            with self.subTest(diagram=name):
                text = " ".join(ET.parse(ROOT / f"assets/{name}.svg").getroot().itertext())
                self.assertIn("NCCL (NVIDIA Collective Communications Library)", text)
                self.assertNotIn("PP делит", text)

    def test_shared_design_and_readable_footnotes(self):
        for path in (ROOT / "assets").glob("[0-9][0-9]-*.svg"):
            with self.subTest(file=path.name):
                root = ET.parse(path).getroot()
                self.assertEqual(root.attrib["data-design"], "hardfest-v2")
                self.assertEqual(root.attrib["viewBox"], "0 0 1200 760")
                self.assertTrue(root.find(SVG + "title").text)
                self.assertTrue(root.find(SVG + "desc").text)
                footer = list(root.iter(SVG + "text"))[-1]
                self.assertEqual(footer.attrib["font-size"], "20")
                self.assertEqual(footer.attrib["data-max-width"], "1104")

    def test_a30_stays_with_webui_and_includes_whisper(self):
        root = ET.parse(ROOT / "assets" / "01-topology.svg").getroot()
        nodes = {" ".join(n.itertext()).strip(): n for n in root.iter(SVG + "text")}
        self.assertIn("КЛАСТЕР WEBUI + A30", nodes)
        self.assertIn("КЛАСТЕР H100 + ШЛЮЗ", nodes)
        self.assertLess(float(nodes["A30 / 2 × 2g.12gb"].attrib["x"]),
                        float(nodes["ai-mcp-gateway"].attrib["x"]))
        self.assertIn("Whisper large-v3: отдельно", " ".join(root.itertext()))

    def test_a30_target_has_two_partitions_and_three_services(self):
        root = ET.parse(ROOT / "assets" / "08-mig-mps.svg").getroot()
        text = " ".join(root.itertext())
        for label in ("Эмбеддер 4B", "Реранкер 4B", "Whisper large-v3",
                      "Multi-Instance GPU", "Multi-Process Service"):
            self.assertIn(label, text)
        # This image explains placement before the automation chapter.
        self.assertNotIn("InferenceService", text)
        self.assertNotIn("GPUClass", text)
        self.assertEqual(text.count("2g.12gb"), 2)
        self.assertNotIn("1g.6gb", text)

    def test_rtx_diagrams_do_not_inherit_h100_memory_or_interconnect(self):
        tp2 = " ".join(ET.parse(ROOT / "assets/24-rtx-tp2.svg").getroot().itertext())
        self.assertIn("RTX 5060 Ti / rank 0", tp2)
        self.assertIn("RTX 5060 Ti / rank 1", tp2)
        for wrong in ("NVLink", "H100", "HBM"):
            self.assertNotIn(wrong, tp2)

    def test_rtx_window_and_concurrency_are_distinct_in_the_recorded_profile(self):
        profile = json.loads((ROOT / "results/rtx5060-rehearsal-20261008.json").read_text())
        self.assertEqual(profile["profiles"]["qwen-128k"]["max_model_len"], 131072)
        self.assertEqual(profile["profiles"]["qwen-128k"]["max_num_seqs"], 8)
        self.assertEqual(profile["profiles"]["qwen-128k"]["kv_offloading_gib"], 16)

    def test_topology_does_not_mix_admin_control_with_inference(self):
        for name in ("01-topology",):
            root = ET.parse(ROOT / f"assets/{name}.svg").getroot()
            text = " ".join(root.itertext())
            self.assertNotIn("Kubernetes MCP", text)
            self.assertIn("Синий маршрут — чат", text)
            self.assertIn("Финал: Qwen вместо двух Gemma", text)
            self.assertIn("ai-mcp-gateway", text)

    def test_latency_defines_time_to_first_token_and_generation(self):
        text = " ".join(ET.parse(ROOT / "assets/02-latency.svg").getroot().itertext())
        self.assertIn("TTFT — до первого токена", text)
        self.assertIn("DECODE — ГЕНЕРАЦИЯ ТОКЕНОВ", text)
        self.assertIn("Рассуждение есть не у всех моделей", text)

    def test_main_kv_example_uses_long_context_not_local_attention_window(self):
        h100 = " ".join(ET.parse(ROOT / "assets/11-gemma-kv.svg").getroot().itertext())
        self.assertIn("Контекст S — 128K или 256K", h100)
        self.assertIn("1024 — окно только локальных слоёв", h100)

    def test_ports_follow_card_bounds_with_equal_clearance(self):
        card = Box(40, 80, 240, 120)
        self.assertEqual(card.port("left"), (34, 140))
        self.assertEqual(card.port("right"), (286, 140))
        self.assertEqual(card.port("top"), (160, 74))
        self.assertEqual(card.port("bottom"), (160, 206))
        self.assertEqual(card.port("right", .75), (286, 170))

    def test_connections_track_moved_cards(self):
        diagram = Diagram("test", "Test", "Test")
        diagram.connect(Box(40, 80, 240, 120), Box(340, 80, 240, 120))
        path = ET.fromstring(diagram.parts[-1])
        self.assertEqual(path.attrib["d"], "M286 140.0 L334 140.0")
        self.assertEqual(path.attrib["data-connector"], "true")

    def test_elbows_are_rounded_without_changing_endpoints(self):
        diagram = Diagram("test", "Test", "Test")
        diagram.connector([(100, 200), (100, 240), (180, 240), (180, 280)])
        route = ET.fromstring(diagram.parts[-1]).attrib["d"]
        self.assertTrue(route.startswith("M100 200"))
        self.assertTrue(route.endswith("L180 280"))
        self.assertEqual(route.count(" Q"), 2)

    def test_refuses_diagonals_zero_segments_and_arrow_stubs(self):
        diagram = Diagram("test", "Test", "Test")
        for points in ([], [(0, 0)], [(0, 0), (0, 0)], [(0, 0), (30, 40)],
                       [(0, 0), (16, 0)], [(0, 0), (0, 40), (8, 40)]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                diagram.connector(points)

    def test_all_arrowheads_have_fixed_size_and_explicit_colour(self):
        for path in (ROOT / "assets").glob("[0-9][0-9]-*.svg"):
            root = ET.parse(path).getroot()
            markers = {node.attrib["id"]: node for node in root.iter(SVG + "marker")}
            for node in root.iter(SVG + "path"):
                if "marker-end" not in node.attrib:
                    continue
                with self.subTest(file=path.name, route=node.attrib["d"]):
                    self.assertEqual(node.attrib.get("data-connector"), "true")
                    marker_id = node.attrib["marker-end"].removeprefix("url(#").removesuffix(")")
                    marker = markers[marker_id]
                    self.assertEqual(marker.attrib["markerUnits"], "userSpaceOnUse")
                    self.assertEqual(marker.attrib["markerWidth"], "10")
                    self.assertEqual(marker.attrib["markerHeight"], "10")
                    self.assertEqual(marker.find(SVG + "path").attrib["fill"], node.attrib["stroke"])


if __name__ == "__main__":
    unittest.main()
