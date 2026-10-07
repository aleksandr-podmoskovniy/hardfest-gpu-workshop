"""Offline rendering checks; no credentials or cluster access."""
import json
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/webui-access"


class WebUIAccessChart(unittest.TestCase):
    def test_durable_issuance_volume_and_retention(self):
        args = ["--set", "persistence.enabled=true", "--set", "config.state_dir=/state"]
        objects = self.render(*args)
        claim = objects["PersistentVolumeClaim"]
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "64Mi")
        self.assertEqual(claim["metadata"]["annotations"]["argocd.argoproj.io/sync-options"], "Prune=false")
        pod = objects["Deployment"]["spec"]["template"]["spec"]
        self.assertEqual(pod["securityContext"]["fsGroup"], 65532)
        self.assertIn({"name": "state", "mountPath": "/state"}, pod["containers"][0]["volumeMounts"])
        existing = self.render(*args, "--set", "persistence.existingClaim=retained-state")
        self.assertNotIn("PersistentVolumeClaim", existing)
        self.assertIn({"name": "state", "persistentVolumeClaim": {"claimName": "retained-state"}}, existing["Deployment"]["spec"]["template"]["spec"]["volumes"])

    def test_refuses_unbacked_or_unconfigured_journal(self):
        for option in ("persistence.enabled=true", "config.state_dir=/state"):
            result = subprocess.run(["helm", "template", "access", str(CHART), "--set", option], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("durable issuance", result.stderr)

    def test_durable_issuance_refuses_reserve_assignment(self):
        result = subprocess.run(["helm", "template", "access", str(CHART), "--set", "persistence.enabled=true", "--set", "config.state_dir=/state", "--set", "config.provisioning_mode=reserve"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("durable issuance requires create mode", result.stderr)

    def render(self, *options):
        result = subprocess.run(
            ["helm", "template", "access", str(CHART), "-n", "workshop-ui", *options],
            capture_output=True, text=True, check=True,
        )
        return {item["kind"]: item for item in yaml.safe_load_all(result.stdout) if item}

    def test_default_stopped_and_no_embedded_credentials(self):
        objects = self.render()
        self.assertEqual(set(objects), {"ConfigMap", "Deployment", "Service", "NetworkPolicy"})
        dep = objects["Deployment"]
        self.assertEqual(dep["spec"]["replicas"], 0)
        self.assertEqual(dep["spec"]["strategy"]["type"], "Recreate")
        pod = dep["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["containers"][0]["envFrom"], [{"secretRef": {"name": "webui-access"}}])
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])

    def test_refuses_multiple_allocators(self):
        result = subprocess.run(["helm", "template", "access", str(CHART), "--set", "replicaCount=2"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("replicaCount", result.stderr)

    def test_private_ca_preserves_system_roots(self):
        objects = self.render("--set", "caConfigMap=internal-ca")
        pod = objects["Deployment"]["spec"]["template"]["spec"]
        self.assertIn({"name": "SSL_CERT_DIR", "value": "/etc/webui-access-ca:/etc/ssl/certs"}, pod["containers"][0]["env"])
        self.assertIn({"name": "ca", "configMap": {"name": "internal-ca"}}, pod["volumes"])

    def test_policy_values_and_checksum_are_rendered(self):
        before = self.render()
        after = self.render("--set", "config.budget_usd=250")
        config = json.loads(before["ConfigMap"]["data"]["config.json"])
        self.assertEqual(config["budget_usd"], 10)
        self.assertEqual(config["pricing"], [])
        self.assertEqual(config["providers"], [])
        self.assertEqual(config["provisioning_mode"], "create")
        checksum = lambda x: x["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
        self.assertNotEqual(checksum(before), checksum(after))

    def test_workshop_policy_is_an_optional_overlay(self):
        objects = self.render("-f", str(ROOT / "examples/webui-access.yaml"))
        config = json.loads(objects["ConfigMap"]["data"]["config.json"])
        self.assertEqual(config["budget_usd"], 500)
        self.assertEqual(len(config["chat_models"]), 2)
        self.assertEqual(config["pricing"][0]["input_usd_per_million_tokens"], 10)

    def test_webui_background_tasks_have_bounded_parallel_capacity(self):
        before = self.render()
        config = json.loads(before["ConfigMap"]["data"]["config.json"])
        self.assertEqual(config["max_concurrent_per_user"], 3)
        self.assertEqual(config["max_concurrent_total"], 8)
        after = self.render("--set", "config.max_concurrent_per_user=2")
        actual = json.loads(after["ConfigMap"]["data"]["config.json"])
        self.assertEqual(actual["max_concurrent_per_user"], 2)
        checksum = lambda obj: obj["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
        self.assertNotEqual(checksum(before), checksum(after))

    def test_running_with_missing_policy_or_unpinned_image_fails(self):
        for extra in ([], ["-f", str(ROOT / "examples/webui-access.yaml")]):
            result = subprocess.run(["helm", "template", "access", str(CHART), *extra,
                                     "--set", "replicaCount=1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_legacy_configuration_is_rejected(self):
        result = subprocess.run(["helm", "template", "access", str(CHART),
                                 "--set", "config.allowed_models[0]=legacy"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("allowed_models", result.stderr)

    def test_native_issuer_is_explicit_and_changes_checksum(self):
        before = self.render()
        after = self.render("--set", "config.issuer_user_id=issuer-owner-id",
                            "--set", "config.issuer_user_access_profile_id=7")
        config = json.loads(after["ConfigMap"]["data"]["config.json"])
        self.assertEqual(config["issuer_user_id"], "issuer-owner-id")
        self.assertEqual(config["issuer_user_access_profile_id"], 7)
        self.assertEqual(after["Deployment"]["spec"]["replicas"], 0)
        checksum = lambda x: x["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
        self.assertNotEqual(checksum(before), checksum(after))

    def test_partial_invalid_or_reserved_native_issuer_is_rejected(self):
        cases = [
            ["--set", "config.issuer_user_id=issuer-owner-id"],
            ["--set", "config.issuer_user_access_profile_id=7"],
            ["--set", "config.issuer_user_id=../bad", "--set", "config.issuer_user_access_profile_id=7"],
            ["--set", "config.issuer_user_id=issuer-owner-id", "--set", "config.issuer_user_access_profile_id=-1"],
            ["--set", "config.issuer_user_id=issuer-owner-id", "--set", "config.issuer_user_access_profile_id=7",
             "--set", "config.provisioning_mode=reserve"],
        ]
        for options in cases:
            with self.subTest(options=options):
                result = subprocess.run(["helm", "template", "access", str(CHART), *options],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
