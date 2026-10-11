"""Strict final-report checks; no Kubernetes access and no partial-run promotion."""
import argparse
import hashlib
import json
import math
from pathlib import Path


VARIANTS = {
    'a': {'model': 'selectel-gemma-a', 'input': 134752, 'window': 167520},
    'b': {'model': 'selectel-gemma-b', 'input': 229376, 'window': 262144},
}
METRICS = {
    'output_tokens': ('vllm:generation_tokens_total', 'tokens'),
    'completed_output_tokens': ('vllm:request_generation_tokens_sum', 'tokens'),
    'completed_output_count': ('vllm:request_generation_tokens_count', 'requests'),
    'success_by_reason': ('vllm:request_success_total', 'requests'),
    'running': ('vllm:num_requests_running', 'requests'),
    'waiting': ('vllm:num_requests_waiting', 'requests'),
    'gpu_kv_fraction': ('vllm:kv_cache_usage_perc', 'ratio 0..1'),
    'preemptions': ('vllm:num_preemptions_total', 'events'),
    'spec_accepted': ('vllm:spec_decode_num_accepted_tokens_total', 'tokens'),
    'spec_proposed': ('vllm:spec_decode_num_draft_tokens_total', 'tokens'),
    'offload_transfer': ('vllm:kv_offload_total_bytes_total', 'bytes'),
}
for key, metric in (
    ('ttft', 'time_to_first_token'), ('tpot', 'request_time_per_output_token'),
    ('queue', 'request_queue_time'), ('prefill', 'request_prefill_time'),
    ('decode', 'request_decode_time'), ('e2e', 'e2e_request_latency'),
):
    for suffix in ('sum', 'count', 'bucket'):
        METRICS[key + '_' + suffix] = (
            'vllm:' + metric + '_seconds_' + suffix,
            'seconds histogram' if suffix == 'bucket' else suffix)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize(raw, monitor, variant):
    expected = VARIANTS[variant]
    require(raw.get('ignore_eos') is True, 'Expected forced-length capacity run')
    require(len(raw.get('passes', [])) == 1, 'Expected one final pass, not progress')
    run = raw['passes'][0]
    require(run.get('ok') is True and run.get('sessions') == 10, 'Incomplete pass')
    require(run.get('model') == expected['model'], 'Wrong model')
    require(run.get('input_tokens') == expected['input'] and
            run.get('output_tokens') == 32768, 'Wrong token budget')
    rows = run['results']
    require(len(rows) == 10 and all(type(r['session']) is int for r in rows) and
            {r['session'] for r in rows} == set(range(10)), 'Missing/duplicate session')
    for row in rows:
        require(row.get('ok') is True and row.get('done') is True and
                row.get('finish_reason') == 'length', 'Incomplete stream')
        usage = row['usage']
        require(usage.get('prompt_tokens') == expected['input'] and
                usage.get('completion_tokens') == 32768 and
                usage.get('total_tokens') == expected['window'], 'Wrong token usage')
        require(all(finite(row.get(k)) for k in
                    ('submitted_at', 'completed_at', 'ttft_s', 'elapsed_s')),
                'Invalid client timing')
        require(0 < row['ttft_s'] < row['elapsed_s'] and
                row['completed_at'] > row['submitted_at'] and
                abs(row['completed_at'] - row['submitted_at'] - row['elapsed_s']) < 1,
                'Inconsistent client timing')
        require(not row.get('watchdog_reason'), 'Watchdog interrupted stream')
    start = min(r['submitted_at'] for r in rows)
    end = max(r['completed_at'] for r in rows)
    spread = max(r['submitted_at'] for r in rows) - start
    require(spread < 1, 'Requests not submitted concurrently')
    require(finite(run.get('elapsed_s')) and abs(run['elapsed_s'] - (end - start)) < 1,
            'Wrong run wall time')
    require(monitor.get('service') == expected['model'], 'Wrong monitored service')
    require(monitor['start_unix'] <= start < end <= monitor['end_unix'],
            'Monitoring interval does not cover run')
    step = monitor['step_seconds']
    require(finite(step) and 0 < step <= 15, 'Unexpected monitoring step')

    def samples(name, selector=None, bracket=True):
        require(name in monitor['series'], 'Missing metric: ' + name)
        metric_name, unit = METRICS[name]
        require(monitor['series'][name]['unit'] == unit, 'Wrong unit: ' + name)
        items = monitor['series'][name]['data']
        if selector is not None:
            items = [s for s in items if selector(s['labels'])]
        require(len(items) == 1, 'Expected one engine series: ' + name)
        item = items[0]
        require(item['labels'].get('__name__') == metric_name and
                item['labels'].get('service') == expected['model'] and
                item['labels'].get('model_name') == expected['model'],
                'Wrong metric identity: ' + name)
        values = item['values']
        require(len(values) >= 2 and all(finite(t) and finite(v) and v >= 0
                                        for t, v in values), 'Invalid samples: ' + name)
        require(all(0 < b[0] - a[0] <= step for a, b in zip(values, values[1:])),
                'Sampling gap/order: ' + name)
        if bracket:
            require(values[0][0] <= start and values[-1][0] >= end,
                    'Metric lacks run boundaries: ' + name)
        return values

    def delta(name, selector=None):
        values = samples(name, selector)
        require(all(b[1] >= a[1] for a, b in zip(values, values[1:])),
                'Counter reset: ' + name)
        return values[-1][1] - values[0][1]

    def maximum(name, unit, ceiling):
        require(monitor['series'][name]['unit'] == unit, 'Wrong unit: ' + name)
        values = samples(name)
        require(all(v <= ceiling for _, v in values), 'Gauge out of bounds: ' + name)
        return max(v for _, v in values)

    generated = sum(r['usage']['completion_tokens'] for r in rows)
    require(delta('completed_output_count') == 10, 'Completed-output request count mismatch')
    require(delta('completed_output_tokens') == generated,
            'Completed-request engine/client token mismatch')
    # Both independent counter families must agree with every API response.
    # Padding before the workload must not include earlier diagnostic traffic.
    global_generated = delta('output_tokens')
    require(global_generated == generated, 'Engine/client token mismatch')
    reason_items = monitor['series']['success_by_reason']['data']
    reasons = {s['labels']['finished_reason'] for s in reason_items}
    require({'length', 'error', 'abort'} <= reasons, 'Missing termination counters')
    for reason in reasons:
        require(delta('success_by_reason', lambda l: l['finished_reason'] == reason) ==
                (10 if reason == 'length' else 0), 'Unexpected termination: ' + reason)
    summary = {
        'run_id': raw['run_id'], 'variant': variant, 'model': expected['model'],
        'input_tokens_each': expected['input'], 'output_tokens_each': 32768,
        'window_tokens': expected['window'], 'submitted_concurrently': 10,
        'submission_spread_seconds': spread, 'completed': 10,
        'wall_seconds': run['elapsed_s'],
        'aggregate_output_tokens_per_second': generated / run['elapsed_s'],
        'client_output_tokens': generated,
        'completed_request_output_tokens': generated,
        'global_generation_tokens_delta': global_generated,
        'output_counter_reconciled': True,
        'client_ttft_min_seconds': min(r['ttft_s'] for r in rows),
        'client_ttft_max_seconds': max(r['ttft_s'] for r in rows),
        'client_ttft_mean_seconds': sum(r['ttft_s'] for r in rows) / 10,
        'sampled_max_running': maximum('running', 'requests', 10),
        'sampled_max_waiting': maximum('waiting', 'requests', 10),
        'sampled_max_gpu_kv_fraction': maximum('gpu_kv_fraction', 'ratio 0..1', 1),
        'monitoring_step_seconds': step, 'preemptions': delta('preemptions'),
        'synthetic_ignore_eos': True,
        'traffic_path': 'direct vLLM API; not WebUI or Bifrost',
        'comparison_limit': 'A and B have different input/window sizes: no speedup claim from this pair.',
        'gpu_scope': 'Both models ran simultaneously: node GPU graphs are not per-service attribution.',
        'timing_semantics': 'Engine wall-clock phases can include scheduling/preemption delays; mean TPOT is the mean of per-request decode/(output_tokens-1).',
        'requests': [
            {k: row[k] for k in ('session', 'ttft_s', 'elapsed_s', 'submitted_at', 'completed_at')}
            for row in sorted(rows, key=lambda r: r['session'])],
    }
    for name in ('ttft', 'tpot', 'queue', 'prefill', 'decode', 'e2e'):
        require(delta(name + '_count') == 10, 'Wrong histogram count: ' + name)
        summary['engine_mean_' + name + '_seconds'] = delta(name + '_sum') / 10
    # Each completed request has exactly 32768 generated tokens. The pinned
    # vLLM metric defines request TPOT as decode/(generated_tokens - 1).
    require(math.isclose(delta('decode_sum'), delta('tpot_sum') * 32767,
                         rel_tol=1e-6, abs_tol=1e-3),
            'Inconsistent decode/TPOT sums')
    for name in ('ttft', 'e2e', 'tpot'):
        buckets = monitor['series'][name + '_bucket']['data']
        bounds = [float(s['labels']['le']) for s in buckets if s['labels']['le'] != '+Inf']
        require(bool(bounds) and all(finite(b) for b in bounds), 'No finite buckets')
        bound = max(bounds)
        count = delta(name + '_count')
        require(delta(name + '_bucket', lambda l: l['le'] == '+Inf') == count,
                'Infinity bucket/count mismatch')
        overflow = count - delta(name + '_bucket', lambda l: float(l['le']) == bound)
        require(0 <= overflow <= count, 'Invalid histogram overflow')
        summary[name + '_histogram_upper_bound_seconds'] = bound
        summary[name + '_histogram_overflow_count'] = overflow
    if variant == 'b':
        accepted, proposed = delta('spec_accepted'), delta('spec_proposed')
        require(proposed > 0 and 0 <= accepted <= proposed, 'Invalid speculative counters')
        summary['speculative_acceptance_fraction'] = accepted / proposed
        summary['native_offload_bytes_in_monitoring_interval'] = {
            direction: delta('offload_transfer', lambda l, d=direction: l['transfer_type'] == d)
            for direction in ('GPU_to_CPU', 'CPU_to_GPU')}
        summary['offload_counter_sample_bounds_unix'] = {}
        for direction in ('GPU_to_CPU', 'CPU_to_GPU'):
            values = samples('offload_transfer', lambda l, d=direction: l['transfer_type'] == d)
            summary['offload_counter_sample_bounds_unix'][direction] = [values[0][0], values[-1][0]]
        summary['offload_limit'] = 'Counter deltas include monitoring padding before/after the client run, not its exact boundaries. Transfers do not measure stored-cache occupancy; a separate replay verifies reuse.'
    else:
        summary['offload_limit'] = 'Baseline configuration has no KV offload; missing metrics are not filled with zero.'
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', choices=VARIANTS, required=True)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--monitor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    client_bytes, monitor_bytes = args.client.read_bytes(), args.monitor.read_bytes()
    summary = summarize(json.loads(client_bytes), json.loads(monitor_bytes), args.variant)
    summary['source_sha256'] = {
        'client': hashlib.sha256(client_bytes).hexdigest(),
        'monitor': hashlib.sha256(monitor_bytes).hexdigest()}
    with args.output.open('x') as out:
        json.dump(summary, out, ensure_ascii=False, indent=2, allow_nan=False)
    print('Validated completed Gemma capacity evidence:', args.output)


if __name__ == '__main__':
    main()
