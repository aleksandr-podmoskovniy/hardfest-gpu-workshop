#!/usr/bin/env python3
"""Print a Markdown comparison; never merge runs into a fabricated aggregate p95."""
import argparse
import json
import pathlib


def fmt(value):
    return "—" if value is None else f"{value:.3f}"


def table(reports):
    lines = ["| Run | C | OK / all | Queue mean, s | Prefill mean, s | Client TTFT p95, s | Output tok/s |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for report in reports:
        s, t = report["summary"], report.get("server_timings", {})
        label = str(report.get("label", "unnamed")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {label} | {report['concurrency']} | {s['successful']} / {s['requests']} | "
                     f"{fmt(t.get('queue_mean_s'))} | {fmt(t.get('prefill_mean_s'))} | "
                     f"{fmt(s.get('client_ttft_p95_s'))} | {fmt(s.get('output_tokens_per_s'))} |")
    keys = {(r["model"], r["dataset_sha256"], r.get("dataset_offset", 0), r["concurrency"], r["summary"]["requests"],
             json.dumps(r.get("sampling"), sort_keys=True)) for r in reports}
    if len(keys) > 1:
        lines += ["", "WARNING: differing model/dataset/concurrency/request count/sampling; not a controlled A/B comparison."]
    lines += ["", "Each row is one run. Cold/repeat state, runtime and physical hardware still require the accompanying snapshot."]
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=pathlib.Path, default=pathlib.Path("results/raw"))
    args = p.parse_args()
    reports = [json.loads(path.read_text()) for path in sorted(args.directory.glob("bench-*.json"))]
    if not reports:
        p.error("No benchmark reports; run a real experiment first")
    print(table(reports))
