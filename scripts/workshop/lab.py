"""Read-only remote helpers. Outputs stay in ignored local directories."""
import json
import pathlib
import subprocess
from .manifests import ROOT, STAGES, read_json, render


def private_output(value):
    path = pathlib.Path(value).expanduser().resolve()
    allowed = [(ROOT / ".local").resolve(), (ROOT / "results/raw").resolve()]
    if not any(parent in path.parents for parent in allowed):
        raise ValueError("Save private output below .local/ or results/raw/")
    if path.exists():
        raise ValueError("Output already exists; choose a new run name, no overwrite")
    return path


def init_site(target):
    path = private_output(target)
    site = read_json(ROOT / "config/site.example.json")
    # Defaults intentionally do not silently select the operator's current cluster.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as file:
        json.dump(site, file, indent=2)
    path.chmod(0o600)
    return path


def require_deployment(k, stage):
    obj = k.get("deployment", STAGES[stage][0])
    if not obj:
        raise ValueError("Deployment is absent")
    k.ownership(obj)
    if not obj.get("status", {}).get("readyReplicas"):
        raise ValueError("Deployment is not ready")
    return obj


def dataset(k, site, stage, args):
    require_deployment(k, stage)
    if STAGES[stage][2] not in {"gemma", "qwen"}:
        raise ValueError("Dataset requires a generative model tokenizer")
    if not 0 < args.input_fraction < 1 or args.output_tokens < 1 or args.documents < 1:
        raise ValueError("Use 0 < input-fraction < 1 and positive lengths/documents")
    output = private_output(args.out)
    context = int(site["context_tokens"])
    input_tokens = int(context * args.input_fraction)
    if input_tokens + args.output_tokens + 8 > context:
        raise ValueError("Input and output do not fit the common context")
    source = (ROOT / "scripts/make_workload.py").read_text()
    raw = k.ns(["exec", "-i", "deployment/" + STAGES[stage][0], "-c", "vllm", "--",
                "python", "-", "--tokenizer", "/models/" + STAGES[stage][2],
                "--input-tokens", str(input_tokens), "--output-tokens", str(args.output_tokens),
                "--context", str(context), "--documents", str(args.documents)], data=source, timeout=600)
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != args.documents or any("messages" not in r for r in rows):
        raise ValueError("Remote tokenizer returned unexpected data; nothing saved")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as file:
        file.write(raw)
    return output


def model_info(k, stage):
    require_deployment(k, stage)
    code = '''import json, pathlib, sys
p=pathlib.Path(sys.argv[1])/"config.json"
c=json.loads(p.read_text())
fields=("architectures","model_type","num_hidden_layers","num_attention_heads","num_key_value_heads","head_dim","hidden_size","sliding_window","layer_types","max_position_embeddings","torch_dtype","dtype","quantization_config")
out={"config":{k:c[k] for k in fields if k in c}}
if "text_config" in c: out["text_config"]={k:c["text_config"][k] for k in fields if k in c["text_config"]}
print(json.dumps(out,indent=2))
'''
    return k.ns(["exec", "-i", "deployment/" + STAGES[stage][0], "-c", "vllm", "--",
                 "python", "-", "/models/" + STAGES[stage][2]], data=code)


def snapshot(k, site, stage, out):
    path = private_output(out)
    name = STAGES[stage][0]
    deployment = k.get("deployment", name)
    if not deployment:
        raise ValueError("Deployment is absent")
    k.ownership(deployment)
    pods = json.loads(k.ns(["get", "pods", "-l", "app.kubernetes.io/name=" + name, "-o", "json"]))
    claims = {}
    for pod in pods["items"]:
        for claim in pod.get("status", {}).get("resourceClaimStatuses", []):
            claim_name = claim.get("resourceClaimName")
            if claim_name:
                claims[claim_name] = k.get("resourceclaim", claim_name)
    data = {"deployment": deployment, "pods": pods, "claims": claims,
            "configmap": k.get("configmap", name), "expected_manifest": render(site, stage)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as file:
        json.dump(data, file, indent=2)
    return path
