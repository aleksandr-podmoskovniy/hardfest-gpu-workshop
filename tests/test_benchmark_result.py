import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BenchmarkResult(unittest.TestCase):
    def test_documented_gate_rejects_failed_and_partial_streams(self):
        success = {"failed": 0, "completed": 8,
                   "total_input_tokens": 262144, "total_output_tokens": 16384}
        cases = [
            (success, True),
            ({**success, "failed": 4, "completed": 4,
              "total_input_tokens": 131072, "total_output_tokens": 3001}, False),
            ({**success, "total_output_tokens": 16383}, False),
            ({**success, "total_input_tokens": 262143}, False),
            ({}, False),
        ]
        filters = []
        for filename in ("README.md", "labs/01-ab.md"):
            text = (ROOT / filename).read_text()
            matches = re.findall(
                r"jq -e '\n(.*?)\n' \"results/hardfest/\$SERIES/result.json\"",
                text, re.S)
            self.assertEqual(len(matches), 1, filename)
            filters.append(matches[0])
            for result, accepted in cases:
                with self.subTest(filename=filename, result=result):
                    run = subprocess.run(["jq", "-e", matches[0]],
                                         input=json.dumps(result), text=True,
                                         capture_output=True, timeout=5)
                    self.assertEqual(run.returncode == 0, accepted, run.stderr)
        self.assertEqual(filters[0], filters[1])


if __name__ == "__main__":
    unittest.main()
