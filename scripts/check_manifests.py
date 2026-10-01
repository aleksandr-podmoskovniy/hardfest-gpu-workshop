#!/usr/bin/env python3
"""Offline contract checks for the public plain-YAML examples, not API validation."""
import hashlib
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
KINDS = {
    "configmap": "ConfigMap", "deployment": "Deployment",
    "resourceclaimtemplate": "ResourceClaimTemplate", "service": "Service",
    "networkpolicy": "NetworkPolicy",
}


def load(path):
    return yaml.safe_load(path.read_text())


def validate_profile(path):
    errors = []
    if {p.stem for p in path.glob("*.yaml")} != set(KINDS):
        errors.append("expected exactly five Kubernetes manifests")
    objects = {}
    for name, kind in KINDS.items():
        obj = load(path / (name + ".yaml"))
        objects[name] = obj
        if obj.get("kind") != kind or not obj.get("apiVersion"):
            errors.append(f"{name}: not a {kind} Kubernetes resource")
        if obj.get("metadata", {}).get("namespace") != "hardfest-demo":
            errors.append(f"{name}: unexpected namespace")
    cm, dep = objects["configmap"], objects["deployment"]
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
    claim = objects["resourceclaimtemplate"]["metadata"]["name"]
    if pod["spec"]["resourceClaims"][0]["resourceClaimTemplateName"] != claim:
        errors.append("ResourceClaimTemplate reference mismatch")
    container = pod["spec"]["containers"][0]
    if "@sha256:" not in container["image"]:
        errors.append("runtime image must be digest-pinned")
    labels = pod["metadata"]["labels"]
    for key, value in objects["service"]["spec"]["selector"].items():
        if labels.get(key) != value:
            errors.append("Service does not select its Pod")
    return errors


def check():
    errors = []
    for path in sorted((ROOT / "deploy").iterdir()):
        if not path.is_dir():
            continue
        try:
            errors.extend(f"{path.name}: {e}" for e in validate_profile(path))
        except (KeyError, TypeError, OSError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: {exc}")
    for path in (ROOT / "argocd").glob("*.yaml"):
        app = load(path)
        if app["spec"]["source"].get("directory") != {"recurse": False}:
            errors.append(f"{path.name}: expected explicit plain-directory source")
        if "automated" in app["spec"].get("syncPolicy", {}) or app["metadata"].get("finalizers"):
            errors.append(f"{path.name}: autosync/cascade must not be enabled")
    for path in (ROOT / "observability").glob("*.yaml"):
        for obj in yaml.safe_load_all(path.read_text()):
            if not obj or not obj.get("apiVersion") or not obj.get("kind"):
                errors.append(f"{path.name}: not a Kubernetes resource")
    return errors


if __name__ == "__main__":
    failures = check()
    print("\n".join(failures) if failures else "Plain-YAML contracts passed (offline; no cluster changes).")
    sys.exit(bool(failures))
