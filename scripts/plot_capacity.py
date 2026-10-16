#!/usr/bin/env python3
"""Render measured capacity timelines, rejecting incomplete evidence."""
import argparse
import json
import math
from pathlib import Path
import re

from build_diagrams import Diagram, BLUE, TEAL, AMBER, MUTED, GRAY, LINE
from heavy_capacity import summarize

ROOT = Path(__file__).resolve().parents[1]


def number(value, places=1):
    return f'{value:,.{places}f}'.replace(',', ' ').replace('.', ',')


def describe(result, runtime, hardware='rtx5060'):
    models = {'rtx-qwen35-9b': ('Qwen', 2),
              'rtx-gemma-base': ('Gemma Base', 1),
              'rtx-gemma-cache': ('Gemma Cache', 1),
              'rtx-gemma-tune': ('Gemma Tune', 1)}
    if hardware == 'pro6000':
        models = {'selectel-gemma-a': ('Gemma Base', 1),
                  'selectel-gemma-cache': ('Gemma Cache', 1),
                  'selectel-gemma-b': ('Gemma Tune', 1),
                  'qwen3-8-flash-next-nvfp4': ('Qwen', 2)}
    elif hardware != 'rtx5060':
        raise ValueError('Unknown hardware')
    if result['model'] not in models:
        raise ValueError('Unknown measured model')
    label, count = models[result['model']]
    identities = runtime.get('models', [runtime['model']] if 'model' in runtime else [])
    actual_repositories = {item['repository'] for item in identities}
    expected_repositories = ({'Qwen/Qwen3.5-9B'} if count == 2 else {'google/gemma-4-E2B-it'})
    if result['model'] == 'rtx-gemma-tune':
        expected_repositories.add('google/gemma-4-E2B-it-assistant')
    if hardware == 'pro6000':
        expected_repositories = ({'nvidia/Qwen3.8-Flash-Next-NVFP4'} if count == 2
                                 else {'google/gemma-4-31B-it'})
        if result['model'] == 'selectel-gemma-b':
            expected_repositories.add('google/gemma-4-31B-it-assistant')
    if (actual_repositories != expected_repositories
            or runtime.get('served_model_names') != [result['model']]):
        raise ValueError('Runtime model identity does not match measured result')
    flags = runtime['server_flags_from_actual_pod']
    configured_window = int(flags['--max-model-len'])
    if hardware == 'pro6000':
        if (runtime.get('effective_max_model_len_from_api') != result['window_tokens']
                or runtime.get('gpu_model') != 'NVIDIA RTX PRO 6000 Blackwell Server Edition'
                or runtime.get('gpu_count') != count):
            raise ValueError('PRO hardware or effective context identity does not match')
        auto_window = result['model'] == 'selectel-gemma-a' and configured_window == -1
    else:
        auto_window = False
    if (int(flags['--tensor-parallel-size']) != count
            or not (configured_window == result['window_tokens'] or auto_window)):
        raise ValueError('Runtime does not match figure identity')
    provenance = runtime['launch_provenance']
    if hardware == 'pro6000':
        expected_mode = 'ai-inference' if count == 2 else 'manual-deployment'
        if provenance['mode'] != expected_mode:
            raise ValueError('Wrong PRO workshop launch method')
    if provenance['mode'] == 'ai-inference':
        if provenance['owner_chain_kinds'][-1] != 'InferenceService' or not provenance['owner_uids_verified']:
            raise ValueError('Unverified platform ownership')
        source = 'AI Inference. Нагрузка напрямую к движку, без WebUI/Bifrost.'
    elif provenance['mode'] == 'manual-deployment':
        if provenance['owner_chain_kinds'][-1] != 'Deployment' or not provenance['owner_uids_verified']:
            raise ValueError('Unverified manual ownership')
        source = 'Ручной Deployment; прямой API, без WebUI/Bifrost. Это не проверка платформы.'
    else:
        raise ValueError('Unknown launch method')
    if hardware == 'pro6000':
        return f'PRO 6000: {label}, {count} GPU, {number(result["window_tokens"], 0)} токенов', source
    return f'RTX 5060 Ti: {label}, {count} GPU, {result["window_tokens"] // 1024}K', source


