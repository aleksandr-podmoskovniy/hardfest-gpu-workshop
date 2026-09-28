import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER = "hardfest-gpu-workshop"
STAGES = {
    "a": ("hf-gemma-a", "gemma-a", "gemma"),
    "b-tuned": ("hf-gemma-b", "gemma-b-tuned", "gemma"),
    "b-cache": ("hf-gemma-b", "gemma-b-cache", "gemma"),
    "b-spec": ("hf-gemma-b", "gemma-b-spec", "gemma"),
    "tp2": ("hf-qwen-tp2", "qwen-tp2", "qwen"),
    "embed-mig": ("hf-embed-mig", None, "embedding"),
    "embed-mps": ("hf-embed-mps", None, "embedding"),
    "rerank-mps": ("hf-rerank-mps", None, "reranker"),
}
READABLE = {"nodes", "deviceclasses", "gpuclasses", "gpupools", "gpus",
            "resourceslices", "pods", "deployments", "resourceclaims", "pvc", "events"}
GLOBAL = {"nodes", "deviceclasses", "gpuclasses", "gpupools", "gpus", "resourceslices"}


def read_json(path):
    return json.loads(pathlib.Path(path).read_text())


def validate_site(site, stage=None):
    required = ["kubeconfig", "context", "expected_server", "namespace"]
    if stage:
        small = stage in {"embed-mig", "embed-mps", "rerank-mps"}
        required += ["mig_node" if small else "h100_node",
                     "mps_device_class" if stage.endswith("mps") else
                     "mig_device_class" if small else "h100_device_class", "image"]
        if stage.endswith("mps"):
            required += ["mps_memory_limit"]
        model_keys = [STAGES[stage][2]] + (["assistant"] if stage == "b-spec" else [])
        for key in model_keys:
            for field in ("pvc", "sub_path"):
                value = site["models"][key][field]
                if not value or "REPLACE_" in value or ".." in value.split("/") or value.startswith("/"):
                    raise ValueError(f"Set models.{key}.{field} to a verified value")
        if stage == "rerank-mps" and (not site.get("reranker_config_verified") or not site.get("reranker_profile")):
            raise ValueError("Reranker conversion/API must be verified in rehearsal first")
    for key in required:
        if not site.get(key) or "REPLACE_" in str(site[key]):
            raise ValueError(f"Set {key} in the private site file")
    if site["namespace"] != "hardfest-demo":
        raise ValueError("This edition is confined to namespace hardfest-demo")
    if stage and not re.search(r"@sha256:[0-9a-f]{64}$", site["image"]):
        raise ValueError("Pin runtime image by digest")
    if stage and not 128 <= int(site["context_tokens"]) <= 262144:
        raise ValueError("context_tokens must be in 128..262144 for these profiles")
    if not 1 <= int(site.get("mps_percent", 25)) <= 100:
        raise ValueError("mps_percent must be 1..100")
    if stage and stage.endswith("mps") and not re.fullmatch(r"[1-9][0-9]*(Mi|Gi)", site["mps_memory_limit"]):
        raise ValueError("Use a measured positive integer Mi or Gi MPS memory limit")


