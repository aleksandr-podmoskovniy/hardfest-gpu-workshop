"""Validate one completed capacity run before deriving public graph numbers."""
import hashlib
import math


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize(raw, monitor):
    require(len(raw['passes']) == 1 and raw['ignore_eos'] is True, 'Expected one forced-length pass')
    run = raw['passes'][0]
    require(run['ok'] is True and run['sessions'] == 10, 'Incomplete capacity run')
    require(run['model'] == 'qwen3-8-flash-next-nvfp4', 'Wrong client model')
    require(run['input_tokens'] == 229376 and run['output_tokens'] == 32768, 'Wrong window')
    rows = run['results']
    require(len(rows) == 10 and {r['session'] for r in rows} == set(range(10)), 'Missing/duplicate session')
    for row in rows:
        require(row['ok'] is True and row['done'] is True and row['finish_reason'] == 'length', 'Unfinished stream')
        require(row['usage']['prompt_tokens'] == run['input_tokens'], 'Wrong input usage')
        require(row['usage']['completion_tokens'] == run['output_tokens'], 'Short output')
        require(row['usage']['total_tokens'] == 262144, 'Wrong total usage')
        require(0 < row['ttft_s'] < row['elapsed_s'], 'Invalid client timings')
        require(row['submitted_at'] < row['completed_at'], 'Invalid client timestamps')
    start = min(r['submitted_at'] for r in rows)
    end = max(r['completed_at'] for r in rows)
    spread = max(r['submitted_at'] for r in rows) - start
    require(spread < 1, 'Requests were not submitted concurrently')
    require(math.isfinite(run['elapsed_s']) and abs(run['elapsed_s'] - (end - start)) < 1, 'Wrong wall time')
    require(monitor['service'] == 'selectel-qwen38-tp2', 'Wrong monitored service')
    require(monitor['start_unix'] <= start < end <= monitor['end_unix'], 'Monitoring does not cover run')
    series = monitor['series']

    def samples(name, selector=None, bracket=False):
        items = series[name]['data']
        if selector is not None:
            items = [s for s in items if selector(s['labels'])]
        require(len(items) == 1, 'Expected one engine series: ' + name)
        item = items[0]
        require(item['labels'].get('service') == monitor['service'] and
                item['labels'].get('model_name') == run['model'], 'Wrong metric identity: ' + name)
        values = item['values']
        require(len(values) >= 2 and all(v is not None and math.isfinite(t) and math.isfinite(v)
                                       for t, v in values), 'Missing metric samples: ' + name)
        require(all(a[0] < b[0] for a, b in zip(values, values[1:])), 'Unordered samples: ' + name)
        if bracket:
            require(values[0][0] <= start and values[-1][0] >= end, 'Counter lacks run boundaries: ' + name)
        return values

    def delta(name, selector=None):
        values = samples(name, selector, bracket=True)
        require(all(b[1] >= a[1] for a, b in zip(values, values[1:])), 'Counter reset: ' + name)
        return values[-1][1] - values[0][1]

    def mean(name):
        require(delta(name + '_count') == len(rows), 'Wrong histogram sample count: ' + name)
        return delta(name + '_sum') / len(rows)

    def plot_points(values, name, maximum=None):
        require(len(values) >= 2 and all(v is not None and math.isfinite(t) and math.isfinite(v)
                                       for t, v in values), 'Missing plot samples: ' + name)
        require(all(0 <= v and (maximum is None or v <= maximum) for _, v in values), 'Plot range: ' + name)
        require(values[0][0] <= start and values[-1][0] >= end, 'Plot lacks run boundaries: ' + name)
        step = monitor['step_seconds']
        require(0 < step <= 15 and all(0 < b[0]-a[0] <= step for a, b in zip(values, values[1:])),
                'Plot sampling gap: ' + name)

    for name, unit, maximum in (('output_rate', 'tokens/s', None), ('gpu_kv_fraction', 'ratio 0..1', 1),
                                ('running', 'requests', 10), ('waiting', 'requests', 10)):
        require(series[name]['unit'] == unit, 'Wrong plot unit: ' + name)
        plot_points(samples(name, bracket=True), name, maximum)
    gpu = series['gpu_utilization']
    require(gpu['unit'] == 'percent 0..100' and len(gpu['data']) == 2, 'GPU plot shape')
    require({s['labels'].get('gpu') for s in gpu['data']} == {'0', '1'}, 'GPU plot identities')
    for item in gpu['data']:
        labels = item['labels']
        require(labels.get('__name__') == 'DCGM_FI_DEV_GPU_UTIL' and
                labels.get('device') == 'nvidia' + labels['gpu'], 'GPU plot device/metric')
        plot_points(item['values'], 'gpu_utilization', 100)

    generated = sum(r['usage']['completion_tokens'] for r in rows)
    require(delta('output_tokens') == generated, 'Client/engine token mismatch')
    reasons = {s['labels']['finished_reason'] for s in series['success_by_reason']['data']}
    require({'length', 'error', 'abort'} <= reasons, 'Missing termination counters')
    for reason in reasons:
        count = delta('success_by_reason', lambda labels: labels['finished_reason'] == reason)
        require(count == (len(rows) if reason == 'length' else 0), 'Unexpected termination: ' + reason)
    accepted, proposed = delta('spec_accepted'), delta('spec_proposed')
    require(0 <= accepted <= proposed and proposed > 0, 'Invalid speculative counters')
    summary = {
        'run_id': raw['run_id'], 'model': 'Qwen3.8 Flash Next NVFP4',
        'hardware': '2 × RTX PRO 6000 Blackwell 96 GB',
        'submitted_concurrently': len(rows), 'submission_spread_seconds': spread,
        'completed': len(rows), 'input_tokens_each': run['input_tokens'],
        'output_tokens_each': run['output_tokens'], 'window_tokens': 262144,
        'synthetic_ignore_eos': raw['ignore_eos'], 'finish_reason': 'length',
        'wall_seconds': run['elapsed_s'], 'aggregate_output_tokens_per_second': generated / run['elapsed_s'],
        'client_ttft_min_seconds': min(r['ttft_s'] for r in rows),
        'client_ttft_max_seconds': max(r['ttft_s'] for r in rows),
        'client_ttft_mean_seconds': sum(r['ttft_s'] for r in rows) / len(rows),
        'speculative_acceptance_fraction': accepted / proposed,
        'observed_max_running': max(v for _, v in samples('running')),
        'observed_max_waiting': max(v for _, v in samples('waiting')),
        'monitoring_step_seconds': monitor['step_seconds'],
        'preemptions': delta('preemptions'),
        'traffic_path': 'direct vLLM API capacity test; not WebUI or Bifrost',
        'performance_caveat': 'Forced synthetic continuation; do not extrapolate throughput to natural chat.',
        'gpu_scope': 'whole node during exclusive Qwen run, not per-service attribution',
        'timing_semantics': 'Engine prefill and decode are wall-clock phases including preemption delays; TPOT is the request mean decode/(output_tokens-1).',
        'interpretation': 'Capacity stress passes, but queue latency is not suitable for live interactive chat.',
    }
    for name in ('ttft', 'tpot', 'queue', 'prefill', 'decode', 'e2e'):
        summary['engine_mean_' + name + '_seconds'] = mean(name)
    for name in ('ttft', 'e2e', 'tpot'):
        bounds = [float(s['labels']['le']) for s in series[name + '_bucket']['data'] if s['labels']['le'] != '+Inf']
        require(bool(bounds), 'No finite histogram bounds: ' + name)
        bound = max(bounds)
        overflow = delta(name + '_count') - delta(name + '_bucket', lambda labels: float(labels['le']) == bound)
        require(overflow >= 0, 'Inconsistent histogram buckets')
        summary[name + '_histogram_upper_bound_seconds'] = bound
        summary[name + '_histogram_overflow_count'] = overflow
    # This lazy counter first appears at 147. Absence is NOT a zero baseline.
    restored = samples('offload_restore_blocks')
    require(all(b[1] >= a[1] for a, b in zip(restored, restored[1:])), 'Restore counter reset')
    summary['offload_restore_counter_first_observed'] = restored[0][1]
    summary['offload_restore_counter_last_observed'] = restored[-1][1]
    summary['offload_restore_counter_observed_increase'] = restored[-1][1] - restored[0][1]
    summary['offload_restore_counter_has_pre_run_baseline'] = restored[0][0] <= start
    summary['requests'] = [
        {'session': r['session'], 'ttft_s': r['ttft_s'], 'elapsed_s': r['elapsed_s'],
         'input_tokens': r['usage']['prompt_tokens'], 'output_tokens': r['usage']['completion_tokens']}
        for r in sorted(rows, key=lambda r: r['session'])]
    return summary


def source_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
