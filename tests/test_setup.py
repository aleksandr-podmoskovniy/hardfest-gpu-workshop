"""Execute the documented bootstrap locally; never contact a cluster or network."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def block_containing(path, marker):
    return next(block for block in re.findall(r"```bash\n(.*?)```", path.read_text(), re.S)
                if marker in block)


class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / "k8s-config"
        self.work.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        WORKSHOP_BRANCH="hardfest-demo")
        self.git("init", "-b", "main")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
        self.git("init", "--bare", str(self.base / "origin.git"))
        self.git("remote", "add", "origin", str(self.base / "origin.git"))
        self.git("push", "origin", "main")
        self.script = block_containing(ROOT / "docs/GITOPS.md", "git fetch origin")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def bootstrap(self, branch="hardfest-demo"):
        return subprocess.run(["bash", "-c", self.script], cwd=self.work,
                              env=dict(self.env, WORKSHOP_BRANCH=branch),
                              capture_output=True, text=True)

    def assert_ready(self, branch):
        self.assertEqual(self.git("branch", "--show-current"), branch)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "@{upstream}"), f"origin/{branch}")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", f"origin/{branch}"))

    def test_fresh_h100_and_rtx_branches_are_published_before_import(self):
        for branch in ("hardfest-demo", "hardfest-rtx"):
            with self.subTest(branch=branch):
                result = self.bootstrap(branch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_ready(branch)
                self.git("switch", "main")

    def test_existing_local_branch_is_reused(self):
        self.git("branch", "hardfest-demo")
        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_ready("hardfest-demo")

    def test_existing_remote_branch_is_checked_out_with_tracking(self):
        self.git("push", "origin", "HEAD:refs/heads/hardfest-demo")
        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_ready("hardfest-demo")

    def test_dirty_worktree_is_preserved_without_switch_or_publication(self):
        (self.work / "site.yaml").write_text("private site configuration\n")
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual((self.work / "site.yaml").read_text(), "private site configuration\n")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "hardfest-demo"), "")


class SetupRoute(unittest.TestCase):
    def test_setup_returns_from_access_branch_before_monitoring_and_runtime(self):
        h100, rtx = (ROOT / "docs/SETUP.md").read_text().split('<a id="rtx"></a>', 1)
        for route in (h100, rtx):
            self.assertLess(route.index('git switch "$WORKSHOP_BRANCH"'),
                            route.index("OBSERVABILITY.md"))
        access = (ROOT / "integrations/webui-access/README.md").read_text()
        update = next(block for block in re.findall(r"```bash\n(.*?)```", access, re.S)
                      if "Configure WebUI personal access" in block)
        self.assertIn('test "$(git branch --show-current)" = "$ACCESS_BRANCH"', update)
        self.assertIn('git push -u origin "HEAD:refs/heads/$ACCESS_BRANCH"', update)

    def test_both_sites_bootstrap_before_model_import_and_rtx_registers_before_sync(self):
        setup = (ROOT / "docs/SETUP.md").read_text()
        h100, rtx = setup.split('<a id="rtx"></a>', 1)
        self.assertLess(h100.index("GITOPS.md#bootstrap"), h100.index("../catalog/README.md"))
        self.assertIn("export WORKSHOP_BRANCH=hardfest-demo", h100)
        self.assertIn("export WORKSHOP_BRANCH=hardfest-rtx", rtx)
        self.assertLess(rtx.index("GITOPS.md#bootstrap"), rtx.index("helm template rtx-models"))
        self.assertLess(rtx.index('apply -f "$RTX_DIR/argo-app/"'),
                        rtx.index("patch application rtx-models"))
        self.assertLess(rtx.index('git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH"'),
                        rtx.index('apply -f "$RTX_DIR/argo-app/"'))

    def test_disabled_order_preflight_renders_the_resource_without_enabling_git_values(self):
        script = block_containing(ROOT / "docs/GITOPS.md", 'helm lint "$DEMO_DIR/charts/inference-service"')
        self.assertEqual(script.count("--set order.enabled=true"), 2)
        self.assertIn("apply --dry-run=server", script)
        for path in (ROOT / "platform").glob("*.yaml"):
            self.assertFalse(yaml.safe_load(path.read_text())["order"]["enabled"], path)


class MonitoringInstall(unittest.TestCase):
    def test_copy_selects_only_the_site_namespace_and_preserves_existing_configuration(self):
        script = block_containing(ROOT / "docs/OBSERVABILITY.md", 'test ! -e "$OBS_ROOT/observability"')
        for namespace in ("hardfest-demo", "hardfest-rtx"):
            with self.subTest(namespace=namespace), tempfile.TemporaryDirectory() as name:
                base = Path(name)
                (base / "hardfest-gpu-workshop").symlink_to(ROOT, target_is_directory=True)
                work = base / "k8s-config"
                work.mkdir()
                target = work / "argo-projects" / namespace
                env = dict(os.environ, OBS_ROOT=str(target), OBS_NAMESPACE=namespace)
                result = subprocess.run(["bash", "-c", script], cwd=work, env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                objects = list(yaml.safe_load_all((target / "observability/monitoring.yaml").read_text()))
                monitor, policy = objects
                self.assertEqual(monitor["kind"], "ServiceMonitor")
                self.assertEqual(monitor["spec"]["namespaceSelector"]["matchNames"], [namespace])
                self.assertEqual(policy["kind"], "NetworkPolicy")
                self.assertEqual(policy["metadata"]["namespace"], namespace)
                self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], {
                    "app.kubernetes.io/part-of": "hardfest-gpu-workshop",
                    "app.kubernetes.io/component": "llm-runtime",
                })
                self.assertEqual(policy["spec"]["ingress"], [{
                    "from": [{"namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "d8-monitoring"}},
                        "podSelector": {"matchLabels": {
                            "app.kubernetes.io/name": "prometheus", "prometheus": "main"}}}],
                    "ports": [{"port": 8000, "protocol": "TCP"}],
                }])
                self.assertEqual({p.name for p in (target / "observability").iterdir()},
                                 {"monitoring.yaml", "dashboard.yaml"})
                dashboard = target / "observability/dashboard.yaml"
                dashboard.write_text("existing site dashboard\n")
                repeated = subprocess.run(["bash", "-c", script], cwd=work, env=env,
                                          capture_output=True, text=True)
                self.assertNotEqual(repeated.returncode, 0)
                self.assertEqual(dashboard.read_text(), "existing site dashboard\n")
                self.assertFalse((target / "observability/observability").exists())


if __name__ == "__main__":
    unittest.main()
