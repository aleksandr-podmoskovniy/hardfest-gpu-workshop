"""Article scorecards must remain traceable to the recorded workload, not marketing ratios."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import article_results as results


class ArticleResults(unittest.TestCase):
    def test_generated_tables_and_figures_match_source_measurements(self):
        for path, expected in results.outputs().items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(), expected)

    def test_rtx_controls_reject_partial_or_different_workloads(self):
        original = results.read("results/rtx5060-rehearsal-20261008.json")
        for key, value in (("failed", 1), ("completed", 7), ("max_concurrency", 8),
                           ("total_output_tokens", 32768), ("total_input_tokens", 65536)):
            with self.subTest(key=key):
                changed = deepcopy(original)
                changed["results"][0]["result"][key] = value
                with self.assertRaisesRegex(ValueError, "Incomparable workload"):
                    results.rtx_results(changed)
        changed = deepcopy(original)
        changed["profiles"]["tune-4k"]["max_model_len"] = 131072
        with self.assertRaisesRegex(ValueError, "Changed control profile"):
            results.rtx_results(changed)
        with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
            results.rtx_results(original | {"results": original["results"][1:]})

    def test_figure_labels_include_limits_and_the_short_request_regression(self):
        _, figures = results.build()
        texts = {name: " ".join(ET.fromstring(svg).itertext()) for name, svg in figures.items()}
        h100 = texts["h100-offload-result"]
        for phrase in ("Offload включён в обоих", "65 536", "128 выхода", "vLLM 0.30", "не ёмкости"):
            self.assertIn(phrase, h100)
        rtx = texts["rtx5060-tuning-result"]
        for phrase in ("Все профили 4K", "средние", "ток/с — вся серия", "не измеряет текущий Tune"):
            self.assertIn(phrase, rtx)
        pro = texts["pro6000-chat-result"]
        for phrase in ("0,308", "0,441", "98 268", "13–14", "Медиана", "напрямую в vLLM"):
            self.assertIn(phrase, pro)

    def test_capacity_cards_reject_incomplete_or_changed_protocol(self):
        for filename, tokens in (("gemma-a-sse32k-validated-summary.json", 134752),
                                 ("gemma-b-sse32k-validated-summary.json", 229376),
                                 ("qwen-10x256k-summary.json", 229376)):
            original = results.read("results/pro6000/" + filename)
            for key, value in (("completed", 9), ("submitted_concurrently", 11),
                               ("output_tokens_each", 12), ("input_tokens_each", 42),
                               ("window_tokens", 4096), ("synthetic_ignore_eos", False),
                               ("requests", [])):
                with self.subTest(file=filename, key=key):
                    with self.assertRaisesRegex(ValueError, "Capacity summary"):
                        results.validate_capacity(original | {key: value}, tokens)

    def test_h100_card_rejects_a_local_gpu_hit_presented_as_ram_replay(self):
        original_read = results.read

        def changed_read(name):
            value = original_read(name)
            if name == "results/kv-ram/observations.json":
                row = next(row for row in value["return_request_counter_samples"]
                           if row["name"] == "vllm:prefix_cache_hits_total")
                row["after"] += 32
            return value

        with patch.object(results, "read", changed_read):
            with self.assertRaisesRegex(ValueError, "H100 replay counters"):
                results.build()

    def test_main_prose_does_not_hide_negative_or_capacity_findings(self):
        pro = (ROOT / "RTXPRO6000.md").read_text()
        for phrase in ("На коротком входе B Tune начинает немного позже", "не о скорости длинной генерации",
                       "Входы различаются", "неприемлемо для чата", "полный набор параметров vLLM не сохранён"):
            self.assertIn(phrase, pro)
        rtx = (ROOT / "RTX5060.md").read_text()
        for phrase in ("первые проходы", "не проверка полного окна", "CPU-лимиты профилей различались"):
            self.assertIn(phrase, rtx)
        h100 = (ROOT / "README.md").read_text()
        self.assertIn("Сопоставимого отчёта по всем трём профилям H100 — Base, Cache и Tune — в материалах нет", h100)


if __name__ == "__main__":
    unittest.main()
