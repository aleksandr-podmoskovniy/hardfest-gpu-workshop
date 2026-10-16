"""The copyable Console order must preserve the prepared chat contract."""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ConsoleReadyOrders(unittest.TestCase):
    def test_a30_orders_are_ready_for_console_and_retain_service_names(self):
        cases = [
            ("embedding", "hardfest-a30-embedding", "qwen3-embedding-4b-w4a16-catalog", "Throughput"),
            ("reranker", "hardfest-a30-reranker", "qwen3-reranker-4b-w4a16-catalog", "Throughput"),
            ("whisper", "hardfest-a30-speech", "whisper-large-v3-catalog", "Latency"),
        ]
        for name, service_class, model, strategy in cases:
            relative = f"platform/a30/{name}.yaml"
            obj = yaml.safe_load((ROOT / relative).read_text())
            expected = {"inferenceServiceClassName": service_class, "launchStrategy": strategy,
                        "model": {"src": "ai-models", "ref": {"kind": "Model", "name": model}}}
            if name != "whisper":
                expected["resources"] = {"accelerator": {"deviceClasses": ["cb-a30-2g12gb-mps-percent"]}}
            self.assertEqual(obj, {"apiVersion": "ai.deckhouse.io/v1alpha1", "kind": "InferenceService",
                "metadata": {"name": "hf-" + name, "namespace": "hardfest-demo"}, "spec": expected})
            for article in ("README.md", "RTX5060.md", "RTXPRO6000.md", "docs/CHAT_AND_ACCESS.md"):
                self.assertIn(relative, (ROOT / article).read_text())

    def test_all_six_orders_are_complete_and_use_the_prepared_names(self):
        cases = [
            ("h100", "gemma", "hf-platform-gemma", "gemma-4-31b", "hardfest-h100-gemma", "hardfest-h100", "Throughput", "README.md"),
            ("h100", "qwen", "hf-qwen-platform", "qwen3-8-flash-next-nvfp4", "hardfest-h100-tp2", "hardfest-h100", "Throughput", "README.md"),
            ("rtx5060", "gemma", "rtx-gemma-platform", "rtx-gemma-e2b", "hardfest-rtx-gemma", "hardfest-rtx", "Throughput", "RTX5060.md"),
            ("rtx5060", "qwen", "rtx-qwen35-tp2", "rtx-qwen35-9b", "hardfest-rtx-qwen35", "hardfest-rtx", "Throughput", "RTX5060.md"),
            ("pro6000", "gemma", "selectel-gemma31", "gemma-4-31b-catalog", "hardfest-selectel-gemma31", "hardfest-selectel-blackwell", "Latency", "RTXPRO6000.md"),
            ("pro6000", "qwen", "selectel-qwen38-tp2", "qwen3-8-flash-next-nvfp4-catalog", "hardfest-selectel-qwen38-tp2", "hardfest-selectel-blackwell", "Latency", "RTXPRO6000.md"),
        ]
        setup = (ROOT / "docs/SETUP.md").read_text()
        for site, model, name, ref, service_class, device, strategy, article in cases:
            with self.subTest(site=site, model=model):
                relative = f"platform/{site}/{model}.yaml"
                text = (ROOT / relative).read_text()
                self.assertNotIn("REPLACE_", text)
                obj = yaml.safe_load(text)
                self.assertEqual(obj["apiVersion"], "ai.deckhouse.io/v1alpha1")
                self.assertEqual(obj["kind"], "InferenceService")
                self.assertEqual(obj["metadata"], {"name": name, "namespace": "hardfest-rtx" if site == "rtx5060" else "hardfest-demo"})
                self.assertEqual(obj["spec"], {
                    "inferenceServiceClassName": service_class,
                    "launchStrategy": strategy,
                    "model": {"src": "ai-models", "ref": {"kind": "Model", "name": ref}},
                    "resources": {"accelerator": {"deviceClasses": [device]}},
                })
                self.assertIn(relative, (ROOT / article).read_text())
                self.assertIn(relative, setup)

    def test_inline_pro_qwen_is_identical_to_the_ready_file(self):
        article = (ROOT / "RTXPRO6000.md").read_text()
        candidates = [yaml.safe_load(s) for s in re.findall(r"```yaml\n(.*?)\n```", article, re.S)]
        candidates = [o for o in candidates if isinstance(o, dict) and o.get("metadata", {}).get("name") == "selectel-qwen38-tp2"]
        self.assertEqual(candidates, [yaml.safe_load((ROOT / "platform/pro6000/qwen.yaml").read_text())])


if __name__ == "__main__":
    unittest.main()
