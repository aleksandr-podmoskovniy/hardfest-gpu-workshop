import json
import pathlib
import subprocess
import sys
from .manifests import OWNER, STAGES

class Cluster:
    def __init__(self, site):
        self.site = site
        self.base = ["kubectl", "--kubeconfig", str(pathlib.Path(site["kubeconfig"]).expanduser()),
                     "--context", site["context"], "--request-timeout=15s"]

    def run(self, args, data=None, stream=False, timeout=60):
        result = subprocess.run(self.base + args, input=data, text=True,
                                capture_output=not stream, timeout=None if stream else timeout, check=True)
        return result.stdout

    def ns(self, args, **kw):
        return self.run(["-n", self.site["namespace"]] + args, **kw)

    def get(self, kind, name):
        raw = self.ns(["get", kind, name, "--ignore-not-found", "-o", "json"])
        return json.loads(raw) if raw.strip() else None

    def identity(self):
        # config view without --raw: credentials are neither requested nor printed.
        actual = self.run(["config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"]).strip()
        if actual.rstrip("/") != self.site["expected_server"].rstrip("/"):
            raise ValueError("Kubeconfig API mismatch; refusing cluster operations")
        self.run(["get", "--raw=/version"])
        print(f"Context: {self.site['context']}; namespace: {self.site['namespace']}", file=sys.stderr)

    def ownership(self, obj):
        if obj and obj.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/part-of") != OWNER:
            raise ValueError("Existing resource is not owned by this workshop; refusing takeover")

    def ready_node(self, stage):
        small = STAGES[stage][1] is None
        node = self.site["mig_node" if small else "h100_node"]
        obj = json.loads(self.run(["get", "node", node, "-o", "json"]))
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in obj["status"]["conditions"])
        if not ready or obj["spec"].get("unschedulable"):
            raise ValueError("Node is not Ready or is cordoned; no automatic repair")
