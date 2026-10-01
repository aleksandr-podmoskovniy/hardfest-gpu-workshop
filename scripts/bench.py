#!/usr/bin/env python3
"""Small closed-loop SSE harness. Client TTFT is NOT server prefill time."""
import argparse
import concurrent.futures
import hashlib
import json
import math
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def events(response):
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield "\n".join(data)


def request(url, model, row, index, timeout, fixed_output=False):
    payload = {"model": model, "messages": row["messages"],
               "max_tokens": row["max_tokens"], "temperature": 0,
               "stream": True, "stream_options": {"include_usage": True}}
    if fixed_output:
        payload.update(ignore_eos=True, min_tokens=row["max_tokens"])
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
        "X-Request-Id": f"hardfest-{time.time_ns()}-{index}"})
    start = time.perf_counter()
    first = None
    first_content = None
    preview = ""
    content_hash = hashlib.sha256()
    chunks = 0
    usage = None
    finish = None
    done = False
    error = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw in events(response):
                if time.perf_counter() - start > timeout:
                    raise TimeoutError("Overall request deadline exceeded")
                if raw == "[DONE]":
                    done = True
                    break
                event = json.loads(raw)
                if event.get("error"):
                    raise ValueError(str(event["error"])[:300])
                usage = event.get("usage") or usage
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    if any(delta.get(k) for k in ("content", "reasoning_content", "reasoning", "tool_calls")):
                        first = first if first is not None else time.perf_counter() - start
                        chunks += 1
                    if isinstance(delta.get("content"), str) and delta["content"]:
                        first_content = first_content if first_content is not None else time.perf_counter() - start
                        content_hash.update(delta["content"].encode())
                        preview = (preview + delta["content"])[:2000]
                    finish = choice.get("finish_reason") or finish
        if not done or first is None:
            raise ValueError("Incomplete SSE or no output; not counted as successful")
    except (OSError, ValueError, TimeoutError) as exc:
        error = str(exc)[:300]
    return {"index": index, "ttft_s": first, "first_content_s": first_content,
            "content_preview": preview, "content_sha256": content_hash.hexdigest(),
            "e2e_s": time.perf_counter() - start,
            "usage": usage, "finish_reason": finish, "stream_chunks": chunks, "error": error}


def summarize(rows, elapsed):
    ok = [r for r in rows if r["error"] is None]
    token_counts = [(r.get("usage") or {}).get("completion_tokens") for r in ok]
    complete_usage = bool(ok) and all(isinstance(n, int) for n in token_counts)
    return {"requests": len(rows), "successful": len(ok), "errors": len(rows) - len(ok),
            "wall_s": elapsed, "client_ttft_mean_s": sum(r["ttft_s"] for r in ok) / len(ok) if ok else None,
            "client_ttft_p95_s": percentile([r["ttft_s"] for r in ok], .95),
            "first_content_p95_s": percentile([r["first_content_s"] for r in ok if r.get("first_content_s") is not None], .95),
            "e2e_p95_s": percentile([r["e2e_s"] for r in ok], .95),
            "output_tokens_per_s": sum(token_counts) / elapsed if complete_usage else None,
            "usage_complete": complete_usage,
            "note": "Closed-loop client timing; SSE chunks are not tokens. No queue/prefill inference."}


