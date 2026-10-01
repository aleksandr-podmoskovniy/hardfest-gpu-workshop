import importlib.util
import io
import json
import pathlib
import re
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


bench = module("bench")
public = module("check_public")
workload = module("make_workload")


class PublicContent(unittest.TestCase):
    def test_credentials_and_secret_manifests_are_detected(self):
        samples = [
            "sk-" + "bf-" + "x" * 32,
            "hf_" + "x" * 32,
            "eyJ" + "x" * 20 + "." + "y" * 20 + "." + "z" * 20,
            "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----",
            "apiVersion: v1\nkind: " + "Secret\nmetadata: {}\n",
        ]
        for index, sample in enumerate(samples):
            with self.subTest(index=index):
                self.assertTrue(any(re.search(pattern, sample) for pattern in public.PATTERNS))


class WorkloadTokenizer(unittest.TestCase):
    def test_explicit_token_list_not_batch_encoding_key_count(self):
        tokenizer = Mock()
        tokenizer.apply_chat_template.side_effect = lambda *a, **kw: (
            [1, 2, 3, 4] if kw.get("return_dict") is False else
            {"input_ids": [1, 2, 3, 4], "attention_mask": [1, 1, 1, 1]})
        self.assertEqual(workload.chat_token_count(tokenizer, []), 4)

    def test_unexpected_template_return_fails_closed(self):
        for value in ({"input_ids": [1, 2]}, [[1, 2]], "text"):
            tokenizer = Mock()
            tokenizer.apply_chat_template.return_value = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                workload.chat_token_count(tokenizer, [])



class Measurements(unittest.TestCase):
    def test_first_reasoning_and_usage_are_recorded(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                           b'data: {"choices":[{"delta":{"reasoning_content":"Let"}}]}\n\n'
                           b'data: {"choices":[{"delta":{"content":"Answer"},"finish_reason":"stop"}]}\n\n'
                           b'data: {"choices":[],"usage":{"completion_tokens":42}}\n\n'
                           b'data: [DONE]\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["ttft_s"])
        self.assertEqual(result["stream_chunks"], 2)
        self.assertEqual(result["usage"]["completion_tokens"], 42)
        self.assertGreaterEqual(result["first_content_s"], result["ttft_s"])
        self.assertEqual(result["content_preview"], "Answer")

    def test_fixed_output_is_opt_in_and_sent_to_vllm(self):
        for fixed in (False, True):
            stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n')
            with patch.object(bench.urllib.request, "urlopen", return_value=stream) as call:
                bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30, fixed)
            payload = json.loads(call.call_args.args[0].data)
            if fixed:
                self.assertTrue(payload["ignore_eos"])
                self.assertEqual(payload["min_tokens"], 128)
            else:
                self.assertNotIn("ignore_eos", payload)

    def test_truncated_stream_is_error(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIn("Incomplete", result["error"])

    def test_done_without_output_is_error(self):
        stream = io.BytesIO(b'data: [DONE]\n\n')
        with patch.object(bench.urllib.request, "urlopen", return_value=stream):
            result = bench.request("http://localhost:8000", "test", {"messages": [], "max_tokens": 128}, 0, 30)
        self.assertIn("no output", result["error"])

    def test_sse_multiline_and_heartbeat(self):
        stream = io.BytesIO(b': ping\n\ndata: {"a":\ndata: 1}\n\ndata: [DONE]\n\n')
        self.assertEqual(list(bench.events(stream)), ['{"a":\n1}', '[DONE]'])

    def test_percentile_not_mean(self):
        self.assertEqual(bench.percentile([0, 10], .95), 9.5)
        self.assertIsNone(bench.percentile([], .95))

    def test_chunks_not_counted_as_tokens(self):
        rows = [{"error": None, "ttft_s": 1., "e2e_s": 2., "usage": None, "stream_chunks": 999}]
        self.assertIsNone(bench.summarize(rows, 2)["output_tokens_per_s"])

    def test_actual_usage_counts_tokens(self):
        rows = [{"error": None, "ttft_s": 1., "e2e_s": 2., "usage": {"completion_tokens": 100}}]
        self.assertEqual(bench.summarize(rows, 2)["output_tokens_per_s"], 50)

    def test_failed_requests_not_counted_successful(self):
        r = bench.summarize([{"error": "failed", "ttft_s": None}], 1)
        self.assertEqual(r["errors"], 1)
        self.assertEqual(r["successful"], 0)
        self.assertIsNone(r["client_ttft_p95_s"])


if __name__ == "__main__":
    unittest.main()
