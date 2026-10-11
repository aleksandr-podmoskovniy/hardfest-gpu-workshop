"""Validate the completed six-wave chat experiment and summarize client timings."""
import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path

from gemma_capacity_summary import finite, require


def summarize(report):
    require(report.get('kind') == 'matched direct-runtime chat comparison', 'Wrong report kind')
    require(not report.get('error') and finite(report.get('ended_at')) and
            finite(report.get('started_at')) and report['ended_at'] > report['started_at'],
            'Report incomplete or failed')
    require(report.get('output_cap') == 32768 and report.get('ignore_eos') is False and
            report.get('sessions_per_wave') == 10, 'Wrong experiment settings')
    for kind in ('short', 'long'):
        counts = report['input_tokens'][kind]
        hashes = report['matched_token_ids_sha256'][kind]
        require(len(counts) == len(hashes) == 10 and
                all(re.fullmatch('[0-9a-f]{64}', h) for h in hashes), 'Missing token identity')
        require(all(isinstance(n, int) and n > 0 and
                    (kind != 'long' or 97000 <= n <= 98304) for n in counts), 'Wrong input budget')
    expected = [('selectel-gemma-' + stage, kind, iteration)
                for kind, iteration in [('short', 'cold'), ('long', 'cold'), ('long', 'warm')]
                for stage in ('a', 'b')]
    waves = report.get('waves', [])
    require([(w.get('model'), w.get('kind'), w.get('iteration')) for w in waves] == expected,
            'Expected exactly six ordered waves')
    output = []
    previous_end = report['started_at']
    for wave in waves:
        rows = wave.get('results', [])
        require(wave.get('ok') is True and wave.get('responses_ok') is True and
                len(rows) == 10 and all(type(r.get('session')) is int for r in rows) and
                {r.get('session') for r in rows} == set(range(10)),
                'Incomplete or duplicate responses')
        rows = sorted(rows, key=lambda row: row['session'])
        for row in rows:
            usage = row.get('usage') or {}
            require(row.get('ok') is True and row.get('done') is True and
                    row.get('answer_correct') is True and row.get('finish_reason') == 'stop' and
                    not row.get('error'), 'Unsuccessful response')
            require(usage.get('prompt_tokens') == report['input_tokens'][wave['kind']][row['session']]
                    and isinstance(usage.get('completion_tokens'), int) and
                    0 < usage['completion_tokens'] <= 32768 and
                    usage.get('total_tokens') == usage['prompt_tokens'] + usage['completion_tokens'],
                    'Response token budget mismatch')
            timings = [row.get(k) for k in ('submitted_at', 'client_ttft_generated_text_s',
                       'client_ttft_visible_answer_s', 'elapsed_s')]
            require(all(finite(t) and t > 0 for t in timings), 'Invalid timing')
            submitted, generated, visible, elapsed = timings
            require(generated <= visible <= elapsed and submitted >= previous_end - .01 and
                    submitted + elapsed <= report['ended_at'] + .1, 'Inconsistent timing')
        starts = [r['submitted_at'] for r in rows]
        spread = max(starts) - min(starts)
        require(spread < 1, 'Requests not submitted concurrently')
        previous_end = max(r['submitted_at'] + r['elapsed_s'] for r in rows)
        item = {k: wave[k] for k in ('model', 'kind', 'iteration')}
        item.update(completed=10, submitted_span_s=spread,
                    input_tokens_each=report['input_tokens'][wave['kind']],
                    output_tokens_each=[r['usage']['completion_tokens'] for r in rows],
                    wall_seconds=previous_end - min(starts))
        for field in ('client_ttft_generated_text_s', 'client_ttft_visible_answer_s', 'elapsed_s'):
            values = [r[field] for r in rows]
            item[field] = {'min': min(values), 'median': statistics.median(values),
                           'max': max(values), 'mean': statistics.mean(values)}
        output.append(item)
    return {'run_id': report['run_id'], 'completed_responses': 60, 'waves': output,
            'limits': ['Direct runtime, not WebUI/Bifrost E2E.',
                       'Ten requests share a prefix inside each wave; not ten unrelated documents.',
                       'Sequential waves, one run: not a general model-speed guarantee.',
                       'Short exact-answer task; natural EOS, 32768 cap is not actual answer length.',
                       'Client time to visible answer includes queue, prefill and any reasoning.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    result = summarize(json.loads(raw))
    result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    with args.output.open('x') as out:
        json.dump(result, out, ensure_ascii=False, indent=2, allow_nan=False)
    print('Validated all 60 chat responses:', args.output)
