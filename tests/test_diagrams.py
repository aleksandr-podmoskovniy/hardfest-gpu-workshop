import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_diagrams import Box, Diagram
from kv_math import calculate_gemma, calculate_gemma_e2b

SVG = "{http://www.w3.org/2000/svg}"


class DiagramConnections(unittest.TestCase):
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

    def test_qwen_gpu_gate_comes_before_launch(self):
        root = ET.parse(ROOT / "assets" / "17-qwen-transition.svg").getroot()
        text = " ".join(root.itertext())
        self.assertLess(text.index("обе H100 свободны"), text.index("Рецепт Qwen"))

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
        for label in ("Эмбеддер 4B", "Реранкер 4B", "Whisper large-v3", "три InferenceService"):
            self.assertIn(label, text)
        self.assertEqual(text.count("2g.12gb"), 2)
        self.assertNotIn("1g.6gb", text)

    def test_rtx_diagrams_do_not_inherit_h100_memory_or_interconnect(self):
        topology = " ".join(ET.parse(ROOT / "assets/21-rtx-topology.svg").getroot().itertext())
        self.assertIn("КЛАСТЕР RTX 5060 Ti + ШЛЮЗ", topology)
        self.assertIn("КЛАСТЕР WEBUI + A30", topology)
        tp2 = " ".join(ET.parse(ROOT / "assets/24-rtx-tp2.svg").getroot().itertext())
        self.assertIn("RTX 5060 Ti / rank 0", tp2)
        self.assertIn("RTX 5060 Ti / rank 1", tp2)
        for wrong in ("NVLink", "H100", "HBM"):
            self.assertNotIn(wrong, tp2)
        budget = " ".join(ET.parse(ROOT / "assets/22-rtx-memory.svg").getroot().itertext())
        for term in ("16 GiB", "4K", "128K", "17,13 GiB", "4 GiB"):
            self.assertIn(term, budget)

    def test_session_count_is_named_and_cannot_be_read_as_eight(self):
        root = ET.parse(ROOT / "assets/25-kv-derivation.svg").getroot()
        text = " ".join(root.itertext())
        self.assertIn("N — число историй", text)
        self.assertIn("Mkv = N × (2 × S × L × Hkv × D × b)", text)
        self.assertNotIn("B НЕЗАВИСИМЫХ ИСТОРИЙ", text)
        self.assertIn("Hkv = KV-головы", text)
        self.assertIn("L = число слоёв", text)
        self.assertEqual(len([n for n in root.iter(SVG + "text")
                              if n.attrib.get("class") == "formula"]), 3)

    def test_rtx_sequence_keeps_independent_cache_and_tune_stages(self):
        text = " ".join(ET.parse(ROOT / "assets/20-rtx-sequence.svg").getroot().itertext())
        self.assertIn("Уже запущена", text)
        self.assertIn("rtx-gemma-cache", text)
        self.assertIn("rtx-gemma-tune", text)
        self.assertIn("Перед Tune останавливаем Cache", text)
        self.assertIn("Перед Qwen — обе Gemma", text)

    def test_rtx_window_is_not_a_concurrency_promise(self):
        text = " ".join(ET.parse(ROOT / "assets/22-rtx-memory.svg").getroot().itertext())
        self.assertIn("окно 128K", text)
        self.assertIn("До 8 активных последовательностей", text)
        self.assertIn("Окно 128K не означает 8 полных историй", text)
        self.assertNotIn("/ 8K", text)
        profile = json.loads((ROOT / "results/rtx5060-rehearsal-20261008.json").read_text())
        self.assertEqual(profile["profiles"]["qwen-128k"]["max_model_len"], 131072)
        self.assertEqual(profile["profiles"]["qwen-128k"]["max_num_seqs"], 8)
        self.assertEqual(profile["profiles"]["qwen-128k"]["kv_offloading_gib"], 16)

    def test_topology_does_not_mix_admin_control_with_inference(self):
        for name in ("01-topology", "21-rtx-topology"):
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

    def test_memory_tables_match_the_architecture_calculations(self):
        h100 = " ".join(ET.parse(ROOT / "assets/03-memory.svg").getroot().itertext())
        rtx = " ".join(ET.parse(ROOT / "assets/27-rtx-long-context.svg").getroot().itertext())
        for element_bytes in (1, 2):
            for tokens in (65536, 131072, 262144):
                value = calculate_gemma(tokens, element_bytes=element_bytes)["one_session_gib"]
                self.assertIn(f"{value:.2f}".replace(".", ",") + " GiB", h100)
            for tokens in (4096, 65536, 131072):
                value = calculate_gemma_e2b(tokens, element_bytes=element_bytes)["one_session_gib"]
                self.assertIn(f"{value * 1024:.0f} MiB", rtx)

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
