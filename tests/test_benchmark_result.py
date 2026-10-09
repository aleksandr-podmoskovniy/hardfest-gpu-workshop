import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BenchmarkResult(unittest.TestCase):
    def test_documented_gate_rejects_failed_and_partial_streams(self):
        success = {"failed": 0, "completed": 8,
                   "total_input_tokens": 65536, "total_output_tokens": 16384}
        cases = [
            (success, True),
            ({**success, "failed": 4, "completed": 4,
              "total_input_tokens": 131072, "total_output_tokens": 3001}, False),
            ({**success, "total_output_tokens": 16383}, False),
            ({**success, "total_input_tokens": 65535}, False),
            ({}, False),
        ]
        text = (ROOT / "README.md").read_text()
        # The reader checks an explicit field/value table, not a hidden parser.
        # Tie those requirements to the actual command's workload dimensions.
        requirements = {key: int(value) for key, value in re.findall(
            r"^\| `(failed|completed|total_input_tokens|total_output_tokens)` \| `(\d+)` \|$",
            text, re.M)}
        self.assertEqual(requirements, success)
        prompts = int(re.search(r"--num-prompts (\d+)", text)[1])
        input_tokens = int(re.search(r"--random-input-len (\d+)", text)[1])
        output_tokens = int(re.search(r"--random-output-len (\d+)", text)[1])
        self.assertEqual(requirements["completed"], prompts)
        self.assertEqual(requirements["total_input_tokens"], prompts * input_tokens)
        self.assertEqual(requirements["total_output_tokens"], prompts * output_tokens)
        for result, accepted in cases:
            with self.subTest(result=result):
                self.assertEqual(all(result.get(key) == value
                                     for key, value in requirements.items()), accepted)
        disclosures = re.findall(r"<details>.*?</details>", text, re.S)
        self.assertTrue(any("vllm bench serve" in block and "`failed`" in block
                            for block in disclosures))


if __name__ == "__main__":
    unittest.main()