def render(site, stage):
    name, profile_name, key = STAGES[stage]
    small = profile_name is None
    if profile_name:
        profile = read_json(ROOT / "manifests/profiles" / (profile_name + ".json"))
        profile["max-model-len"] = int(site["context_tokens"])
    else:
        profile = {"model": f"/models/{key}", "served-model-name": key,
                   "host": "0.0.0.0", "port": 8000, "runner": "pooling",
                   "dtype": "bfloat16", "max-model-len": 8192,
                   "max-num-seqs": 8, "gpu-memory-utilization": 0.75}
        if key == "reranker":
            # Model-specific conversion must come from a tested runtime recipe.
            profile.update(site.get("reranker_profile", {}))
    ns = site["namespace"]
    labels = {"app.kubernetes.io/part-of": OWNER, "app.kubernetes.io/name": name}
    meta = lambda n: {"name": n, "namespace": ns, "labels": labels.copy()}
    device_class = site["mps_device_class"] if stage.endswith("mps") else (
        site["mig_device_class"] if small else site["h100_device_class"])
    exactly = {"deviceClassName": device_class, "allocationMode": "ExactCount",
               "count": 2 if stage == "tp2" else 1}
    devices = {"requests": [{"name": "gpu", "exactly": exactly}]}
    if stage.endswith("mps"):
        exactly["capacity"] = {"requests": {"sharePercent": str(site["mps_percent"])}}
        devices["config"] = [{"requests": ["gpu"], "opaque": {
            "driver": "gpu.deckhouse.io", "parameters": {
                "apiVersion": "resource.gpu.deckhouse.io/v1alpha1", "kind": "MigDeviceConfig",
                "sharing": {"strategy": "MPS", "mpsConfig": {
                    "defaultActiveThreadPercentage": int(site["mps_percent"]),
                    "defaultPinnedDeviceMemoryLimit": site["mps_memory_limit"]}}}}}]
    # A changed immutable DRA spec gets a new template; no forced replacement.
    claim_hash = hashlib.sha256(json.dumps(devices, sort_keys=True).encode()).hexdigest()[:10]
    claim = name + "-" + claim_hash
    keys = [key] + (["assistant"] if stage == "b-spec" else [])
    volumes = [{"name": "profile", "configMap": {"name": name}},
               {"name": "runtime", "emptyDir": {"sizeLimit": "20Gi"}},
               {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "8Gi"}}]
    mounts = [{"name": "profile", "mountPath": "/etc/vllm", "readOnly": True},
              {"name": "runtime", "mountPath": "/runtime"},
              {"name": "shm", "mountPath": "/dev/shm"}]
    for model in keys:
        source = site["models"][model]
        volumes.append({"name": model, "persistentVolumeClaim": {"claimName": source["pvc"], "readOnly": True}})
        mounts.append({"name": model, "mountPath": f"/models/{model}",
                       "subPath": source["sub_path"], "readOnly": True})
    cpu, memory, limit = ("2", "4Gi", "12Gi") if small else ("16", "64Gi", "128Gi")
    if stage == "b-cache":
        memory, limit = "128Gi", "224Gi"
    if stage == "tp2":
        cpu, memory, limit = "24", "96Gi", "200Gi"
    env = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "HF_HOME": "/runtime/huggingface", "VLLM_CACHE_ROOT": "/runtime/vllm",
           "XDG_CACHE_HOME": "/runtime/cache", "TOKENIZERS_PARALLELISM": "false"}
    pod = {"automountServiceAccountToken": False, "terminationGracePeriodSeconds": 120,
           "nodeSelector": {"kubernetes.io/hostname": site["mig_node" if small else "h100_node"]},
           "tolerations": site.get("tolerations", []),
           "resourceClaims": [{"name": "gpu", "resourceClaimTemplateName": claim}],
           "containers": [{"name": "vllm", "image": site["image"], "imagePullPolicy": "IfNotPresent",
               "command": ["vllm", "serve", "--config", "/etc/vllm/profile.json"],
               "ports": [{"name": "http", "containerPort": 8000}],
               "env": [{"name": k, "value": v} for k, v in env.items()],
               "resources": {"requests": {"cpu": cpu, "memory": memory, "ephemeral-storage": "4Gi"},
                   "limits": {"memory": limit, "ephemeral-storage": "24Gi"}, "claims": [{"name": "gpu"}]},
               "volumeMounts": mounts,
               "startupProbe": {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 10, "failureThreshold": 180},
               "readinessProbe": {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 5}}],
           "volumes": volumes}
    if site.get("image_pull_secrets"):
        pod["imagePullSecrets"] = [{"name": n} for n in site["image_pull_secrets"]]
    fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta(name),
        "spec": {"replicas": 0, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {"metadata": {"labels": labels, "annotations": {
                "workshop/profile": stage, "workshop/profile-sha256": fingerprint}}, "spec": pod}}}
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta(name),
         "data": {"profile.json": json.dumps(profile, indent=2)}},
        {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceClaimTemplate", "metadata": meta(claim),
         "spec": {"spec": {"devices": devices}}}, deployment,
        {"apiVersion": "v1", "kind": "Service", "metadata": meta(name),
         "spec": {"selector": {"app.kubernetes.io/name": name},
                  "ports": [{"port": 8000, "targetPort": 8000}], "type": "ClusterIP"}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": meta(name),
         "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}, "policyTypes": ["Ingress"],
                  "ingress": [{"from": [{"podSelector": {}}], "ports": [{"port": 8000, "protocol": "TCP"}]}]}}]}
