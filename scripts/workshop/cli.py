import argparse
import difflib
import json
import sys
from .manifests import ROOT, STAGES, READABLE, GLOBAL, read_json, render, validate_site
from .cluster import Cluster
from .lab import init_site, dataset, model_info, snapshot

def make_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", default=str(ROOT / ".local/site.json"))
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("init-site")
    s = sub.add_parser("diff")
    s.add_argument("before", choices=STAGES)
    s.add_argument("after", choices=STAGES)
    s = sub.add_parser("dataset")
    s.add_argument("stage", choices=STAGES)
    s.add_argument("--input-fraction", type=float, default=0.5)
    s.add_argument("--output-tokens", type=int, default=2048)
    s.add_argument("--documents", type=int, default=32)
    s.add_argument("--out", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("stage", choices=STAGES)
    s.add_argument("--out", required=True)
    sub.add_parser("model-info").add_argument("stage", choices=STAGES)
    sub.add_parser("preflight")
    sub.add_parser("get").add_argument("resource", choices=sorted(READABLE))
    for action in ("render", "apply", "start", "stop", "logs", "port-forward"):
        s = sub.add_parser(action)
        s.add_argument("stage", choices=STAGES)
        if action in {"apply", "start", "stop"}:
            s.add_argument("--ack", action="store_true", help="explicitly permit this mutation")
        if action == "port-forward":
            s.add_argument("port", type=int)
    return p


def main():
    p = make_parser()
    args = p.parse_args()
    if args.action == "init-site":
        print(init_site(args.site))
        print("Edit private site values before any cluster operation. Nothing deployed.")
        return
    site = read_json(args.site)
    stage = getattr(args, "stage", None)
    if args.action == "diff":
        def lines(stage):
            return render(site, stage)["items"][0]["data"]["profile.json"].splitlines(keepends=True)
        print("".join(difflib.unified_diff(lines(args.before), lines(args.after), fromfile=args.before, tofile=args.after)))
        return
    if args.action == "render":
        print(json.dumps(render(site, stage), indent=2))
        return
    validate_site(site, stage if args.action in {"apply", "start"} else None)
    if args.action in {"apply", "start", "stop"} and not args.ack:
        p.error("Mutation requires --ack; render/preflight are non-mutating")
    k = Cluster(site)
    k.identity()
    if args.action == "dataset":
        print(dataset(k, site, stage, args))
        return
    if args.action == "model-info":
        print(model_info(k, stage))
        return
    if args.action == "snapshot":
        print(snapshot(k, site, stage, args.out))
        return
    if args.action == "preflight":
        for r in ("nodes", "deviceclasses"):
            print(k.run(["get", r, "-o", "wide"]))
        for r in ("pods", "pvc", "resourceclaims"):
            print(k.ns(["get", r, "-o", "wide"]))
        print("Read-only inventory complete. CUDA/NVLink/fit/permissions still require rehearsal.")
        return
    if args.action == "get":
        args_read = ["get", args.resource, "-o", "wide"]
        print(k.run(args_read) if args.resource in GLOBAL else k.ns(args_read))
        return
    name = STAGES[stage][0]
    if args.action == "apply":
        current = k.get("deployment", name)
        if current and current["spec"].get("replicas", 0):
            raise ValueError("Stop the deployment explicitly before replacing its profile")
        pods = json.loads(k.ns(["get", "pods", "-l", "app.kubernetes.io/name=" + name, "-o", "json"]))
        if pods["items"]:
            raise ValueError("Old pods still exist (possibly Terminating); wait before changing the profile")
        bundle = render(site, stage)
        for obj in bundle["items"]:
            k.ownership(k.get(obj["kind"], obj["metadata"]["name"]))
        raw = json.dumps(bundle)
        print(k.ns(["apply", "--dry-run=server", "-f", "-"], data=raw))
        print(k.ns(["apply", "-f", "-"], data=raw))
        print("Applied at replicas=0. API acceptance is not a runtime test.")
        return
    obj = k.get("deployment", name)
    if obj is None:
        raise ValueError("Deployment not found; apply the reviewed stage first")
    k.ownership(obj)
    if args.action == "start":
        if obj["spec"]["template"]["metadata"]["annotations"].get("workshop/profile") != stage:
            raise ValueError("Deployment contains a different profile; stop/apply explicitly")
        expected = next(item for item in render(site, stage)["items"] if item["kind"] == "Deployment")
        desired = expected["spec"]["template"]
        actual = obj["spec"]["template"]
        if (actual["metadata"]["annotations"].get("workshop/profile-sha256") != desired["metadata"]["annotations"]["workshop/profile-sha256"]
                or actual["spec"]["containers"][0]["image"] != desired["spec"]["containers"][0]["image"]
                or actual["spec"]["resourceClaims"] != desired["spec"]["resourceClaims"]):
            raise ValueError("Site/profile differs from applied deployment; review and stop/apply first")
        k.ready_node(stage)
        deployments = json.loads(k.ns(["get", "deployments", "-o", "json"]))["items"]
        incompatible = {"hf-gemma-a", "hf-gemma-b"} if stage == "tp2" else (
            {"hf-qwen-tp2"} if stage in {"a", "b-tuned", "b-cache", "b-spec"} else set())
        if any(d["metadata"]["name"] in incompatible and d["spec"].get("replicas", 0) for d in deployments):
            raise ValueError("Another workshop stage still occupies the H100s; stop it first")
        print(k.ns(["scale", "deployment/" + name, "--replicas=1"]))
        k.ns(["rollout", "status", "deployment/" + name, "--timeout=300s"], stream=True)
    elif args.action == "stop":
        print(k.ns(["scale", "deployment/" + name, "--replicas=0"]))
        k.ns(["wait", "--for=delete", "pod", "-l", "app.kubernetes.io/name=" + name, "--timeout=180s"], stream=True)
    elif args.action == "logs":
        print(k.ns(["logs", "deployment/" + name, "--tail=100"]))
    elif args.action == "port-forward":
        if not 1024 <= args.port <= 65535:
            raise ValueError("Choose an unprivileged local TCP port")
        k.ns(["port-forward", "--address=127.0.0.1", "service/" + name, f"{args.port}:8000"], stream=True)
