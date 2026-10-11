#!/usr/bin/env python3
"""Prepare, verify and render the recorded PRO6000 evidence; never contact a cluster."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "results/pro6000"
sys.path.insert(0, str(DEST / "tools"))
import gemma_capacity_summary
import qwen_evidence_summary
import render_gemma_capacity
import render_gemma_monitoring
import render_matched_chat
import summarize_matched_chat

LABELS = {"__name__", "device", "engine", "finished_reason", "gpu", "le",
          "model_name", "service", "transfer_type"}
PRIVATE = re.compile(r"[/]Users[/]|https?://[^\s\"]*(?:apiac|deckhouse\.io)|"
                     r"\b(?:192\.168\.|10\.\d+\.\d+\.|172\.(?:1[6-9]|2\d|3[01])\.)\d+|"
                     r"\b(?:pod_uid|claim_uid|gpu_uuid|client-key-data)\b|"
                     r"\b(?:ghp_|github_pat_|glpat-|sk-bf-)[A-Za-z0-9_-]{15,}")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(name):
    return json.loads((DEST / name).read_bytes())


def encoded(value, compact=False):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":") if compact else None,
                       indent=None if compact else 2) + "\n").encode()


def client_names(stage):
    if stage == "qwen":
        return ("qwen-10x256k-output32k-offload160-cold.json",
                "qwen-10x256k-prometheus.json", "qwen-10x256k-summary.json")
    return (f"gemma-{stage}-capacity-sse32k.final.json",
            f"gemma-{stage}-sse32k-final-prometheus-isolated.json",
            f"gemma-{stage}-sse32k-validated-summary.json")


def matched_svg():
    names = {"raw": "gemma-matched-chat-final.json", "summary": "gemma-matched-chat-validated-summary.json"}
    return render_matched_chat.render(read(names["raw"]), read(names["summary"]),
                                      {key: sha((DEST / name).read_bytes()) for key, name in names.items()})


def prepare(source):
    manifest = {"schema_version": 1, "scope": "Recorded direct-runtime experiments; offline export",
                "files": {}}
    files = manifest["files"]

    def save(name, value, transformation, source_path=None, inputs=None, compact=False):
        data = value if isinstance(value, bytes) else encoded(value, compact)
        if len(data) >= 5_000_000:
            raise ValueError("Public artifact exceeds review limit: " + name)
        if PRIVATE.search(data.decode()):
            raise ValueError("Private identifiers in " + name)
        (DEST / name).write_bytes(data)
        row = {"sha256": sha(data), "bytes": len(data), "transformation": transformation}
        if source_path is not None:
            row.update(source_file=source_path.name, source_sha256=sha(source_path.read_bytes()))
        if inputs:
            row["inputs"] = inputs
        files[name] = row

    for stage in ("a", "b", "qwen"):
        directory = source / "qwen-evidence-reviewed.mkbVfC" if stage == "qwen" else source
        client_name, monitor_name, summary_name = client_names(stage)
        client_path, monitor_path = directory / client_name, directory / monitor_name
        original = json.loads(client_path.read_bytes())
        client = {key: original[key] for key in ("run_id", "ignore_eos", "passes")}
        save(client_name, client, "Whitelist run_id, ignore_eos, passes; drop embedded metrics.", client_path)
        monitor = json.loads(monitor_path.read_bytes())
        for series in monitor["series"].values():
            for row in series["data"]:
                if not set(row["labels"]) <= LABELS:
                    raise ValueError("Unexpected monitoring labels")
        save(monitor_name, monitor, "Compact JSON only; preserve every timestamp, sample, unit and label.",
             monitor_path, compact=True)
        if stage == "qwen":
            summary = qwen_evidence_summary.summarize(client, monitor)
        else:
            summary = gemma_capacity_summary.summarize(client, monitor, stage)
        # Original source hashes establish provenance; public hashes enable offline verification.
        summary["source_sha256"] = {"client": sha(client_path.read_bytes()),
                                    "monitor": sha(monitor_path.read_bytes())}
        summary["public_source_sha256"] = {"client": files[client_name]["sha256"],
                                           "monitor": files[monitor_name]["sha256"]}
        save(summary_name, summary, "Recomputed with the included strict validator from public inputs.",
             directory / summary_name, [client_name, monitor_name])

    name = "gemma-matched-chat-final.json"
    path = source / name
    report = json.loads(path.read_bytes())
    for wave in report["waves"]:
        for key in ("metrics_before", "metrics_after"):
            wave.pop(key, None)
    save(name, report, "Remove only wave metrics_before/metrics_after; preserve responses, token hashes and timings.", path)
    summary = summarize_matched_chat.summarize(report)
    summary["source_sha256"] = sha(path.read_bytes())
    summary["public_source_sha256"] = files[name]["sha256"]
    save("gemma-matched-chat-validated-summary.json", summary,
         "Recomputed from the sanitized report with the included strict validator.",
         source / "gemma-matched-chat-validated-summary.json", [name])

    name = "gemma-b-offload-replay-final.json"
    save(name, (source / name).read_bytes(), "Unchanged one-token diagnostic; no endpoint or private identifier.", source / name)

    path = source / "gemma-gpu-attribution-final.json"
    original = json.loads(path.read_bytes())
    mapping = {"scope": original["scope"], "verified_at_utc": original["verified_at_utc"],
               "bindings": [{key: row[key] for key in ("model", "prometheus_gpu", "prometheus_device")}
                            for row in original["bindings"]],
               "prometheus": {key: original["prometheus"][key] for key in
                              ("range_start_unix", "range_end_unix", "step_seconds", "limit")},
               "limit": "Public mapping omits node, hardware identifiers and object identities; it records allocation, not exclusive process attribution."}
    save("gemma-gpu-mapping.json", mapping,
         "Keep model-to-GPU-index mapping, verification time and interval; remove query, hardware and object identifiers.", path)

    for stage in ("a", "b"):
        client_name, monitor_name, _ = client_names(stage)
        client, monitor = read(client_name), read(monitor_name)
        hashes = {"client": files[client_name]["sha256"], "monitor": files[monitor_name]["sha256"],
                  "mapping": files["gemma-gpu-mapping.json"]["sha256"]}
        for suffix, svg in (
                ("sse32k-capacity", render_gemma_capacity.render(client, monitor, stage, hashes)),
                ("capacity-monitoring", render_gemma_monitoring.render(client, monitor, stage, mapping, hashes))):
            name = f"gemma-{stage}-{suffix}.svg"
            save(name, svg.encode(), "Regenerated from public samples and client timings; source hashes refer to public files.",
                 source / name, [client_name, monitor_name, "gemma-gpu-mapping.json"])

    name = "qwen-capacity-monitoring.svg"
    subprocess.run([sys.executable, str(DEST / "tools/render_qwen_evidence.py"),
                    "--client", str(DEST / client_names("qwen")[0]), "--monitor", str(DEST / client_names("qwen")[1]),
                    "--output", str(DEST / name)], check=True)
    save(name, (DEST / name).read_bytes(), "Regenerated from public samples; renderer changed only to accept explicit input/output paths.",
         source / "qwen-evidence-reviewed.mkbVfC" / name, list(client_names("qwen")[:2]))

    save("gemma-matched-chat-ttft.svg", matched_svg().encode(),
         "Validated public client TTFT; median and min/max of ten responses per profile and wave; separate explicit axes.",
         inputs=["gemma-matched-chat-final.json", "gemma-matched-chat-validated-summary.json"])

    for path in sorted((DEST / "tools").glob("*.py")):
        if path.name == "render_matched_chat.py":
            files["tools/" + path.name] = {"sha256": sha(path.read_bytes()),
                                          "transformation": "Project renderer for the validated public matched-chat report; no private runtime input."}
            continue
        origin = source / path.name
        if path.name.startswith("qwen_") or path.name == "render_qwen_evidence.py":
            origin = source / "qwen-evidence-reviewed.mkbVfC" / path.name.replace("render_qwen_evidence", "render-qwen-evidence")
        files["tools/" + path.name] = {"sha256": sha(path.read_bytes()), "source_file": origin.name,
                                      "source_sha256": sha(origin.read_bytes()),
                                      "transformation": "Unchanged validator/renderer, except Qwen renderer accepts explicit paths."}
    (DEST / "manifest.json").write_bytes(encoded(manifest))
    verify()


def verify():
    manifest = read("manifest.json")
    for name, record in manifest["files"].items():
        data = (DEST / name).read_bytes()
        if sha(data) != record["sha256"]:
            raise ValueError("Artifact checksum mismatch: " + name)
        if not name.endswith(".png") and PRIVATE.search(data.decode()):
            raise ValueError("Private identifiers in " + name)
    for stage in ("a", "b", "qwen"):
        client, monitor, summary = client_names(stage)
        actual = (qwen_evidence_summary.summarize(read(client), read(monitor)) if stage == "qwen"
                  else gemma_capacity_summary.summarize(read(client), read(monitor), stage))
        saved = read(summary)
        for key in ("source_sha256", "public_source_sha256"):
            saved.pop(key)
        if actual != saved:
            raise ValueError("Summary mismatch: " + stage)
    saved = read("gemma-matched-chat-validated-summary.json")
    for key in ("source_sha256", "public_source_sha256"):
        saved.pop(key)
    if saved != summarize_matched_chat.summarize(read("gemma-matched-chat-final.json")):
        raise ValueError("Matched-chat summary mismatch")
    if (DEST / "gemma-matched-chat-ttft.svg").read_text() != matched_svg():
        raise ValueError("Matched-chat figure does not match public response timings")
    print("Verified public artifact hashes, three completed capacity runs and all 60 matched responses.")


def png(chrome):
    manifest = read("manifest.json")
    for path in sorted(DEST.glob("*.svg")):
        output = path.with_suffix(".png")
        svg = ET.fromstring(path.read_bytes())
        width, height = int(svg.attrib["width"]), int(svg.attrib["height"])
        with tempfile.TemporaryDirectory(prefix="pro6000-evidence-chrome-") as profile:
            subprocess.run([str(chrome), "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                            "--disable-background-networking", "--user-data-dir=" + profile,
                            f"--window-size={width},{height}", "--screenshot=" + str(output), path.as_uri()],
                           check=True, capture_output=True, timeout=60)
        manifest["files"][output.name] = {"sha256": sha(output.read_bytes()),
                                         "bytes": output.stat().st_size, "inputs": [path.name],
                                         "transformation": f"{width}x{height} headless Chrome screenshot of the public SVG using an isolated temporary profile."}
    (DEST / "manifest.json").write_bytes(encoded(manifest))
    verify()


def render():
    """Rebuild every SVG from the checked-in public data without private inputs."""
    verify()
    manifest = read("manifest.json")
    for stage in ("a", "b"):
        client_name, monitor_name, _ = client_names(stage)
        hashes = {"client": sha((DEST / client_name).read_bytes()),
                  "monitor": sha((DEST / monitor_name).read_bytes()),
                  "mapping": sha((DEST / "gemma-gpu-mapping.json").read_bytes())}
        client, monitor = read(client_name), read(monitor_name)
        for suffix, svg in (
                ("sse32k-capacity", render_gemma_capacity.render(client, monitor, stage, hashes)),
                ("capacity-monitoring", render_gemma_monitoring.render(client, monitor, stage,
                                                                      read("gemma-gpu-mapping.json"), hashes))):
            (DEST / f"gemma-{stage}-{suffix}.svg").write_text(svg)
    subprocess.run([sys.executable, str(DEST / "tools/render_qwen_evidence.py"),
                    "--client", str(DEST / client_names("qwen")[0]),
                    "--monitor", str(DEST / client_names("qwen")[1]),
                    "--output", str(DEST / "qwen-capacity-monitoring.svg")], check=True)
    (DEST / "gemma-matched-chat-ttft.svg").write_text(matched_svg())
    # Identical public inputs must recreate identical SVG bytes.
    for path in DEST.glob("*.svg"):
        if sha(path.read_bytes()) != manifest["files"][path.name]["sha256"]:
            raise ValueError("Rendered SVG changed: " + path.name)
    print("Reproduced all six SVG figures from public data.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--source-root", type=Path, required=True)
    commands.add_parser("verify")
    commands.add_parser("render")
    png_parser = commands.add_parser("png")
    png_parser.add_argument("--chrome", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.source_root)
    elif args.command == "png":
        png(args.chrome)
    elif args.command == "render":
        render()
    else:
        verify()
