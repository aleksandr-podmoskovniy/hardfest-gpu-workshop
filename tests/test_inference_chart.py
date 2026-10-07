import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/inference-service"


class InferenceOrders(unittest.TestCase):
    def test_inline_launch_commands_use_real_helm_values(self):
        # Exercise the filenames shown in the walkthroughs, not a separate lab.
        expected = {"README.md": {"gemma", "qwen"},
                    "RTX5060.md": {"rtx-gemma", "rtx-qwen"}}
        for filename, orders in expected.items():
            text = (ROOT / filename).read_text()
            commands = re.findall(r"helm template [^`]*?\|\n\s+kubectl[^\n]*", text)
            found = set()
            for command in commands:
                if "/charts/inference-service" not in command:
                    continue
                name = re.search(r'/platform/([\w-]+)\.yaml', command)[1]
                self.assertNotIn('apply --dry-run=server -f "$', command)
                self.assertIn("apply --dry-run=server -f -", command)
                rendered = self.render(name, overrides=self.enabled())
                self.assertEqual([doc["kind"] for doc in rendered], ["InferenceService"])
                found.add(name)
            self.assertEqual(found, orders)

    def render(self, name, enabled=False, overrides=None, success=True):
        with tempfile.TemporaryDirectory() as tmp:
            flags = Path(tmp) / "values.yaml"
            flags.write_text(yaml.safe_dump(overrides or {"order": {"enabled": enabled}}))
            proc = subprocess.run(["helm", "template", "test", str(CHART), "-n", "hardfest-demo",
                                   "-f", str(ROOT / "platform" / (name + ".yaml")),
                                   "-f", str(flags)], capture_output=True, text=True)
            if not success:
                self.assertNotEqual(proc.returncode, 0)
                return proc.stderr
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return [obj for obj in yaml.safe_load_all(proc.stdout) if obj]

    def enabled(self):
        return {"order": {"enabled": True, "spec": {
            "inferenceServiceClassName": "dedicated-test",
            "resources": {"accelerator": {"deviceClasses": ["generated-test"]}}}}}

    def test_defaults_allocate_nothing_and_placeholders_fail_closed(self):
        for name in ("gemma", "qwen", "embedding", "reranker", "whisper"):
            with self.subTest(name=name):
                self.assertEqual(self.render(name), [])
                self.assertIn("replace order placeholders", self.render(name, enabled=True, success=False))

    def test_orders_create_only_the_controller_api_and_keep_model_refs(self):
        for name in ("gemma", "qwen", "embedding", "reranker", "whisper"):
            with self.subTest(name=name):
                objects = self.render(name, overrides=self.enabled())
                self.assertEqual(len(objects), 1)
                order = objects[0]
                self.assertEqual(order["kind"], "InferenceService")
                self.assertEqual(order["apiVersion"], "ai.deckhouse.io/v1alpha1")
                self.assertEqual(order["metadata"]["namespace"], "hardfest-demo")
                self.assertEqual(order["spec"]["model"]["src"], "ai-models")
                self.assertEqual(order["spec"]["launchStrategy"], "Throughput")
                self.assertNotIn("recipeName", order["spec"])
                app = yaml.safe_load((ROOT / "argocd" / (name + "-platform.yaml")).read_text())
                cluster = "gpu-cluster" if name in ("gemma", "qwen") else "a30-cluster"
                self.assertEqual(app["spec"]["destination"]["name"], cluster)
                self.assertEqual(app["spec"]["source"]["helm"]["valueFiles"], ["../../platform/" + name + ".yaml"])
                self.assertNotIn("automated", app["spec"]["syncPolicy"])

    def test_invalid_order_shape_and_unowned_runtime_flags_are_rejected(self):
        for patch in ({"replicas": 2}, {"recipeName": "invented"}, {"vllm": {"model": "x"}}):
            values = self.enabled()
            values["order"]["spec"].update(patch)
            self.render("gemma", overrides=values, success=False)

    def test_model_reference_namespace_matches_controller_contract(self):
        for kind, namespace, succeeds in (("Model", "hardfest-demo", True),
                                          ("Model", "another-project", False),
                                          ("ClusterModel", "hardfest-demo", False),
                                          ("ClusterModel", None, True)):
            with self.subTest(kind=kind, namespace=namespace):
                values = self.enabled()
                ref = {"kind": kind, "name": "test-model"}
                if namespace is not None:
                    ref["namespace"] = namespace
                values["order"]["spec"]["model"] = {"src": "ai-models", "ref": ref}
                self.render("gemma", overrides=values, success=succeeds)

    def test_a30_catalog_is_pinned_to_lock(self):
        catalog = yaml.safe_load((ROOT / "catalog/a30.yaml").read_text())
        lock = json.loads((ROOT / "models.lock.json").read_text())
        pinned = {model["repository"]: model["revision"] for model in lock["models"].values()}
        self.assertEqual(len(catalog["models"]), 3)
        for model in catalog["models"]:
            self.assertEqual(model["revision"], pinned[model["repository"]])
        proc = subprocess.run(["helm", "template", "a30", str(ROOT / "charts/model-catalog"),
                               "-n", "hardfest-demo", "-f", str(ROOT / "catalog/a30.yaml")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objects = [obj for obj in yaml.safe_load_all(proc.stdout) if obj]
        self.assertEqual([obj["kind"] for obj in objects], ["Model"] * 3)
