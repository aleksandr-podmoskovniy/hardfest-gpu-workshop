import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kv_math import calculate_gemma, calculate_gemma_e2b


class KVTeaching(unittest.TestCase):
    def test_e2b_counts_only_layers_owning_kv(self):
        kv = calculate_gemma_e2b(4096)
        self.assertEqual(kv["full_attention_gib"] * 1024, 24)
        self.assertEqual(kv["sliding_attention_gib"] * 1024, 6)
        self.assertEqual(kv["one_session_gib"] * 1024, 30)
        self.assertEqual(calculate_gemma_e2b(4096, 2)["all_sessions_gib"] * 1024, 60)

    def test_e2b_length_and_kv_precision(self):
        for tokens, bf16 in ((4096, 30), (8192, 54), (65536, 390), (131072, 774)):
            with self.subTest(tokens=tokens):
                self.assertEqual(calculate_gemma_e2b(tokens)["one_session_gib"] * 1024, bf16)
                self.assertEqual(calculate_gemma_e2b(tokens, 1, 1)["one_session_gib"] * 1024, bf16 / 2)
        self.assertEqual(calculate_gemma_e2b(256)["sliding_attention_gib"] * 2,
                         calculate_gemma_e2b(512)["sliding_attention_gib"])
        self.assertEqual(calculate_gemma_e2b(512)["sliding_attention_gib"],
                         calculate_gemma_e2b(8192)["sliding_attention_gib"])

    def test_31b_baseline_and_long_history(self):
        for tokens, expected in ((16384, 2.03125), (65536, 5.78125),
                                 (131072, 10.78125), (262144, 20.78125)):
            self.assertEqual(calculate_gemma(tokens)["one_session_gib"], expected)
            self.assertEqual(calculate_gemma(tokens, 1, 1)["one_session_gib"], expected / 2)
        self.assertEqual(calculate_gemma(16384, 4)["all_sessions_gib"], 8.125)

    def test_derivation_remains_in_both_main_guides(self):
        for name in ("README.md", "RTX5060.md"):
            doc = (ROOT / name).read_text().split('id="memory"', 1)[1].split('id="ram"', 1)[0]
            for term in ("KV-голов", "GQA", "Hkv", "awk -v S=", "min(S,",
                         "BF16", "FP8", "docs/MEMORY_BUDGET.md#verify-kv"):
                self.assertIn(term, doc)

    def test_rtx_long_context_is_required_before_platform_stage(self):
        doc = (ROOT / "RTX5060.md").read_text()
        self.assertLess(doc.index('id="long-context"'), doc.index('id="platform"'))
        stage = doc.split('id="long-context"', 1)[1].split('id="platform"', 1)[0]
        # The current route uses a single 128K server window and two input
        # lengths. 65536 was an older run's window, not a required restart.
        for term in ("131072", "57344", "122880", "assistant", "completed=1", "failed=0"):
            self.assertIn(term, stage)
        qwen = (ROOT / "RTX5060.md").read_text().split('id="qwen-long-context"', 1)[1]
        self.assertIn("--endpoint /v1/completions --model rtx-qwen35-9b", qwen)
        self.assertIn("--max-concurrency 8", qwen)
        self.assertIn("122880", qwen)
        self.assertRegex(qwen, r"(?i)Состояние linear attention")
