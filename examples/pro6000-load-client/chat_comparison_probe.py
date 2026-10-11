"""Matched A/B chat workload; 32K output cap, ordinary EOS, no forced generation.

Only execute after both complete capacity reports exist. This supplements, never
replaces, the maximum-window tests. Long comparison uses the same ~96K-token
input on both models, within a 128K total budget, so their delays are comparable.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.request
import uuid

from verify_offload_replay import capture_metrics, checkpoint, metric_values

FILLER = ' Архивная запись: датчик работает штатно, температура стабильна, обслуживание не требуется.\n'


def check_capacity(data, model, input_tokens):
    if len(data.get('passes', [])) != 1:
        raise ValueError('Missing final capacity report')
    report = data['passes'][0]
    if (report.get('model'), report.get('sessions'), report.get('ok')) != (model, 10, True):
        raise ValueError('Capacity model, concurrency or completion mismatch')
    if (report.get('input_tokens'), report.get('output_tokens')) != (input_tokens, 32768):
        raise ValueError('Capacity metadata token budget mismatch')
    rows = report.get('results', [])
    if len(rows) != 10 or {row.get('session') for row in rows} != set(range(10)):
        raise ValueError('Expected ten unique completed capacity requests')
    for row in rows:
        usage = row.get('usage') or {}
        if not row.get('ok') or not row.get('done') or row.get('finish_reason') != 'length':
            raise ValueError('Capacity request was not successfully completed')
        if (usage.get('prompt_tokens'), usage.get('completion_tokens')) != (input_tokens, 32768):
            raise ValueError('Capacity token count mismatch')


def ensure_idle(values):
    if values['running'] != 0 or values['waiting'] != 0:
        raise RuntimeError('Runtime busy; comparison will not interfere')


def matched_tokens(a, b, kind):
    if not isinstance(a, list) or not a or a != b:
        raise ValueError('Token ID sequences differ or are absent; comparison invalid')
    if kind == 'long' and not 97000 <= len(a) <= 98304:
        raise ValueError('Long input does not fit the matched 128K total budget')
    return len(a), hashlib.sha256(json.dumps(a, separators=(',', ':')).encode()).hexdigest()


def messages(run_id, repeat, session):
    content = (f'Проверка {run_id}. Контрольный код установки: ORBIT-42.\n'
               + FILLER * repeat + f'\nСессия {session}. Какой контрольный код установки? '
               'Ответь одной строкой: «Код: <код>. ГОТОВО». Не пересказывай архив.')
    return [{'role': 'user', 'content': content}]


def consume_stream(lines, started, now=time.monotonic):
    first_generated = first_visible = None
    usage = finish = None
    done = False
    text_parts = []
    for raw in lines:
        if not raw.startswith(b'data: '):
            continue
        payload = raw[6:].strip()
        if payload == b'[DONE]':
            done = True
            break
        event = json.loads(payload)
        if event.get('error'):
            raise RuntimeError('Gateway/runtime returned a stream error')
        usage = event.get('usage') or usage
        for choice in event.get('choices', []):
            delta = choice.get('delta') or {}
            visible = delta.get('content') or ''
            reasoning = delta.get('reasoning_content') or delta.get('reasoning') or ''
            generated = (visible if isinstance(visible, str) else '') or (
                reasoning if isinstance(reasoning, str) else '')
            if generated and first_generated is None:
                first_generated = now() - started
            if isinstance(visible, str) and visible:
                if first_visible is None:
                    first_visible = now() - started
                text_parts.append(visible)
            finish = choice.get('finish_reason') or finish
    text = ''.join(text_parts)
    return {'done': done, 'finish_reason': finish, 'usage': usage,
            'client_ttft_generated_text_s': first_generated,
            'client_ttft_visible_answer_s': first_visible,
            'elapsed_s': now() - started, 'answer_tail': text[-256:],
            'answer_correct': 'ORBIT-42' in text and text.rstrip().rstrip('.').endswith('ГОТОВО'),
            'ok': bool(done and finish == 'stop' and usage and first_visible is not None
                       and 'ORBIT-42' in text and text.rstrip().rstrip('.').endswith('ГОТОВО'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for stage in ('a', 'b'):
        parser.add_argument('--url-' + stage, required=True)
        parser.add_argument('--capacity-report-' + stage, type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    capacity_hashes, original_capacity_hashes = {}, {}
    for stage, tokens in [('a', 134752), ('b', 229376)]:
        raw = getattr(args, 'capacity_report_' + stage).read_bytes()
        document = json.loads(raw)
        check_capacity(document, 'selectel-gemma-' + stage, tokens)
        capacity_hashes[stage] = hashlib.sha256(raw).hexdigest()
        original_capacity_hashes[stage] = document.get('source_report_sha256', capacity_hashes[stage])
    if not args.execute:
        print('Capacity gates passed. Plan: short10, matched-long10 cold/warm; 32768 output cap; ordinary EOS.')
        return
    if args.report.exists():
        raise FileExistsError('Refusing to overwrite comparison results')
    endpoints = {stage: getattr(args, 'url_' + stage).rstrip('/') for stage in ('a', 'b')}

    def call(stage, path, body=None):
        req = urllib.request.Request(endpoints[stage] + path,
            None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read().decode() if path == '/metrics' else json.load(response)

    def tokenize(stage, content):
        result = call(stage, '/tokenize', {'model': 'selectel-gemma-' + stage,
                                          'messages': content, 'add_generation_prompt': True})
        tokens = result.get('tokens')
        if not isinstance(tokens, list) or not tokens or result.get('count', len(tokens)) != len(tokens):
            raise ValueError('Tokenizer did not return a complete token ID sequence')
        return tokens

    for stage in ('a', 'b'):
        ensure_idle(metric_values(call(stage, '/metrics')))
        if 'selectel-gemma-' + stage not in {m['id'] for m in call(stage, '/v1/models')['data']}:
            raise ValueError('Expected runtime is absent')

    run_id = uuid.uuid4().hex
    # Calibrate on A; verify identical tokenization on B before inference.
    empty = len(tokenize('a', messages(run_id, 0, 0)))
    one = len(tokenize('a', messages(run_id, 1, 0)))
    width = one - empty
    if width <= 0:
        raise ValueError('Unexpected tokenizer calibration')
    target = 98272  # Leave a small margin for session-suffix token boundaries.
    repeat = max(1, (target - empty) // width)
    for _ in range(10):
        tokens = len(tokenize('a', messages(run_id, repeat, 0)))
        if target - width <= tokens <= target:
            break
        repeat = max(1, repeat + (target - tokens) // width)
    else:
        raise ValueError('Unable to calibrate matched long prompt')
    prepared = {kind: [messages(run_id + ('-short' if kind == 'short' else ''),
                               0 if kind == 'short' else repeat, i) for i in range(10)]
                for kind in ('short', 'long')}
    counts, token_hashes = {}, {}
    for kind, prompts in prepared.items():
        counts[kind] = []
        token_hashes[kind] = []
        for prompt in prompts:
            a, b = tokenize('a', prompt), tokenize('b', prompt)
            length, digest = matched_tokens(a, b, kind)
            counts[kind].append(length)
            token_hashes[kind].append(digest)

    evidence = {'kind': 'matched direct-runtime chat comparison', 'started_at': time.time(),
        'run_id': run_id, 'capacity_report_sha256': capacity_hashes, 'sessions_per_wave': 10,
        'original_capacity_report_sha256': original_capacity_hashes,
        'output_cap': 32768, 'ignore_eos': False, 'input_tokens': counts,
        'matched_token_ids_sha256': token_hashes, 'waves': [],
        'notes': ['Maximum configured windows remain unchanged.',
                  'This matched workload is NOT the maximum-window capacity proof.',
                  'Cold means prefix absent before the wave; ten requests share the prefix within the wave.',
                  'This measures shared-prefix batching/APC, NOT ten independent cold prefills.',
                  'Client TTFT distinguishes generated reasoning from the first visible answer.',
                  'Short exact-answer task does not measure general answer quality.']}
    save = lambda: checkpoint(args.report, evidence)
    save()

    def wave(stage, kind, iteration):
        ensure_idle(metric_values(call(stage, '/metrics')))
        before = call(stage, '/metrics')
        result = {'model': 'selectel-gemma-' + stage, 'kind': kind, 'iteration': iteration,
                  'results': [], 'ok': False, 'metrics_before': before}
        evidence['waves'].append(result)
        save()
        barrier = threading.Barrier(10)

        def request(i):
            barrier.wait()
            started = time.monotonic()
            submitted_at = time.time()
            try:
                body = {'model': 'selectel-gemma-' + stage, 'messages': prepared[kind][i],
                    'max_tokens': 32768, 'temperature': 0, 'stream': True,
                    'stream_options': {'include_usage': True}}
                req = urllib.request.Request(endpoints[stage] + '/v1/chat/completions',
                    json.dumps(body).encode(), {'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=3600) as response:
                    result = consume_stream(response, started)
                if (result.get('usage') or {}).get('prompt_tokens') != counts[kind][i]:
                    result.update(ok=False, error='Prompt count differs from matched tokenizer preparation')
                return {'session': i, 'submitted_at': submitted_at, **result}
            except Exception as exc:
                return {'session': i, 'submitted_at': submitted_at, 'ok': False,
                        'error': type(exc).__name__ + ': ' + str(exc)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(request, i) for i in range(10)]
            for future in concurrent.futures.as_completed(futures):
                result['results'].append(future.result())
                save()
        result['responses_ok'] = all(row['ok'] for row in result['results'])
        capture_metrics(result, lambda: call(stage, '/metrics'), save)
        result['ok'] = result['responses_ok']
        save()
        return result

    try:
        for kind, iteration in [('short', 'cold'), ('long', 'cold'), ('long', 'warm')]:
            for stage in ('a', 'b'):
                result = wave(stage, kind, iteration)
                print(json.dumps({'stage': stage, 'kind': kind, 'iteration': iteration, 'ok': result['ok']}), flush=True)
                # Stop at a failed wave rather than silently hiding it in averages.
                if not result['ok']:
                    raise RuntimeError('Chat wave failed acceptance; inspect evidence')
    except Exception as exc:
        evidence['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        evidence['ended_at'] = time.time()
        save()


if __name__ == '__main__':
    main()
