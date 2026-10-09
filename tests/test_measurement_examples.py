"""Offline checks for the copyable diagnostic examples; never contact a cluster."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs/MEASUREMENTS.md").read_text()


class MeasurementExamples(unittest.TestCase):
    def test_trace_generator_produces_two_exact_scheduled_requests(self):
        mixed = DOC.split('<a id="mixed-prefill"></a>', 1)[1].split("## 5.", 1)[0]
        generator = re.search(r"```bash\n(.*?)```", mixed, re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["bash", "-euc", generator], cwd=directory,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            paths = list((Path(directory) / "results").glob("mixed-*.jsonl"))
            self.assertEqual(len(paths), 1)
            trace = [json.loads(line) for line in paths[0].read_text().splitlines()]
        self.assertEqual(len(trace), 2)
        self.assertEqual([row["timestamp"] for row in trace], [0, 0.05])
        self.assertEqual([row["input_length"] for row in trace], [8192, 128])
        self.assertEqual([row["output_length"] for row in trace], [512, 512])
        self.assertEqual(trace[1]["hash_ids"][0], trace[0]["hash_ids"][0] + 1)
        for flag in ("PYTHONHASHSEED=0", "--self-timed", "--save-detailed",
                     "--ready-check-timeout-sec 0", "--num-warmups 0"):
            self.assertIn(flag, mixed)

    @unittest.skipUnless(shutil.which("jq"), "jq is needed for the offload JSON gate")
    def test_offload_gate_rejects_partial_results_even_with_successful_cli_exit(self):
        offload = DOC.split('<a id="offload-replay"></a>', 1)[1].split("## 6.", 1)[0]
        expression = re.search(r"jq -e --argjson input.*?\n\s*'([^']+)'", offload).group(1)
        good = {"completed": 1, "failed": 0, "total_input_tokens": 8192,
                "total_output_tokens": 1}
        cases = ({}, {"completed": 0}, {"failed": 1},
                 {"total_input_tokens": 4096}, {"total_output_tokens": 0})
        for override in cases:
            with self.subTest(override=override):
                result = subprocess.run(["jq", "-e", "--argjson", "input", "8192", expression],
                                        input=json.dumps(good | override), text=True,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode == 0, not override)
        self.assertIn("offload_request 9001 original || exit 1", offload)
        self.assertIn('offload_request "$SEED" "evict-$SEED" || exit 1', offload)
        self.assertIn("offload_request 9001 replay || exit 1", offload)
        self.assertIn("2>&1 | tee", offload)


if __name__ == "__main__":
    unittest.main()
