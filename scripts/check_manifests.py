#!/usr/bin/env python3
"""Offline Helm contracts; no API validation or cluster changes."""
import functools
import hashlib
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/vllm-runtime"
KINDS = {"ConfigMap", "Deployment", "ResourceClaimTemplate", "Service", "NetworkPolicy"}


def load(path):
    return yaml.safe_load(path.read_text())


@functools.lru_cache(maxsize=None)
def render(path):
    # Helm can silently ignore trailing YAML documents in a values file.
    if not isinstance(load(path), dict):
        raise ValueError("values must contain one YAML mapping")
    proc = subprocess.run(["helm", "template", "workshop", str(CHART),
                           "-n", "hardfest-demo", "-f", str(path)],
                          capture_output=True, text=True, check=True)
    docs = [obj for obj in yaml.safe_load_all(proc.stdout) if obj]
    if len(docs) != 5 or {obj["kind"] for obj in docs} != KINDS:
        raise ValueError("expected exactly five workload resources")
    return {obj["kind"]: obj for obj in docs}


def validate_objects(objects):
    errors = []
    for kind, obj in objects.items():
        if not obj.get("apiVersion") or obj.get("metadata", {}).get("namespace") != "hardfest-demo":
            errors.append(f"{kind}: invalid resource or namespace")
    cm, dep = objects["ConfigMap"], objects["Deployment"]
    raw = cm["data"]["profile.yaml"]
    config = yaml.safe_load(raw)
    pod = dep["spec"]["template"]
    if pod["metadata"]["annotations"].get("checksum/vllm-config") != hashlib.sha256(raw.encode()).hexdigest():
        errors.append("profile changed without updating the Pod checksum")
    if dep["spec"]["replicas"] != 0 or dep["spec"]["strategy"]["type"] != "Recreate":
        errors.append("public Deployment must be stopped and use Recreate")
    vols = {v["name"]: v for v in pod["spec"]["volumes"]}
    if vols["profile"]["configMap"]["name"] != cm["metadata"]["name"]:
        errors.append("ConfigMap reference mismatch")
    if "cpu-offload-gb" in config:
        errors.append("weight offload is not part of these profiles")
    claim = objects["ResourceClaimTemplate"]["metadata"]["name"]
    if pod["spec"]["resourceClaims"][0]["resourceClaimTemplateName"] != claim:
        errors.append("ResourceClaimTemplate reference mismatch")
    container = pod["spec"]["containers"][0]
    if "@sha256:" not in container["image"]:
        errors.append("runtime image must be digest-pinned")
    labels = pod["metadata"]["labels"]
    for key, value in objects["Service"]["spec"]["selector"].items():
        if labels.get(key) != value:
            errors.append("Service does not select its Pod")
    return errors


def check():
    errors = []
    paths = sorted((ROOT / "values").glob("*.yaml"))
    if len(paths) != 8:
        errors.append("expected eight standalone Helm profiles")
    for path in paths:
        try:
            errors.extend(f"{path.name}: {e}" for e in validate_objects(render(path)))
        except (KeyError, TypeError, OSError, ValueError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
            errors.append(f"{path.name}: {exc}")
    for path in (ROOT / "argocd").glob("*.yaml"):
        app = load(path)
        source = app["spec"]["source"]
        if path.stem == "observability":
            if source.get("directory") != {"recurse": False}:
                errors.append("observability must retain its standalone directory")
        elif "directory" in source or "helm" not in source:
            errors.append(f"{path.name}: expected Helm source, not directory")
        else:
            helm = source["helm"]
            if helm.get("parameters") or helm.get("valuesObject") or helm.get("values"):
                errors.append(f"{path.name}: keep settings in explicit valueFiles")
            if helm.get("valueFiles") != [f"../../values/{path.stem}.yaml",
                                         "../../site/gemma.yaml" if path.stem.startswith("gemma")
                                         else f"../../site/{path.stem}.yaml"]:
                errors.append(f"{path.name}: unexpected values precedence")
        if "automated" in app["spec"].get("syncPolicy", {}) or app["metadata"].get("finalizers"):
            errors.append(f"{path.name}: autosync/cascade must not be enabled")
    for path in (ROOT / "observability").glob("*.yaml"):
        for obj in yaml.safe_load_all(path.read_text()):
            if not obj or not obj.get("apiVersion") or not obj.get("kind"):
                errors.append(f"{path.name}: not a Kubernetes resource")
    return errors


if __name__ == "__main__":
    failures = check()
    print("\n".join(failures) if failures else "Helm contracts passed (offline; no cluster changes).")
    sys.exit(bool(failures))
