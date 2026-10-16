#!/usr/bin/env python3
"""Validate one completed ten-client, 32K-output capacity wave; no network access."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'results/pro6000/tools'))
from gemma_capacity_summary import METRICS


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize(client, monitor):
    require(client.get('ignore_eos') is True, 'Expected forced-output capacity workload')
    require(len(client.get('passes', [])) == 1, 'Final report required; progress is not a result')
    run = client['passes'][0]
    require(run.get('ok') is True and run.get('sessions') == 10, 'Incomplete wave')
    reduced_gemma = (run.get('model') in ('rtx-gemma-base', 'rtx-gemma-cache')
                     and run.get('input_tokens') in (4096, 8192, 16384))
    require(type(run.get('input_tokens')) is int and (run['input_tokens'] >= 32768 or reduced_gemma)
            and run.get('output_tokens') == 32768, 'Not the long-context/32K-output protocol')
    rows = run.get('results', [])
    require(len(rows) == 10 and all(type(r.get('session')) is int for r in rows)
            and {r['session'] for r in rows} == set(range(10)), 'Missing or duplicate client')
    for row in rows:
        require(row.get('ok') is True and row.get('done') is True
                and row.get('finish_reason') == 'length' and not row.get('error')
                and not row.get('watchdog_reason'), 'Stream incomplete or failed')
        usage = row.get('usage', {})
        require(usage.get('prompt_tokens') == run['input_tokens']
                and usage.get('completion_tokens') == 32768
                and usage.get('total_tokens') == run['input_tokens'] + 32768, 'Token count mismatch')
        require(all(finite(row.get(k)) for k in ('ttft_s', 'elapsed_s', 'submitted_at', 'completed_at', 'last_content_s')),
                'Invalid client timing')
        require(0 < row['ttft_s'] <= row['last_content_s'] <= row['elapsed_s']
                and abs(row['completed_at'] - row['submitted_at'] - row['elapsed_s']) < 1,
                'Inconsistent client timing')
    start = min(r['submitted_at'] for r in rows)
    end = max(r['completed_at'] for r in rows)
    require(max(r['submitted_at'] for r in rows) - start < 1, 'Clients not submitted together')
    require(finite(run.get('elapsed_s')) and abs(run['elapsed_s'] - (end - start)) < 1, 'Invalid wave duration')
    require(monitor['start_unix'] <= start < end <= monitor['end_unix'], 'Monitoring lacks boundaries')
    require(0 < monitor['step_seconds'] <= 15, 'Monitoring step too large')

    def samples(name, reason=None):
        metric_name, unit = METRICS[name]
        require(monitor['series'][name]['unit'] == unit, 'Wrong metric unit: ' + name)
        items = monitor['series'][name]['data']
        if reason is not None:
            items = [s for s in items if s['labels'].get('finished_reason') == reason]
        require(len(items) == 1, 'Expected exactly one engine series: ' + name)
        item = items[0]
        require(item['labels'].get('__name__') == metric_name
                and item['labels'].get('service') == monitor['service']
                and item['labels'].get('model_name') == run['model'], 'Wrong metric identity: ' + name)
        values = item['values']
        require(len(values) >= 2 and all(finite(t) and finite(v) and v >= 0 for t, v in values), 'Invalid samples: ' + name)
        require(values[0][0] <= start and values[-1][0] >= end, 'Metric lacks boundaries: ' + name)
        require(all(0 < b[0] - a[0] <= monitor['step_seconds'] for a, b in zip(values, values[1:])), 'Metric gap: ' + name)
        return values

    def delta(name, reason=None):
        values = samples(name, reason)
        require(all(b[1] >= a[1] for a, b in zip(values, values[1:])), 'Counter reset: ' + name)
        return values[-1][1] - values[0][1]

    require(delta('completed_output_count') == 10, 'Wrong completed-request counter')
    require(delta('completed_output_tokens') == 327680 and delta('output_tokens') == 327680,
            'Engine/client output counters do not reconcile')
    reasons = {s['labels']['finished_reason'] for s in monitor['series']['success_by_reason']['data']}
    require({'length', 'error', 'abort'} <= reasons, 'Missing termination counters')
    for reason in reasons:
        require(delta('success_by_reason', reason) == (10 if reason == 'length' else 0), 'Unexpected termination counter')
    out = {
        'run_id': client['run_id'], 'model': run['model'], 'service': monitor['service'],
        'input_tokens_each': run['input_tokens'], 'output_tokens_each': 32768,
        'window_tokens': run['input_tokens'] + 32768,
        'submitted_concurrently': 10, 'completed': 10, 'failed': 0,
        'wall_seconds': run['elapsed_s'], 'aggregate_output_tokens_per_second': 327680 / run['elapsed_s'],
        'synthetic_ignore_eos': True, 'output_counter_reconciled': True,
        'traffic_path': 'Separate CPU client to private vLLM; not WebUI/Bifrost',
        'comparison_limit': 'Repeated synthetic filler with independent session prefixes; not normal chat quality or representative speculative-decoding acceptance.',
        'parallelism_limit': 'Running counts scheduled requests, including prefill. This does not prove that ten full contexts decoded simultaneously.',
        'client_ttft_median_seconds': statistics.median(r['ttft_s'] for r in rows),
        'client_ttft_max_seconds': max(r['ttft_s'] for r in rows),
        'client_mean_decode_seconds_per_token': statistics.mean((r['last_content_s'] - r['ttft_s']) / 32767 for r in rows),
        'preemptions': delta('preemptions'),
        'monitoring_step_seconds': monitor['step_seconds'],
        'requests': [{k: r[k] for k in ('session', 'ttft_s', 'elapsed_s', 'last_content_s', 'submitted_at', 'completed_at', 'usage', 'finish_reason', 'done')}
                     for r in sorted(rows, key=lambda r: r['session'])],
    }
    for name, ceiling in (('running', 10), ('waiting', 10), ('gpu_kv_fraction', 1)):
        values = samples(name)
        require(all(v <= ceiling for _, v in values), 'Gauge out of range: ' + name)
        out['sampled_max_' + name] = max(v for _, v in values)
    for name in ('ttft', 'tpot', 'queue', 'prefill', 'decode', 'e2e'):
        require(delta(name + '_count') == 10, 'Wrong histogram count: ' + name)
        out['engine_mean_' + name + '_seconds'] = delta(name + '_sum') / 10
    require(math.isclose(out['engine_mean_decode_seconds'], out['engine_mean_tpot_seconds'] * 32767,
                         rel_tol=1e-6, abs_tol=1e-3), 'Decode/TPOT counters disagree')
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client', type=Path, required=True)
    p.add_argument('--monitor', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    inputs = {'client': a.client.read_bytes(), 'monitor': a.monitor.read_bytes()}
    result = summarize(*(json.loads(inputs[k]) for k in ('client', 'monitor')))
    result['source_sha256'] = {k: hashlib.sha256(v).hexdigest() for k, v in inputs.items()}
    with a.output.open('x') as target:
        json.dump(result, target, ensure_ascii=False, indent=2, allow_nan=False)
    print('Validated all ten clients, 327680 output tokens and engine counters:', a.output.name)


if __name__ == '__main__':
    main()