def build(prefix, hardware='rtx5060'):
    if hardware not in ('rtx5060', 'pro6000'):
        raise ValueError('Unknown hardware')
    suffix = 'ctx' if hardware == 'pro6000' else 'k'
    if not re.fullmatch(r'(qwen|gemma-(base|cache|tune))-[0-9]+' + suffix, prefix):
        raise ValueError('Invalid result prefix')
    folder = ROOT / 'results' / (hardware + '-heavy')
    client = json.loads((folder / f'{prefix}-client.json').read_text())
    monitor = json.loads((folder / f'{prefix}-prometheus.json').read_text())
    telemetry = json.loads((folder / f'{prefix}-hardware.json').read_text())
    runtime = json.loads((folder / f'{prefix}-runtime.json').read_text())
    result = summarize(client, monitor)
    window_suffix = (f'-{result["window_tokens"]}ctx' if hardware == 'pro6000'
                     else f'-{result["window_tokens"] // 1024}k')
    if not prefix.endswith(window_suffix):
        raise ValueError('Result prefix and measured window disagree')
    title, launch = describe(result, runtime, hardware)
    start = min(r['submitted_at'] for r in result['requests'])
    end = max(r['completed_at'] for r in result['requests'])
    duration = end - start
    gpu = [s for s in telemetry['series']['gpu']
           if s['labels']['__name__'] == 'DCGM_FI_DEV_GPU_UTIL']
    if len(gpu) != 2 or {s['labels']['gpu'] for s in gpu} != {'0', '1'}:
        raise ValueError('Expected both measured node GPUs')
    for item in gpu:
        points = item['values']
        if (points[0][0] > start or points[-1][0] < end
                or any(not math.isfinite(v) or not 0 <= v <= 100 for t, v in points)
                or any(not 0 < b[0] - a[0] <= 10 for a, b in zip(points, points[1:]))):
            raise ValueError('Incomplete or invalid GPU activity series')
    d = Diagram(hardware + '-' + prefix + '-capacity',
                title,
                '10 запросов вместе: ' + number(result['input_tokens_each'], 0) + ' входных + 32 768 выходных каждый.',
                height=850 if hardware == 'pro6000' else 800)
    for x, label, value in ((48, 'Завершено запросов', '10 / 10'),
                            (424, 'Время всей серии', number(duration / 60) + ' мин'),
                            (800, 'Вытеснения KV', number(result['preemptions'], 0))):
        d.rect(x, 150, 352, 112, GRAY)
        d.text(x + 20, 182, label, 20, MUTED, width=312)
        d.text(x + 20, 233, value, 34, BLUE, True, width=312)

    left, width, height = 160, 992, 76
    def chart(top, title, ceiling, series):
        d.text(48, top + 30, title, 19, MUTED, True, width=95)
        for fraction in (0, .5, 1):
            y = top + height * (1 - fraction)
            d.path(f'M{left} {y} H1152', LINE, width=1)
            d.text(left - 12, y + 5, number(ceiling * fraction, 0), 16, MUTED, anchor='end')
        for j, (label, values, color) in enumerate(series):
            selected = [(max(start, min(t, end)), v) for t, v in values
                        if start - monitor['step_seconds'] <= t <= end + monitor['step_seconds']]
            route = ''
            for t, v in selected:
                x, y = left + width * (t - start) / duration, top + height * (1 - v / ceiling)
                route += (f'M{x:.2f} {y:.2f}' if not route else f' H{x:.2f} V{y:.2f}')
            d.path(route, color, width=2)
            d.text(left + j * 370, top - 10, label, 18, color, True, width=355)

    values = lambda key: monitor['series'][key]['data'][0]['values']
    chart(300, 'Запросы', 10, [('Активны (включая prefill)', values('running'), BLUE),
                              ('Ожидают', values('waiting'), AMBER)])
    chart(418, ['GPU узла', '%'], 100, [('GPU ' + item['labels']['gpu'], item['values'], color)
                              for item, color in zip(sorted(gpu, key=lambda x: x['labels']['gpu']), (BLUE, TEAL))])
    chart(536, 'KV, %', 100, [('Занятая часть KV-пула GPU', [[t, v * 100] for t, v in values('gpu_kv_fraction')], TEAL)])
    for i in range(5):
        d.text(left + width * i / 4, 640, number(duration * i / 240) + ' мин', 16, MUTED,
               anchor='start' if i == 0 else 'end' if i == 4 else 'middle')
    d.text(48, 674, 'TTFT: медиана ' + number(result['client_ttft_median_seconds'])
           + ' с, максимум ' + number(result['client_ttft_max_seconds'])
           + ' с. Средний TPOT движка: ' + number(result['engine_mean_tpot_seconds'] * 1000) + ' мс.',
           20, MUTED, width=1104)
    d.text(48, 711, launch, 20, MUTED, width=1104)
    if hardware == 'pro6000':
        d.text(48, 748, 'GPU узла показаны обе: соседняя модель может работать параллельно. Это не сравнение A/B.',
               18, MUTED, width=1104)
    d.footer('Синтетический тест. Данные: results/' + hardware + '-heavy/' + prefix + '-*.json.')
    return ROOT / 'assets' / (d.name + '.svg'), '\n'.join(d.parts + ['</svg>']) + '\n'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix', required=True)
    p.add_argument('--hardware', choices=('rtx5060', 'pro6000'), default='rtx5060')
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    path, content = build(a.prefix, a.hardware)
    if a.check:
        if not path.exists() or path.read_text() != content:
            raise SystemExit('Capacity figure is missing or stale')
    else:
        path.write_text(content)
    print('Validated capacity timeline:', path.name)
