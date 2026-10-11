import argparse
import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.request
import urllib.error
import uuid
from probe_transport import (make_opener, bind_session, release_session,
                             abort_session, abort_reason, IdleEngineWatchdog)

p = argparse.ArgumentParser()
p.add_argument('--url', required=True)
p.add_argument('--model', required=True)
p.add_argument('--input-tokens', type=int, default=8192)
p.add_argument('--output-tokens', type=int, default=512)
p.add_argument('--sessions', type=int, default=8)
p.add_argument('--repeat', type=int, default=1)
p.add_argument('--natural', action='store_true')
p.add_argument('--report', help='Private JSON evidence artifact')
p.add_argument('--run-id', default=uuid.uuid4().hex, help='Unique cold prefix; reuse deliberately for warm-cache tests')
p.add_argument('--request-timeout', type=int, default=7200)
p.add_argument('--stream-idle-timeout', type=int, default=90,
               help='Socket read limit; requires vLLM SSE heartbeat at 15s')
p.add_argument('--hold-results', type=int, default=0, help='Keep completed artifact readable by kubectl exec')
args = p.parse_args()
assert args.url.startswith('http://'), 'This private transport is HTTP-only'
assert args.repeat == 1, 'Use distinct Jobs/run IDs for repeat measurements'
assert args.sessions > 0 and args.request_timeout > 0 and args.stream_idle_timeout > 15
opener = make_opener()
reports = []
metric_samples = []
live_results = {}
result_lock = threading.Lock()
checkpoint_lock = threading.Lock()
monitor_stop = threading.Event()

def checkpoint():
    if not args.report:
        return
    with result_lock:
        snapshot = {'run_id': args.run_id, 'model': args.model,
                    'input_tokens': args.input_tokens, 'output_tokens': args.output_tokens,
                    'sessions': args.sessions, 'ignore_eos': True,
                    'updated_at': time.time(), 'results': list(live_results.values()),
                    'transport': {'tcp_keepalive': [60, 20, 3],
                                  'stream_idle_timeout_s': args.stream_idle_timeout,
                                  'total_deadline_s': args.request_timeout},
                    'metrics': list(metric_samples)}
    path = args.report + '.progress.json'
    with checkpoint_lock:
        with open(path + '.tmp', 'w') as artifact:
            json.dump(snapshot, artifact, ensure_ascii=False)
        os.replace(path + '.tmp', path)

def monitor():
    names = ('num_requests_running', 'num_requests_waiting', 'kv_cache_usage_perc',
             'num_preemptions_total', 'kv_offload_total_bytes_total',
             'prefix_cache_hits_total', 'prefix_cache_queries_total',
             'spec_decode_num_accepted_tokens_total',
             'external_prefix_cache_queries_total', 'external_prefix_cache_hits_total',
             'simple_kv_offload_save_outcomes_total', 'simple_kv_offload_load_blocks_total',
             'simple_kv_offload_used_blocks', 'simple_kv_offload_pending_store_blocks',
             'simple_kv_offload_info')
    while not monitor_stop.is_set():
        sample = {'unix_time': time.time()}
        try:
            with urllib.request.urlopen(args.url.rstrip('/') + '/metrics', timeout=5) as response:
                lines = response.read().decode().splitlines()
            sample['metrics'] = {line.rsplit(' ', 1)[0]: float(line.rsplit(' ', 1)[1])
                                 for line in lines if any(line.startswith('vllm:' + n + '{') for n in names)}
        except Exception as exc:
            sample['error'] = str(exc)
        metric_samples.append(sample)
        checkpoint()
        monitor_stop.wait(10)

def call(path, data=None, timeout=60):
    req = urllib.request.Request(args.url.rstrip('/') + path,
                                 None if data is None else json.dumps(data).encode(),
                                 {'Content-Type': 'application/json'})
    return opener.open(req, timeout=timeout)

if args.natural:
    with call('/v1/chat/completions', {'model': args.model, 'messages': [
        {'role': 'user', 'content': 'Ответь по-русски: чем prefix caching отличается от KV-cache offload в RAM? Дай три коротких пункта и закончи словом ГОТОВО.'}],
        'max_tokens': args.output_tokens, 'temperature': 0}, timeout=args.request_timeout) as response:
        result = json.load(response)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.report:
        with open(args.report, 'x') as artifact:
            json.dump({'ignore_eos': False, 'requested_max_tokens': args.output_tokens,
                       'model': args.model, 'response': result}, artifact, ensure_ascii=False, indent=2)
    raise SystemExit(0)

with call('/tokenize', {'model': args.model, 'prompt': ' The observatory records temperature and pressure. These are synthetic measurements for a load test.', 'add_special_tokens': False}) as response:
    filler = json.load(response)['tokens']

