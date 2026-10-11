import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pro6000_evidence as evidence


class PRO6000Evidence(unittest.TestCase):
    def test_public_checksums_and_strict_validators_pass(self):
        evidence.verify()

    def test_capacity_budgets_and_sampled_concurrency_are_distinct(self):
        for stage, budget, active, preemptions in (("a", 134752, 1, 0),
                                                  ("b", 229376, 2, 0),
                                                  ("qwen", 229376, 6, 2)):
            client_name, _, summary_name = evidence.client_names(stage)
            raw, summary = evidence.read(client_name), evidence.read(summary_name)
            self.assertEqual(set(raw), {"run_id", "ignore_eos", "passes"})
            self.assertTrue(raw["ignore_eos"])
            self.assertEqual(summary["completed"], 10)
            self.assertEqual(summary["preemptions"], preemptions)
            self.assertEqual(summary.get("sampled_max_running", summary.get("observed_max_running")), active)
            for row in raw["passes"][0]["results"]:
                self.assertEqual(row["usage"]["prompt_tokens"], budget)
                self.assertEqual(row["usage"]["completion_tokens"], 32768)
                self.assertEqual(row["finish_reason"], "length")

    def test_partial_or_inconsistent_capacity_cannot_be_promoted(self):
        for stage in ("a", "b", "qwen"):
            client_name, monitor_name, _ = evidence.client_names(stage)
            raw, monitor = evidence.read(client_name), evidence.read(monitor_name)
            validator = (evidence.qwen_evidence_summary.summarize if stage == "qwen"
                         else lambda client, metrics: evidence.gemma_capacity_summary.summarize(client, metrics, stage))
            for corruption in ("missing-response", "wrong-budget", "counter-reset"):
                client, metrics = copy.deepcopy(raw), copy.deepcopy(monitor)
                if corruption == "missing-response":
                    client["passes"][0]["results"].pop()
                elif corruption == "wrong-budget":
                    client["passes"][0]["results"][0]["usage"]["completion_tokens"] = 1
                else:
                    metrics["series"]["output_tokens"]["data"][0]["values"][-1][1] = 0
                with self.subTest(stage=stage, corruption=corruption), self.assertRaises(ValueError):
                    validator(client, metrics)

    def test_matched_results_keep_natural_eos_and_actual_output_lengths(self):
        report = evidence.read("gemma-matched-chat-final.json")
        summary = evidence.read("gemma-matched-chat-validated-summary.json")
        self.assertEqual(summary["source_sha256"],
                         "79e917b8f082f76b450d89fbae0df9b4c8ef140545d03ec040d6cce32a48caca")
        self.assertFalse(report["ignore_eos"])
        self.assertEqual(report["output_cap"], 32768)
        self.assertEqual(summary["completed_responses"], 60)
        for wave in report["waves"]:
            self.assertNotIn("metrics_before", wave)
            self.assertNotIn("metrics_after", wave)
            for row in wave["results"]:
                self.assertEqual(row["finish_reason"], "stop")
                self.assertTrue(row["answer_correct"])
                self.assertIn(row["usage"]["completion_tokens"], (13, 14))
                self.assertEqual(row["usage"]["prompt_tokens"], 98 if wave["kind"] == "short" else 98268)
        short_a, short_b = summary["waves"][:2]
        self.assertGreater(short_b["client_ttft_visible_answer_s"]["median"],
                           short_a["client_ttft_visible_answer_s"]["median"])

    def test_replay_is_one_token_diagnostic_with_measured_restore(self):
        report = evidence.read("gemma-b-offload-replay-final.json")
        self.assertTrue(report["restore_proved"])
        self.assertEqual(len(report["attempts"]), 1)
        attempt = report["attempts"][0]
        self.assertEqual(attempt["usage"]["completion_tokens"], 1)
        self.assertTrue(attempt["before"]["cpu_counter_present"])
        self.assertEqual(attempt["metrics_after"]["cpu_to_gpu_bytes_delta"], 9813360640)
        self.assertEqual(attempt["metrics_after"]["values"]["cpu_to_gpu_bytes"] -
                         attempt["before"]["cpu_to_gpu_bytes"], 9813360640)

    def test_gemma_figures_reproduce_public_samples_and_correct_caption(self):
        mapping = evidence.read("gemma-gpu-mapping.json")
        for binding in mapping["bindings"]:
            self.assertEqual(set(binding), {"model", "prometheus_gpu", "prometheus_device"})
        for stage in ("a", "b"):
            client_name, monitor_name, _ = evidence.client_names(stage)
            client, monitor = evidence.read(client_name), evidence.read(monitor_name)
            hashes = {"client": evidence.sha((evidence.DEST / client_name).read_bytes()),
                      "monitor": evidence.sha((evidence.DEST / monitor_name).read_bytes()),
                      "mapping": evidence.sha((evidence.DEST / "gemma-gpu-mapping.json").read_bytes())}
            actual = evidence.render_gemma_monitoring.render(client, monitor, stage, mapping, hashes)
            self.assertEqual(actual, (evidence.DEST / f"gemma-{stage}-capacity-monitoring.svg").read_text())
            self.assertIn("наблюдавшийся максимум active", actual)
            timeline = evidence.render_gemma_capacity.render(client, monitor, stage, hashes)
            self.assertEqual(timeline, (evidence.DEST / f"gemma-{stage}-sse32k-capacity.svg").read_text())
            self.assertEqual(timeline.count('data-phase="first-token"'), 10)

    def test_png_dimensions_and_public_artifact_inventory(self):
        svg_paths = sorted(evidence.DEST.glob("*.svg"))
        self.assertEqual(len(svg_paths), 6)
        manifest = evidence.read("manifest.json")
        for svg in svg_paths:
            parsed = ET.fromstring(svg.read_bytes())
            png = svg.with_suffix(".png")
            data = png.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]),
                             (int(parsed.attrib["width"]), int(parsed.attrib["height"])))
            self.assertEqual(manifest["files"][png.name]["inputs"], [svg.name])
            self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["files"][png.name]["sha256"])
        for path in evidence.DEST.glob("*.json"):
            self.assertLess(path.stat().st_size, 5_000_000)
            self.assertIsNone(evidence.PRIVATE.search(path.read_text()), path.name)

    def test_matched_plot_intervals_and_axes_follow_the_ten_client_responses(self):
        svg = ET.fromstring(evidence.matched_svg())
        ns = {"svg": "http://www.w3.org/2000/svg"}
        panels = svg.findall("svg:g[@data-panel]", ns)
        self.assertEqual([panel.attrib["data-scale"] for panel in panels], ["linear", "linear", "log"])
        summary = evidence.read("gemma-matched-chat-validated-summary.json")
        for panel in panels:
            kind, iteration = panel.attrib["data-panel"].split("-")
            for interval in panel.findall("svg:g[@data-series]", ns):
                wave = next(wave for wave in summary["waves"] if
                            (wave["model"], wave["kind"], wave["iteration"]) ==
                            ("selectel-gemma-" + interval.attrib["data-series"], kind, iteration))
                self.assertEqual(int(interval.attrib["data-count"]), 10)
                for stat in ("min", "median", "max"):
                    self.assertEqual(float(interval.attrib["data-" + stat]),
                                     wave["client_ttft_visible_answer_s"][stat])
        bad = copy.deepcopy(summary)
        bad["waves"][0]["client_ttft_visible_answer_s"]["median"] *= 2
        with self.assertRaisesRegex(ValueError, "does not match"):
            evidence.render_matched_chat.render(evidence.read("gemma-matched-chat-final.json"), bad, {})


if __name__ == "__main__":
    unittest.main()
