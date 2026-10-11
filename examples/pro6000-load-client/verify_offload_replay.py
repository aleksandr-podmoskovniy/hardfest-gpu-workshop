"""Replay up to three completed cold-test prefixes; never flush or alter caches.

This is a one-token diagnostic, NOT another capacity/latency benchmark and NOT
the WebUI output limit. Execution requires a successful complete capacity report
and an idle runtime. No Kubernetes or workload configuration writes are made.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.request

FILLER = ' The observatory records temperature and pressure. These are synthetic measurements for a load test.'


def checkpoint(path, evidence):
    """Keep an atomic, readable checkpoint even if the process is killed later."""
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as output:
        json.dump(evidence, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def capture_metrics(record, fetch, save):
    """Persist completed responses before a fallible post-response scrape."""
    save()
    try:
        record['metrics_after'] = fetch()
    except Exception as exc:
        record['metrics_after_error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        save()


def wait_restore(before, fetch, deadline_s=15, clock=time.monotonic, sleep=time.sleep):
    """Allow bounded propagation of the native transfer counter."""
    deadline = clock() + deadline_s
    while True:
        after = fetch()
        delta = restore_delta(before, after)
        if delta > 0 or clock() >= deadline:
            return {'values': after, 'cpu_to_gpu_bytes_delta': delta}
        sleep(min(2, max(0, deadline - clock())))


def replay_plan(document):
    if not document.get('ignore_eos') or len(document.get('passes', [])) != 1:
        raise ValueError('Expected one completed synthetic capacity pass')
    run_id = document['run_id']
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('Unexpected run ID')
    result = document['passes'][0]
    if (result.get('model'), result.get('input_tokens'), result.get('output_tokens'),
            result.get('sessions'), result.get('ok')) != ('selectel-gemma-b', 229376, 32768, 10, True):
        raise ValueError('Not the successful full Gemma B capacity pass')
    rows = result.get('results', [])
    if len(rows) != 10 or {r.get('session') for r in rows} != set(range(10)):
        raise ValueError('All ten unique sessions are required')
    for row in rows:
        usage = row.get('usage') or {}
        if not row.get('ok') or not row.get('done') or row.get('finish_reason') != 'length':
            raise ValueError('Incomplete or failed capacity request')
        if (usage.get('prompt_tokens'), usage.get('completion_tokens')) != (229376, 32768):
            raise ValueError('Token count differs from capacity requirement')
        if not isinstance(row.get('completed_at'), (float, int)):
            raise ValueError('Missing completion time')
    # Recent prefixes are more likely to remain in CPU cache. Skip the latest
    # two, which may still occupy GPU cache. This is a candidate heuristic,
    # never evidence of a restore: CPU_to_GPU counter growth must prove it.
    ordered = sorted(rows, key=lambda row: row['completed_at'], reverse=True)
    return {'run_id': run_id, 'model': result['model'], 'input_tokens': 229376,
            'candidate_sessions': [row['session'] for row in ordered[2:5]]}


def metric_values(text):
    values = {'running': 0.0, 'waiting': 0.0, 'cpu_to_gpu_bytes': 0.0,
              'cpu_counter_present': False}
    present = set()
    for line in text.splitlines():
        if line.startswith('#') or not line.strip():
            continue
        if not line.startswith(('vllm:num_requests_running{', 'vllm:num_requests_waiting{',
                                'vllm:kv_offload_total_bytes_total{')):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        key, value = fields[0], float(fields[1])
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid runtime metric')
        if key.startswith('vllm:num_requests_running{'):
            values['running'] += value
            present.add('running')
        elif key.startswith('vllm:num_requests_waiting{'):
            values['waiting'] += value
            present.add('waiting')
        elif key.startswith('vllm:kv_offload_total_bytes_total{') and 'transfer_type="CPU_to_GPU"' in key:
            values['cpu_to_gpu_bytes'] += value
            values['cpu_counter_present'] = True
    if present != {'running', 'waiting'}:
        raise ValueError('Runtime activity metrics are missing; cannot prove idle')
    return values


def require_idle(values):
    if values['running'] != 0 or values['waiting'] != 0:
        raise RuntimeError('Runtime is busy; no diagnostic request submitted')
    if not values['cpu_counter_present']:
        raise RuntimeError('Native CPU_to_GPU byte counter absent')


def restore_delta(before, after):
    if not before['cpu_counter_present'] or not after['cpu_counter_present']:
        raise ValueError('Missing restore counter is not zero')
    difference = after['cpu_to_gpu_bytes'] - before['cpu_to_gpu_bytes']
    if difference < 0:
        raise ValueError('Restore counter reset during probe')
    return difference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capacity-report', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Without this flag only print the replay plan')
    args = parser.parse_args()
    source = args.capacity_report.read_bytes()
    document = json.loads(source)
    plan = replay_plan(document)
    if not args.execute:
        print(json.dumps(plan))
        return
    if args.report.exists():
        raise FileExistsError('Refusing to replace diagnostic evidence')

    def call(path, body=None):
        request = urllib.request.Request(args.url.rstrip('/') + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=600 if body else 10) as response:
            data = response.read()
        return data.decode() if path == '/metrics' else json.loads(data)

    require_idle(metric_values(call('/metrics')))
    if plan['model'] not in {row['id'] for row in call('/v1/models')['data']}:
        raise ValueError('Expected runtime model is absent')
    filler = call('/tokenize', {'model': plan['model'], 'prompt': FILLER,
                               'add_special_tokens': False})['tokens']
    evidence = {'kind': 'one-token CPU KV restore diagnostic',
                'capacity_report_sha256': hashlib.sha256(source).hexdigest(),
                'original_capacity_report_sha256': document.get('source_report_sha256', hashlib.sha256(source).hexdigest()),
                'plan': plan, 'started_at': time.time(), 'attempts': [], 'restore_proved': False}
    save = lambda: checkpoint(args.report, evidence)
    save()
    try:
        for session in plan['candidate_sessions']:
            before = metric_values(call('/metrics'))
            require_idle(before)
            head = call('/tokenize', {'model': plan['model'], 'add_special_tokens': False,
                'prompt': f'Run {plan["run_id"]} session {session}: test {session * 7919 + 103}. '})['tokens']
            count = plan['input_tokens']
            prompt = head + (filler * (count // len(filler) + 1))[:count - len(head)]
            assert len(prompt) == count
            started = time.monotonic()
            reply = call('/v1/completions', {'model': plan['model'], 'prompt': prompt,
                'temperature': 0, 'max_tokens': 1, 'stream': False})
            elapsed = time.monotonic() - started
            usage = reply.get('usage', {})
            attempt = {'session': session, 'usage': usage, 'elapsed_s': elapsed, 'before': before}
            evidence['attempts'].append(attempt)
            save()
            if reply.get('error') or usage.get('prompt_tokens') != count or usage.get('completion_tokens') != 1:
                raise RuntimeError('Unexpected one-token diagnostic response')
            capture_metrics(attempt, lambda: wait_restore(before, lambda: metric_values(call('/metrics'))), save)
            delta = attempt['metrics_after']['cpu_to_gpu_bytes_delta']
            if delta > 0:
                evidence['restore_proved'] = True
                break
    except Exception as exc:
        evidence['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        evidence['ended_at'] = time.time()
        save()
    print(json.dumps({'restore_proved': evidence['restore_proved'], 'attempts': len(evidence['attempts'])}))
    if not evidence['restore_proved']:
        raise SystemExit('No RAM restore observed; do not claim offload reload works')


if __name__ == '__main__':
    main()
