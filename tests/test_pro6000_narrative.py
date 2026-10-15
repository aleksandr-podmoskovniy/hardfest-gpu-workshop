"""Keep the PRO6000 route readable, honest, and runnable without cluster access."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "examples/pro6000-load-client"


class PRO6000Narrative(unittest.TestCase):
    def test_story_order_and_terms(self):
        text = (ROOT / "RTXPRO6000.md").read_text()
        self.assertIn("# Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере", text)
        sections = re.findall(r"^## (\d+)\.", text, re.M)
        self.assertEqual(sections, list("12345678"))
        platform = text.index("## 5.")
        self.assertNotIn("InferenceService", text[:platform])
        for term in ("Key–Value cache", "Dynamic Resource Allocation", "Multi-Instance GPU", "Multi-Process Service"):
            self.assertIn(term, text)
        self.assertNotIn("·", text)

    def test_visible_limits_and_long_budget(self):
        text = (ROOT / "RTXPRO6000.md").read_text()
        for value in ("229 376", "32 768", "262 144", "107,81", "207,81", "103,91"):
            self.assertIn(value, text)
        for caveat in ("несколько настроек", "Ручная B и платформенная Gemma",
                       "не попадает в учёт VK", "Заказ не фиксирует все параметры",
                       "Десять отправленных запросов не означают десять активных"):
            self.assertIn(caveat, text)
        self.assertIn("examples/pro6000-load-client/README.md", text)
        self.assertIn("results/pro6000/README.md", text)
        evidence = (ROOT / "results/pro6000/README.md").read_text()
        self.assertIn("gemma-matched-chat-ttft.svg", evidence)

    def test_archived_clients_have_exact_checksums_and_parse(self):
        entries = (CLIENT / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(entries), 5)
        for row in entries:
            checksum, name = row.split()
            data = (CLIENT / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), checksum, name)
            ast.parse(data, filename=name)

    def test_matched_gate_is_offline_without_execute(self):
        command = [sys.executable, str(CLIENT / "chat_comparison_probe.py")]
        for stage in ("a", "b"):
            command += ["--url-" + stage, "http://127.0.0.1:1",
                        "--capacity-report-" + stage,
                        str(ROOT / f"results/pro6000/gemma-{stage}-capacity-sse32k.final.json")]
        command += ["--report", "unused-pro6000-offline-report.json"]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Capacity gates passed", completed.stdout)
        self.assertFalse((ROOT / "unused-pro6000-offline-report.json").exists())

    def test_bootstrap_and_manual_ui_scaling_are_explicit(self):
        text = (ROOT / "docs/GITOPS.md").read_text()
        for token in ("pro6000-bootstrap", "values/pro6000/", "argocd/pro6000/", "site/pro6000.yaml",
                      "hardfest-pro6000-gemma-cache", "RespectIgnoreDifferences=true"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