prompts = []
for i in range(args.sessions):
    with call('/tokenize', {'model': args.model, 'prompt': f'Run {args.run_id} session {i}: test {i * 7919 + 103}. ', 'add_special_tokens': False}) as response:
        head = json.load(response)['tokens']
    prompts.append(head + (filler * (args.input_tokens // len(filler) + 1))[:args.input_tokens - len(head)])
assert all(len(x) == args.input_tokens for x in prompts)

def one(i, barrier):
    bind_session(i)
    barrier.wait()
    start = time.monotonic()
    submitted_at = time.time()
    first = None
    usage = None
    finish = None
    done = False
    text_chars = 0
    last_content = None
    heartbeats = 0
    first_heartbeat = None
    with result_lock:
        live_results[i] = {'session': i, 'submitted_at': submitted_at, 'state': 'waiting'}
    try:
        with call('/v1/completions', {'model': args.model, 'prompt': prompts[i],
                  'max_tokens': args.output_tokens, 'temperature': 0, 'ignore_eos': True,
                  'stream': True, 'stream_options': {'include_usage': True}}, timeout=args.stream_idle_timeout) as response:
            for raw in response:
                if raw.startswith(b':'):
                    heartbeats += 1
                    first_heartbeat = first_heartbeat or time.monotonic() - start
                    with result_lock:
                        live_results[i].update(heartbeats=heartbeats,
                                               first_heartbeat_s=first_heartbeat,
                                               last_heartbeat_at=time.time())
                    continue
                if not raw.startswith(b'data: '):
                    continue
                raw = raw[6:].strip()
                if raw == b'[DONE]':
                    done = True
                    break
                item = json.loads(raw)
                if item.get('error'):
                    raise RuntimeError(str(item['error']))
                for choice in item.get('choices', []):
                    piece = choice.get('text') or ''
                    if piece and first is None:
                        first = time.monotonic() - start
                        with result_lock:
                            live_results[i].update(state='streaming', ttft_s=first)
                    text_chars += len(piece)
                    if piece:
                        last_content = time.monotonic() - start
                    finish = choice.get('finish_reason') or finish
                usage = item.get('usage') or usage
        ok = bool(not abort_reason(i) and done and finish == 'length' and usage and usage.get('prompt_tokens') == args.input_tokens and usage.get('completion_tokens') == args.output_tokens)
        return {'session': i, 'ok': ok, 'ttft_s': first, 'elapsed_s': time.monotonic() - start,
                'submitted_at': submitted_at, 'completed_at': time.time(),
                'usage': usage, 'finish_reason': finish, 'done': done, 'text_chars': text_chars,
                'last_content_s': last_content,
                'heartbeats': heartbeats, 'first_heartbeat_s': first_heartbeat,
                'watchdog_reason': abort_reason(i),
                'client_mean_decode_s_per_output_token_estimate':
                    (last_content - first) / max(1, usage['completion_tokens'] - 1)
                    if first is not None and last_content is not None and usage else None}
    except Exception as exc:
        return {'session': i, 'ok': False, 'elapsed_s': time.monotonic() - start,
                'submitted_at': submitted_at, 'completed_at': time.time(), 'error': str(exc),
                'ttft_s': first, 'last_content_s': last_content, 'usage': usage,
                'finish_reason': finish, 'done': done, 'text_chars': text_chars,
                'heartbeats': heartbeats, 'first_heartbeat_s': first_heartbeat,
                'watchdog_reason': abort_reason(i)}
    finally:
        release_session(i)

all_ok = True
monitor_thread = threading.Thread(target=monitor, daemon=True)
monitor_thread.start()
for attempt in range(args.repeat):
    print(json.dumps({'event': 'start', 'pass': attempt, 'model': args.model,
                      'input_tokens': args.input_tokens, 'output_tokens': args.output_tokens,
                      'sessions': args.sessions, 'run_id': args.run_id, 'unix_time': time.time()}), flush=True)
    barrier = threading.Barrier(args.sessions)
    started = time.monotonic()
    idle_watchdog = IdleEngineWatchdog()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.sessions) as pool:
        pending = {pool.submit(one, i, barrier) for i in range(args.sessions)}
        results = []
        while pending:
            complete, pending = concurrent.futures.wait(pending, timeout=10,
                                                       return_when=concurrent.futures.FIRST_COMPLETED)
            for task in complete:
                r = task.result()
                results.append(r)
                with result_lock:
                    live_results[r['session']] = {'state': 'completed', **r}
                checkpoint()
                print(json.dumps({'event': 'result', 'pass': attempt, **r}, ensure_ascii=False), flush=True)
            reason = None
            if time.monotonic() - started >= args.request_timeout:
                reason = 'total_request_deadline_exceeded'
            elif idle_watchdog.observe(metric_samples[-1] if metric_samples else {}, time.time(), bool(pending)):
                reason = 'engine_idle_120s_but_client_requests_incomplete'
            if reason:
                with result_lock:
                    unfinished = [i for i, row in live_results.items() if row.get('state') != 'completed']
                for i in unfinished:
                    abort_session(i, reason)
                print(json.dumps({'event': 'watchdog_abort', 'reason': reason, 'sessions': unfinished}), flush=True)
            if not complete:
                print(json.dumps({'event': 'progress', 'pass': attempt, 'remaining': len(pending),
                                  'elapsed_s': round(time.monotonic() - started, 1)}), flush=True)
    good = all(r['ok'] for r in results)
    all_ok &= good
    reports.append({'model': args.model, 'pass': attempt,
                    'input_tokens': args.input_tokens, 'output_tokens': args.output_tokens,
                    'sessions': args.sessions, 'ok': good, 'results': results,
                    'elapsed_s': time.monotonic() - started})
    print(json.dumps({'event': 'summary', 'pass': attempt, 'ok': good,
                      'completed': len(results), 'elapsed_s': time.monotonic() - started}), flush=True)
monitor_stop.set()
monitor_thread.join(timeout=6)
if args.report:
    with open(args.report, 'x') as artifact:
        json.dump({'run_id': args.run_id, 'ignore_eos': True, 'passes': reports, 'metrics': metric_samples}, artifact, ensure_ascii=False, indent=2)
if args.hold_results:
    print(json.dumps({'event': 'artifact_ready', 'ok': all_ok, 'path': args.report}), flush=True)
    time.sleep(args.hold_results)
raise SystemExit(0 if all_ok else 1)
