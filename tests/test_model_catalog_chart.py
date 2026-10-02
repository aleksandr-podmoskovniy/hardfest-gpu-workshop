import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/model-catalog"


class ModelCatalogChart(unittest.TestCase):
    def render(self, values=None, success=True):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["helm", "template", "hf-models", str(CHART), "-n", "hardfest-demo"]
            if values is not None:
                path = Path(tmp) / "values.yaml"
                path.write_text(yaml.safe_dump(values))
                command += ["-f", str(path)]
            result = subprocess.run(command, capture_output=True, text=True)
            if not success:
                self.assertNotEqual(result.returncode, 0)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            return [item for item in yaml.safe_load_all(result.stdout) if item]

    def test_empty_default_does_not_download_anything(self):
        self.assertEqual(self.render(), [])

    def test_catalog_matches_lock_and_preserves_models(self):
        values = yaml.safe_load((ROOT / "catalog/models.yaml").read_text())
        lock = json.loads((ROOT / "models.lock.json").read_text())["models"]
        objects = self.render(values)
        self.assertEqual(len(objects), 3)
        for obj, key in zip(objects, ("gemma", "assistant", "qwen")):
            expected = lock[key]
            self.assertEqual(obj["kind"], "Model")
            self.assertEqual(obj["metadata"]["namespace"], "hardfest-demo")
            self.assertEqual(obj["spec"]["source"], {
                "url": f"https://huggingface.co/{expected['repository']}/tree/{expected['revision']}"})
            self.assertEqual(obj["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
                             "Prune=false,Delete=false")
            self.assertEqual(obj["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")

    def test_duplicate_unpinned_and_inline_secret_rejected(self):
        model = yaml.safe_load((ROOT / "catalog/models.yaml").read_text())["models"][0]
        for items in ([model, model], [dict(model, revision="main")],
                      [dict(model, token="must-not-enter-git")]):
            with self.subTest(items=items):
                self.render({"models": items}, success=False)

    def test_secret_reference_is_not_secret_data(self):
        model = yaml.safe_load((ROOT / "catalog/models.yaml").read_text())["models"][0]
        obj = self.render({"models": [dict(model, authSecretName="hf-private-token")]})[0]
        self.assertEqual(obj["spec"]["source"]["authSecretRef"], {"name": "hf-private-token"})
