"""Supplementary matched chat/mixed-load experiment; not a maximum-window test.

Runs one model, preserves all responses and token hashes. Use the same run-id and
document for Base, Cache and Tune. Compare only matching hashes afterward.
CPU Job only; never changes a runtime or resets its cache.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.request

from verify_offload_replay import checkpoint, metric_values
from probe_transport import make_opener, open_exact, bind_session, release_session, abort_session, abort_reason

FILLER = '\nАрхив наблюдений учебного магазина: сервис проверен, новые события не зарегистрированы.\n'
QUESTION = ('Разбери инцидент оплаты по документу: вероятная причина, подтверждения, '
            'последствия и безопасный порядок проверки заказов. Дай восемь коротких пунктов, '
            'до 250 слов. Не пересказывай архив наблюдений и не придумывай отсутствующие факты.')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Runtime redirect refused')

def wait_idle(fetch, timeout=20, clock=time.monotonic, sleep=time.sleep):
    """Wait only for post-response metric propagation; never clear requests."""
    deadline = clock() + timeout
    while True:
        metrics = fetch()
        activity = metric_values(metrics)
        if not activity['running'] and not activity['waiting']:
            return metrics
        if clock() >= deadline:
            raise RuntimeError('Runtime busy; leave current requests untouched')
        sleep(min(1, max(0, deadline - clock())))

def valid_usage(usage):
    if not isinstance(usage, dict):
        return False
    if any(type(usage.get(k)) is not int for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
        return False
    return (usage['prompt_tokens'] > 0 and 0 < usage['completion_tokens'] <= 32768
            and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])

def content(run_id, kind, session, repeat, document):
    # Shared prefix for cold/warm; independent prefixes in the mixed wave.
    discriminator = f'-request-{session}' if kind == 'mixed' else ''
    return (f'Учебный прогон {run_id}-{kind}{discriminator}.\n' + document + FILLER * repeat
            + f'\nВопрос {session}. ' + QUESTION)

def consume(response, start):
    first = visible = last = None
    text, reasoning = [], []
    usage = finish = None
    done = False
    error = None
    try:
        for raw in response:
            if time.monotonic() - start > 1800:
                raise TimeoutError('Request exceeded 30 minutes')
            if not raw.startswith(b'data:'):
                continue
            payload = raw[5:].strip()
            if payload == b'[DONE]':
                done = True
                break
            event = json.loads(payload)
            if event.get('error'):
                raise RuntimeError('Stream returned error')
            usage = event.get('usage') or usage
            for choice in event.get('choices', []):
                delta = choice.get('delta', {})
                answer = delta.get('content') or ''
                thought = delta.get('reasoning_content') or delta.get('reasoning') or ''
                if answer or thought:
                    elapsed = time.monotonic() - start
                    first = elapsed if first is None else first
                    last = elapsed
                if answer:
                    visible = elapsed if visible is None else visible
                    text.append(answer)
                if thought:
                    reasoning.append(thought)
                finish = choice.get('finish_reason') or finish
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    return {'done': done, 'finish_reason': finish, 'usage': usage,
            'first_generated_text_s': first, 'first_visible_text_s': visible,
            'last_content_s': last, 'elapsed_s': time.monotonic() - start,
            'answer': ''.join(text), 'reasoning': ''.join(reasoning), 'error': error,
            'ok': bool(not error and done and finish == 'stop' and valid_usage(usage) and text)}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True)
    p.add_argument('--model', choices=['selectel-gemma-a', 'selectel-gemma-cache', 'selectel-gemma-b'], required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--document', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--hold', type=int, default=3600)
    a = p.parse_args()
    if a.report.exists():
        raise FileExistsError('Evidence exists; no overwrite')
    if not a.url.startswith('http://') or not 0 <= a.hold <= 3600:
        raise ValueError('Unexpected private endpoint/hold')
    opener = make_opener()
    def call(path, body=None):
        req = urllib.request.Request(a.url.rstrip('/') + path,
            data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=60) as r:
            return r.read().decode() if path == '/metrics' else json.load(r)
    def idle():
        return wait_idle(lambda: call('/metrics'))
    def tokenize(message):
        v = call('/tokenize', {'model': a.model, 'messages': [{'role': 'user', 'content': message}],
                               'add_generation_prompt': True})
        if not v.get('tokens') or v.get('count', len(v['tokens'])) != len(v['tokens']):
            raise RuntimeError('Incomplete tokenization')
        return v['tokens']
    idle()
    models = call('/v1/models')['data']
    model = next(m for m in models if m['id'] == a.model)
    document = a.document.read_text()
    empty = len(tokenize(content(a.run_id, 'long', 0, 0, document)))
    width = len(tokenize(content(a.run_id, 'long', 0, 1, document))) - empty
    if width <= 0 or empty > 32700:
        raise RuntimeError('Unexpected document/tokenizer')
    repeat = (32700 - empty) // width
    for _ in range(10):
        size = len(tokenize(content(a.run_id, 'long', 0, repeat, document)))
        if 32600 <= size <= 32700:
            break
        repeat = max(0, repeat + (32700 - size) // width)
    else:
        raise RuntimeError('Prompt calibration failed')
    prepared = {}
    evidence = {'run_id': a.run_id, 'model': a.model, 'started_at': time.time(),
        'document_sha256': hashlib.sha256(a.document.read_bytes()).hexdigest(),
        'synthetic_padding': True, 'output_cap': 32768, 'ignore_eos': False,
        'configured_context': model['max_model_len'], 'prompts': {}, 'waves': [],
        'timing_policy': {'request_deadline_seconds': 1800, 'socket_timeout_seconds': 1800,
                          'deadline_action': 'Shutdown the exact request socket; preserve partial data.'},
        'prefix_note': 'First wave uses a new shared prefix; requests within that wave may reuse it. '
                       'Mixed requests have distinct early prefixes. No runtime cache is reset.',
        'scope': 'Matched short/32K-input natural chat and a mixed schedule, NOT maximum-window capacity or exhaustive answer quality.'}
    for kind in ('short', 'long', 'mixed'):
        prepared[kind] = []
        evidence['prompts'][kind] = []
        for i in range(10):
            n = repeat if kind == 'long' or kind == 'mixed' and i < 2 else 0
            message = content(a.run_id, kind, i, n, document)
            tokens = tokenize(message)
            if len(tokens) + 32768 > model['max_model_len']:
                raise RuntimeError('Prompt plus output does not fit actual context')
            prepared[kind].append(message)
            evidence['prompts'][kind].append({'session': i, 'input_tokens': len(tokens),
                'token_ids_sha256': hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()})
    save = lambda: checkpoint(a.report, evidence)
    save()
    failure = None
    try:
        for wave_index, (kind, iteration) in enumerate([('short', 'first'), ('long', 'first'), ('long', 'repeat'), ('mixed', 'first')]):
            before = idle()
            wave = {'kind': kind, 'iteration': iteration, 'started_at': time.time(),
                    'metrics_before': before, 'results': [], 'ok': False}
            evidence['waves'].append(wave)
            save()
            barrier = threading.Barrier(10)
            def request(i):
                barrier.wait(timeout=30)
                delay = 1.0 if kind == 'mixed' and i >= 2 else 0.0
                time.sleep(delay)
                start = time.monotonic()
                submitted = time.time()
                transport_id = wave_index * 10 + i
                bind_session(transport_id)
                deadline = threading.Timer(1800, abort_session, args=(transport_id, 'total_request_deadline_exceeded'))
                deadline.daemon = True
                deadline.start()
                try:
                    body = {'model': a.model, 'messages': [{'role': 'user', 'content': prepared[kind][i]}],
                            'temperature': 0, 'max_tokens': 32768, 'stream': True,
                            'stream_options': {'include_usage': True}}
                    req = urllib.request.Request(a.url.rstrip('/') + '/v1/chat/completions',
                        json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
                    with open_exact(opener, req, timeout=1800) as r:
                        row = consume(r, start)
                    if abort_reason(transport_id):
                        row.update(ok=False, watchdog_reason=abort_reason(transport_id))
                    if (row.get('usage') or {}).get('prompt_tokens') != evidence['prompts'][kind][i]['input_tokens']:
                        row.update(ok=False, validation_error='Prompt count differs from tokenizer')
                    return {'session': i, 'scheduled_delay_s': delay, 'submitted_at': submitted,
                            'completed_at': time.time(), **row}
                except Exception as exc:
                    return {'session': i, 'submitted_at': submitted, 'completed_at': time.time(),
                            'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}
                finally:
                    deadline.cancel()
                    deadline.join(timeout=1)
                    release_session(transport_id)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(request, i) for i in range(10)]
                for future in concurrent.futures.as_completed(futures):
                    wave['results'].append(future.result())
                    save()
            wave['ended_at'] = time.time()
            wave['metrics_after'] = idle()
            wave['ok'] = len(wave['results']) == 10 and all(r['ok'] for r in wave['results'])
            if kind == 'mixed':
                long_rows = [r for r in wave['results'] if r['session'] < 2]
                wave['short_requests_submitted_while_long_in_flight'] = sum(
                    any(long['submitted_at'] <= short['submitted_at'] < long['completed_at'] for long in long_rows)
                    for short in wave['results'] if short['session'] >= 2)
                wave['overlap_note'] = 'Request lifetimes, not proof of simultaneous GPU computation.'
            save()
            print(json.dumps({'kind': kind, 'iteration': iteration, 'completed': len(wave['results']),
                              'ok': wave['ok']}), flush=True)
            if not wave['ok']:
                raise RuntimeError('Incomplete wave; preserve failure and stop')
    except Exception as exc:
        evidence['error'] = type(exc).__name__ + ': ' + str(exc)
        failure = exc
    finally:
        evidence['ended_at'] = time.time()
        save()
        time.sleep(a.hold)
    if failure is not None:
        raise failure

if __name__ == '__main__':
    main()