def metrics_snapshot(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/metrics", timeout=15) as response:
            return response.read().decode(), None
    except OSError as exc:
        return None, str(exc)[:200]


def counter_samples(text):
    samples = {}
    for line in (text or "").splitlines():
        match = re.match(r'^(vllm:request_(?:queue|prefill)_time_seconds_(?:sum|count))(\{.*\})?\s+([^\s]+)', line)
        if match:
            samples[(match[1], match[2] or "")] = float(match[3])
    return samples


def server_timings(before, after):
    if before is None or after is None:
        return {"note": "Metrics unavailable; queue/prefill not inferred from client TTFT"}
    a, b = counter_samples(before), counter_samples(after)
    out = {"note": "Counter deltas for all completions on this endpoint; isolate the run from other traffic"}
    for phase in ("queue", "prefill"):
        prefix = "vllm:request_" + phase + "_time_seconds_"
        sums = [key for key in b if key[0] == prefix + "sum"]
        keys = sums + [(prefix + "count", labels) for _, labels in sums]
        valid = bool(sums) and all(key in a and key in b and b[key] >= a[key] for key in keys)
        if not valid:
            out[phase + "_mean_s"] = None
            out[phase + "_note"] = "missing series or counter reset"
            continue
        count = sum(b[(prefix + "count", labels)] - a[(prefix + "count", labels)] for _, labels in sums)
        total = sum(b[key] - a[key] for key in sums)
        out[phase + "_mean_s"] = total / count if count else None
        out[phase + "_completed"] = count
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--dataset", type=pathlib.Path, required=True)
    p.add_argument("--model", default="gemma-4-31b")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--requests", type=int, default=8)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--label", default="unnamed")
    p.add_argument("--cold", action="store_true", help="reject reused dataset rows; does NOT clear server cache")
    p.add_argument("--metrics", action="store_true", help="capture endpoint metrics before/after, without inventing p95")
    p.add_argument("--fixed-output", action="store_true", help="vLLM-only synthetic test: ignore EOS and generate max_tokens; not a quality test")
    p.add_argument("--offset", type=int, default=0, help="start at this dataset row, for X -> Y... -> X experiments")
    p.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path("results/raw"))
    args = p.parse_args()
    parsed = urllib.parse.urlparse(args.url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        p.error("Use a loopback port-forward, not an unauthenticated public endpoint")
    if not 1 <= args.concurrency <= 50 or not 1 <= args.requests <= 10000 or args.timeout < 1:
        p.error("concurrency: 1..50, requests: 1..10000, positive timeout")
    data = args.dataset.read_bytes()
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    if not rows or any(not r.get("messages") or not isinstance(r.get("max_tokens"), int) or r["max_tokens"] < 1 for r in rows):
        p.error("Dataset must contain messages and positive max_tokens per JSONL row")
    if not 0 <= args.offset < len(rows):
        p.error("offset must point to an existing dataset row")
    rows = rows[args.offset:]
    if args.cold and (args.requests > len(rows) or len({json.dumps(r["messages"], sort_keys=True) for r in rows}) < len(rows)):
        p.error("Cold series requires enough unique message rows; this flag does not reset GPU/CPU cache")
    before, before_error = metrics_snapshot(args.url) if args.metrics else (None, None)
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(request, args.url, args.model, rows[i % len(rows)], i, args.timeout, args.fixed_output)
                   for i in range(args.requests)]
        result = [f.result() for f in futures]
    elapsed = time.perf_counter() - start
    after, after_error = metrics_snapshot(args.url) if args.metrics else (None, None)
    report = {"schema": 1, "label": args.label, "dataset_sha256": hashlib.sha256(data).hexdigest(),
              "dataset_rows": len(rows), "dataset_offset": args.offset, "model": args.model, "concurrency": args.concurrency,
              "sampling": {"temperature": 0, "fixed_output": args.fixed_output}, "summary": summarize(result, elapsed),
              "server_timings": server_timings(before, after),
              "metrics": {"before": before, "after": after, "errors": [before_error, after_error]},
              "requests": result}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output = args.out_dir / f"bench-{time.time_ns()}.json"
    with output.open("x") as file:
        json.dump(report, file, indent=2)
    print(json.dumps(report["summary"], indent=2))
    if args.metrics:
        print(json.dumps(report["server_timings"], indent=2))
    print(f"Raw report: {output}")
    raise SystemExit(1 if report["summary"]["errors"] else 0)


if __name__ == "__main__":
    main()
