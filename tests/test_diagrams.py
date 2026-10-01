import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_diagrams import Box, Diagram

SVG = "{http://www.w3.org/2000/svg}"


class DiagramConnections(unittest.TestCase):
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
