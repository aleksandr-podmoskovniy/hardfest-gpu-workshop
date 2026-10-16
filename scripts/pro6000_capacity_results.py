#!/usr/bin/env python3
"""Build offline PRO capacity tables only after all four evidence bundles validate.

No article or asset is changed by build(). The optional CLI output directory gets
new files only; it is created after all evidence has passed validation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import NamedTuple

from build_diagrams import Diagram, BLUE, AMBER, MUTED, GRAY
from hardware_coverage import coverage
from heavy_capacity import summarize
from plot_capacity import describe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOLDER = ROOT / 'results/pro6000-heavy'
GPU_MODEL = 'NVIDIA RTX PRO 6000 Blackwell Server Edition'
SUFFIXES = ('client', 'prometheus', 'summary', 'runtime', 'hardware', 'hardware-coverage')
MATCHED_SOURCES = ('examples/pro6000-load-client/matched_suite.py',
                   'examples/pro6000-load-client/verify_offload_replay.py',
                   'examples/capacity-load-client/probe_transport.py', 'examples/pizza-incident.md')
MATCHED_WAVES = (('short', 'first'), ('long', 'first'), ('long', 'repeat'), ('mixed', 'first'))


class Series(NamedTuple):
    prefix: str
    label: str
    service: str
    model: str
    window: int
    gpu_count: int


# Matches the immutable public export mapping: base/cache/tune, not a/b.
SERIES = (
    Series('gemma-base-167520ctx', 'Gemma Base', 'selectel-gemma-a', 'selectel-gemma-a', 167520, 1),
    Series('gemma-cache-262144ctx', 'Gemma Cache', 'selectel-gemma-cache', 'selectel-gemma-cache', 262144, 1),
    Series('gemma-tune-262144ctx', 'Gemma Tune', 'selectel-gemma-b', 'selectel-gemma-b', 262144, 1),
    Series('qwen-262144ctx', 'Qwen TP2', 'selectel-qwen38-tp2', 'qwen3-8-flash-next-nvfp4', 262144, 2),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(source):
    return hashlib.sha256(source).hexdigest()


def number(value, places=2):
    return f'{value:,.{places}f}'.replace(',', ' ').replace('.', ',')


def verify_hardware(hardware, report, monitor, spec):
    """Validate the full node scope, without attributing both GPUs to Gemma."""
    require(report['fully_covered'] and report['missing_evaluations'] == 0,
            'Incomplete hardware evaluation coverage')
    require(all(hardware[k] == monitor[k] for k in ('start_unix', 'end_unix', 'step_seconds')),
            'Hardware interval differs from measured runtime interval')
    groups = hardware['series']
    expected_groups = {'gpu', 'memory_available', 'runtime_memory_working_set', 'conditions',
                       'restarts', 'runtime_scrape_up', 'engine_sample_age_seconds'}
    require(set(groups) == expected_groups, 'Missing or unexpected hardware group')
    gpu_metrics = {'DCGM_FI_DEV_FB_FREE', 'DCGM_FI_DEV_FB_USED', 'DCGM_FI_DEV_GPU_TEMP',
                   'DCGM_FI_DEV_GPU_UTIL', 'DCGM_FI_DEV_POWER_USAGE', 'DCGM_FI_DEV_XID_ERRORS'}
    expected_gpu = {(metric, gpu) for metric in gpu_metrics for gpu in ('0', '1')}
    actual_gpu = set()
    for item in groups['gpu']:
        labels = item['labels']
        identity = (labels.get('__name__'), labels.get('gpu'))
        require(identity in expected_gpu and identity not in actual_gpu
                and labels.get('modelName') == GPU_MODEL
                and labels.get('device') == 'nvidia' + labels['gpu'],
                'Hardware GPU identity mismatch')
        actual_gpu.add(identity)
        values = [v for _, v in item['values']]
        require(min(values) >= 0, 'Negative GPU sample')
        if identity[0] == 'DCGM_FI_DEV_GPU_UTIL':
            require(max(values) <= 100, 'Invalid GPU activity percentage')
        if identity[0] in ('DCGM_FI_DEV_FB_FREE', 'DCGM_FI_DEV_FB_USED'):
            require(max(values) <= 97887, 'GPU memory exceeds actual board capacity')
    require(actual_gpu == expected_gpu, 'Incomplete GPU telemetry')

    expected_names = {'memory_available': 'node_memory_MemAvailable_bytes',
                      'runtime_memory_working_set': 'container_memory_working_set_bytes',
                      'restarts': 'kube_pod_container_status_restarts_total',
                      'runtime_scrape_up': 'up'}
    for group, metric in expected_names.items():
        require(len(groups[group]) == 1 and groups[group][0]['labels'].get('__name__') == metric,
                'Wrong hardware metric: ' + group)
        require(all(v >= 0 for _, v in groups[group][0]['values']), 'Negative hardware sample')
    container = 'runtime' if spec.gpu_count == 2 else 'vllm'
    for group in ('runtime_memory_working_set', 'restarts', 'runtime_scrape_up'):
        require(groups[group][0]['labels'].get('container') == container,
                'Wrong measured runtime container')
    up = groups['runtime_scrape_up'][0]
    require(up['labels'].get('service') == spec.service and all(v == 1 for _, v in up['values']),
            'Runtime scrape was unavailable or belongs to another service')
    require(all(v == 0 for _, v in groups['restarts'][0]['values']),
            'Runtime restarted during measured series')
    conditions = groups['conditions']
    require(len(conditions) == 4 and {i['labels'].get('condition') for i in conditions}
            == {'Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure'}, 'Missing node conditions')
    for item in conditions:
        require(item['labels'].get('__name__') == 'kube_node_status_condition'
                and item['labels'].get('status') == 'true'
                and all(v in (0, 1) for _, v in item['values']), 'Invalid node condition')
    ages = groups['engine_sample_age_seconds']
    require(len(ages) == 1 and ages[0]['labels'].get('model_name') == spec.model
            and ages[0]['labels'].get('service') == spec.service
            and ages[0]['labels'].get('container') == container,
            'Engine freshness identity mismatch')
    require(all(0 <= v <= 30 for _, v in ages[0]['values']),
            'Engine samples older than 30 seconds or invalid')


def load_bundle(folder, spec):
    """Read and revalidate one actual bundle; useful independently for audits."""
    folder = Path(folder)
    paths = {suffix: folder / f'{spec.prefix}-{suffix}.json' for suffix in SUFFIXES}
    missing = [p.name for p in paths.values() if not p.is_file()]
    require(not missing, 'Missing evidence: ' + ', '.join(missing))
    raw = {key: path.read_bytes() for key, path in paths.items()}
    data = {key: json.loads(value) for key, value in raw.items()}
    expected = summarize(data['client'], data['prometheus'])
    expected['source_sha256'] = {'client': digest(raw['client']), 'monitor': digest(raw['prometheus'])}
    require(data['summary'] == expected, 'Stored summary or source SHA256 differs: ' + spec.prefix)
    require((expected['service'], expected['model'], expected['window_tokens'])
            == (spec.service, spec.model, spec.window), 'Measured series identity mismatch: ' + spec.prefix)

    runtime = data['runtime']
    # Shared validator checks model repositories, actual API window, TP, GPU count,
    # and verified manual Deployment / platform InferenceService ownership.
    describe(expected, runtime, 'pro6000')
    require(runtime.get('gpu_memory_mib_each') == 97887, 'Wrong measured GPU memory capacity')
    require(runtime.get('runtime_restart_count_after') == 0, 'Runtime restart count is not zero')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', runtime.get('runtime_image_digest', '')) is not None,
            'Missing actual runtime image digest')
    for model in runtime['models']:
        require(re.fullmatch(r'[0-9a-f]{40}', model.get('revision', '')) is not None
                and re.fullmatch(r'sha256:[0-9a-f]{64}', model.get('artifact_digest_in_actual_pod', '')) is not None,
                'Missing immutable model identity')
    actual_coverage = coverage(raw['hardware'])
    require(data['hardware-coverage'] == actual_coverage, 'Hardware coverage report or SHA256 differs')
    verify_hardware(data['hardware'], actual_coverage, data['prometheus'], spec)
    return {'spec': spec, 'summary': expected, 'runtime': runtime, 'coverage': actual_coverage}


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| --- |' + ' ---: |' * (len(headers) - 1),
                      *('| ' + ' | '.join(row) + ' |' for row in rows)])


def metric_rows(bundles):
    results = [bundle['summary'] for bundle in bundles]
    rows = []
    for label, key, scale, places in (
            ('Полное окно, токенов', 'window_tokens', 1, 0),
            ('Вход на запрос, токенов', 'input_tokens_each', 1, 0),
            ('Фактический выход на запрос, токенов', 'output_tokens_each', 1, 0),
            ('Подано одновременно', 'submitted_concurrently', 1, 0),
            ('Завершено', 'completed', 1, 0),
            ('Пик Running по метрикам', 'sampled_max_running', 1, 0),
            ('TTFT клиента, медиана, мин', 'client_ttft_median_seconds', 1 / 60, 2),
            ('TTFT клиента, максимум, мин', 'client_ttft_max_seconds', 1 / 60, 2),
            ('Средний TPOT движка, мс', 'engine_mean_tpot_seconds', 1000, 2),
            ('Суммарный выход за время серии, токенов/с', 'aggregate_output_tokens_per_second', 1, 2),
            ('Время всей серии, мин', 'wall_seconds', 1 / 60, 2),
            ('Вытеснения активных запросов', 'preemptions', 1, 0)):
        rows.append([label, *[number(r[key] * scale, places) for r in results]])
    rows.append(['Покрытие сетки аппаратных метрик', *['Полное' for _ in bundles]])
    return rows


def explanation(bundles):
    sources = ', '.join(f'[{b["spec"].label}](results/pro6000-heavy/{b["spec"].prefix}-summary.json)'
                        for b in bundles)
    return ('**Это перегрузочный тест вместимости, не проверка скорости обычного чата.** '
            'На каждый запрос действительно получено 32 768 токенов; остановка по EOS отключена. '
            'Нагрузка шла напрямую к vLLM, без WebUI и Bifrost.\n\n'
            'Running — наблюдавшийся пик активных запросов, включая prefill, на дискретных замерах. '
            'Он не доказывает одновременное размещение десяти полных окон. '
            'TTFT — ожидание первого токена, включая очередь; TPOT — средний интервал генерации токена. '
            'Суммарный выход делится на время всей серии, включая ожидание.\n\n'
            'Серии различаются размером окна, моделью и условиями нагрузки. '
            'По этим сериям нельзя вычислять ускорение A/B или обещать приемлемую задержку чата. '
            'Аппаратные метрики охватывают обе GPU узла; если на нём работали другие сервисы, '
            'их потребление нельзя приписывать измеряемой модели. '
            'Полное покрытие сетки не означает непрерывного наблюдения.\n\n'
            'Проверенные исходные результаты: ' + sources + '.')


def hero(bundles):
    d = Diagram('pro6000-heavy-result', 'PRO 6000: проверка длинных запросов',
                'Каждая серия: 10 поданных и 10 завершённых запросов, по 32 768 выходных токенов.', height=820)
    columns = ((68, ['Модель и полное окно'], 246),
               (342, ['Первый токен', 'медиана, мин'], 242),
               (620, ['Вся серия,', 'минуты'], 218),
               (884, ['Пик Running', 'по метрикам'], 244))
    for x, labels, width in columns:
        d.text(x, 165, labels, 20, MUTED, True, width=width)
    for index, bundle in enumerate(bundles):
        spec, result = bundle['spec'], bundle['summary']
        y = 219 + index * 105
        d.rect(48, y, 1104, 90, GRAY)
        d.text(68, y + 34, spec.label, 24, bold=True, width=246)
        d.text(68, y + 65, number(result['window_tokens'], 0) + ' токенов', 19, MUTED, width=246)
        d.text(342, y + 55, number(result['client_ttft_median_seconds'] / 60), 30, AMBER, True, width=242)
        d.text(620, y + 55, number(result['wall_seconds'] / 60), 30, BLUE, True, width=218)
        d.text(884, y + 55, number(result['sampled_max_running'], 0), 30, MUTED, True, width=244)
    d.text(48, 665, ['Синтетический тест вместимости. Длинное ожидание не является нормой для чата.',
                     'Running включает prefill; это не число полностью размещённых окон.',
                     'Разные окна и пересечение нагрузок: ускорение A/B здесь не измеряется.'],
           20, MUTED, width=1104)
    d.footer('Источник: results/pro6000-heavy; четыре завершённые серии, прямой API vLLM.')
    return '\n'.join(d.parts + ['</svg>']) + '\n'


def build(folder=DEFAULT_FOLDER):
    """Return (blocks, figures), using article_results' extensionless figure keys.

    The figure key pro6000-heavy-result corresponds to pro6000-heavy-result.svg.
    No subset, fallback, file write or article integration is performed.
    """
    bundles = [load_bundle(folder, spec) for spec in SERIES]
    gemma, qwen = bundles[:3], bundles[3:]
    blocks = {
        'pro-capacity': table(['Gemma: полное окно', 'Base', 'Cache', 'Tune'], metric_rows(gemma))
                        + '\n\n' + explanation(gemma),
        'pro-qwen': table(['Qwen TP2: полное окно', 'Измерено'], metric_rows(qwen))
                    + '\n\n' + explanation(qwen),
    }
    return blocks, {'pro6000-heavy-result': hero(bundles)}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def sha256_value(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def matched_summaries(profile, prompts):
    """Recompute exporter summaries from redacted request records, never captions."""
    waves = profile['waves']
    require([(w['kind'], w['iteration']) for w in waves] == list(MATCHED_WAVES), 'Wrong matched wave schedule')
    previous = profile['started_at']
    result = []
    for wave in waves:
        require(all(finite(wave.get(k)) for k in ('started_at', 'ended_at'))
                and previous <= wave['started_at'] < wave['ended_at'] <= profile['ended_at'],
                'Invalid matched wave interval')
        previous = wave['ended_at']
        rows = wave['results']
        require(len(rows) == 10 and all(type(r.get('session')) is int for r in rows)
                and {r['session'] for r in rows} == set(range(10)), 'Ten unique matched requests required')
        for row in rows:
            require(row.get('ok') is True and row.get('done') is True and row.get('finish_reason') == 'stop'
                    and not row.get('error') and not row.get('watchdog_reason'), 'Incomplete natural reply')
            require(type(row.get('answer_characters')) is int and row['answer_characters'] > 0
                    and type(row.get('reasoning_characters')) is int and row['reasoning_characters'] >= 0
                    and sha256_value(row.get('answer_sha256')) and sha256_value(row.get('reasoning_sha256')),
                    'Missing redacted natural answer identity')
            usage = row['usage']
            require(all(type(usage.get(k)) is int for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                    and usage['prompt_tokens'] == prompts[wave['kind']][row['session']]['input_tokens']
                    and 0 < usage['completion_tokens'] <= 32768
                    and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens']
                    and usage['total_tokens'] <= profile['runtime']['effective_runtime']['max_model_len_from_api'],
                    'Wrong matched actual usage')
            times = [row.get(k) for k in ('first_generated_text_s', 'first_visible_text_s', 'last_content_s', 'elapsed_s')]
            require(all(finite(t) and t >= 0 for t in times) and times == sorted(times)
                    and all(finite(row.get(k)) for k in ('submitted_at', 'completed_at'))
                    and wave['started_at'] <= row['submitted_at'] < row['completed_at'] <= wave['ended_at']
                    and abs(row['completed_at'] - row['submitted_at'] - row['elapsed_s']) < 1,
                    'Invalid matched stream timing')
            delay = 1.0 if wave['kind'] == 'mixed' and row['session'] >= 2 else 0.0
            require(finite(row.get('scheduled_delay_s')) and row['scheduled_delay_s'] == delay,
                    'Wrong matched scheduled delay')
        first = min(r['submitted_at'] for r in rows)
        require(all(abs(r['submitted_at'] - first - r['scheduled_delay_s']) < .5 for r in rows),
                'Matched launch schedule drifted')
        overlap = None
        groups = [('all', rows)]
        if wave['kind'] == 'mixed':
            long_rows = [r for r in rows if r['session'] < 2]
            short_rows = [r for r in rows if r['session'] >= 2]
            overlap = sum(any(long['submitted_at'] <= short['submitted_at'] < long['completed_at']
                              for long in long_rows) for short in short_rows)
            groups = [('long', long_rows), ('short', short_rows)]
        require(wave.get('short_requests_submitted_while_long_in_flight') == overlap,
                'Matched overlap is not reproducible')
        for group, members in groups:
            result.append({
                'kind': wave['kind'], 'iteration': wave['iteration'], 'group': group, 'completed': len(members),
                'input_tokens_range': [min(r['usage']['prompt_tokens'] for r in members), max(r['usage']['prompt_tokens'] for r in members)],
                'actual_output_tokens_range': [min(r['usage']['completion_tokens'] for r in members), max(r['usage']['completion_tokens'] for r in members)],
                'first_generated_median_s': statistics.median(r['first_generated_text_s'] for r in members),
                'first_visible_median_s': statistics.median(r['first_visible_text_s'] for r in members),
                'first_visible_max_s': max(r['first_visible_text_s'] for r in members),
                'elapsed_median_s': statistics.median(r['elapsed_s'] for r in members),
                'actual_output_tokens_total': sum(r['usage']['completion_tokens'] for r in members),
                'request_lifetime_overlap': overlap,
            })
    return result


def validate_matched(data, source_root=ROOT):
    """Check public export integrity; private capture hashes are attestations.

    Private Pod/capture files are deliberately unavailable here. Their hash format
    and verified-export fields are checked, not falsely described as re-hashed.
    Public workload files and all exported response timings are independently checked.
    """
    require(data.get('schema_version') == 1 and data.get('experiment') == 'PRO 6000 matched natural chat'
            and data.get('sessions_per_wave') == 10 and data.get('completed_requests') == 120
            and data.get('output_cap') == 32768 and data.get('ignore_eos') is False
            and data.get('input_identity_verified') is True and data.get('same_host_verified') is True
            and type(data.get('same_physical_gpu_across_profiles')) is bool
            and data.get('sequential_profile_order') == ['Base', 'Cache', 'Tune'],
            'Wrong completed matched experiment protocol')
    require(isinstance(data.get('limitations'), list) and data['limitations'], 'Missing matched limitations')
    require(set(data.get('timing_definitions', {})) == {'first_generated_text_s', 'first_visible_text_s', 'elapsed_s'},
            'Missing matched timing definitions')
    require(data.get('document', {}).get('path') == MATCHED_SOURCES[-1], 'Unexpected matched public document')
    source_hashes = {path: digest((Path(source_root) / path).read_bytes()) for path in MATCHED_SOURCES}
    require(data['document'].get('sha256') == source_hashes[MATCHED_SOURCES[-1]], 'Matched public document SHA256 differs')
    prompts = data['prompts']
    require(set(prompts) == {'short', 'long', 'mixed'}, 'Missing matched prompt family')
    for kind, rows in prompts.items():
        require(len(rows) == 10 and [r['session'] for r in rows] == list(range(10)), 'Wrong matched prompt identity table')
        for row in rows:
            long_input = kind == 'long' or kind == 'mixed' and row['session'] < 2
            tokens = row.get('input_tokens')
            require(type(tokens) is int and (32600 <= tokens <= 32700 if long_input else 0 < tokens < 8192)
                    and sha256_value(row.get('token_ids_sha256')), 'Wrong matched prompt identity')
    profiles = data['profiles']
    require([p['profile'] for p in profiles] == ['Base', 'Cache', 'Tune'], 'Missing or duplicate matched profiles')
    before_labels = {'runtime_capture', 'ownership_capture', 'service_capture', 'timestamp_capture',
                     'mounted_profile', 'served_model_api', 'version_api', 'runtime_log', 'idle_metrics',
                     'model_artifacts', 'gpu_capture'}
    capture_keys = {stage + '_' + name for stage in ('before', 'after') for name in before_labels}
    capture_keys |= {'launch_boundary', 'actual_client_objects', 'collector_report'}
    capture_keys |= {kind + '_' + Path(path).name for kind in ('client', 'collector') for path in MATCHED_SOURCES[:-1]}
    hardware, model_identities = [], []
    previous = None
    for profile, spec in zip(profiles, SERIES[:3]):
        require(all(finite(profile.get(k)) for k in ('started_at', 'ended_at'))
                and profile['started_at'] < profile['ended_at']
                and (previous is None or previous <= profile['started_at']), 'Matched profile runs overlap or are incomplete')
        previous = profile['ended_at']
        interval = profile.get('runtime_capture_interval', {})
        require(profile.get('runtime_stable_before_launch_after') is True
                and all(finite(interval.get(k)) for k in ('before', 'launch', 'after'))
                and interval['before'] <= interval['launch'] <= profile['started_at'] < profile['ended_at'] <= interval['after'],
                'Matched captures do not bracket the experiment')
        captures = profile.get('capture_sha256', {})
        require(set(captures) == capture_keys and all(sha256_value(v) for v in captures.values())
                and sha256_value(profile.get('source_report_sha256')), 'Missing matched capture provenance')
        client = profile['client']
        require(client.get('actual_mount_and_command_verified') is True
                and client.get('collector_container_identity_verified') is True
                and re.fullmatch(r'sha256:[0-9a-f]{64}', client.get('client_image_digest', '')) is not None,
                'Unverified matched client identity')
        sources = client['sources']
        require(len(sources) == len(MATCHED_SOURCES)
                and {s['path']: s['sha256'] for s in sources} == source_hashes, 'Measured client source SHA256 differs')
        require(all(captures['client_' + Path(path).name] == source_hashes[path] for path in MATCHED_SOURCES[:-1]),
                'Captured matched client SHA256 differs')
        runtime = profile['runtime']
        require(runtime.get('gpu_model') == GPU_MODEL and runtime.get('gpu_memory_mib') == 97887
                and runtime.get('gpu_count') == 1 and runtime.get('restart_count') == 0
                and re.fullmatch(r'\d+(?:\.\d+)+', runtime.get('driver_version', '')) is not None
                and re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z0-9.+-]+)?', runtime.get('vllm_version', '')) is not None
                and re.fullmatch(r'sha256:[0-9a-f]{64}', runtime.get('runtime_image_digest', '')) is not None,
                'Wrong actual matched runtime/GPU identity')
        require(runtime.get('ownership') == {'kind': 'manual-deployment', 'chain': ['Pod', 'ReplicaSet', 'Deployment'],
                                             'controller_chain_verified': True}, 'Unverified matched manual runtime')
        parameters, effective = runtime['configured_parameters'], runtime['effective_runtime']
        require(parameters.get('tensor-parallel-size') == 1 and parameters.get('max-num-seqs') == 10
                and effective.get('max_model_len_from_api') == spec.window
                and parameters.get('max-model-len') in ((-1, spec.window) if spec.label == 'Gemma Base' else (spec.window,))
                and type(effective.get('gpu_kv_pool_tokens')) is int and effective['gpu_kv_pool_tokens'] > 0
                and type(effective.get('max_num_batched_tokens')) is int and effective['max_num_batched_tokens'] > 0
                and effective.get('chunked_prefill') is True
                and effective.get('prefix_caching') is bool(parameters.get('enable-prefix-caching', False)),
                'Matched effective configuration differs')
        repositories = {'google/gemma-4-31B-it'}
        if profile['profile'] == 'Tune':
            repositories.add('google/gemma-4-31B-it-assistant')
        models = runtime['models']
        require(len(models) == len(repositories) and {m['repository'] for m in models} == repositories,
                'Wrong matched model artifacts')
        for model in models:
            require(re.fullmatch(r'[0-9a-f]{40}', model.get('revision', '')) is not None
                    and re.fullmatch(r'sha256:[0-9a-f]{64}', model.get('artifact_digest', '')) is not None,
                    'Missing matched immutable model identity')
        model_identities.append(next(m for m in models if m['repository'] == 'google/gemma-4-31B-it'))
        hardware.append((runtime['gpu_model'], runtime['gpu_memory_mib'], runtime['driver_version']))
        require(profile['summary'] == matched_summaries(profile, prompts), 'Matched summary differs from actual responses')
    require(all(m == model_identities[0] for m in model_identities), 'Matched Gemma artifacts differ across profiles')
    require(len(set(hardware)) == 1, 'Matched hardware differs across profiles')
    return data


def build_matched(folder=DEFAULT_FOLDER, source_root=ROOT):
    """Build a separate natural-reply scorecard; never use capacity as chat data."""
    path = Path(folder) / 'gemma-matched-chat.json'
    require(path.is_file(), 'Missing evidence: gemma-matched-chat.json')
    data = validate_matched(json.loads(path.read_bytes()), source_root)
    profiles = data['profiles']
    labels = ('Короткий вход', 'Длинный вход, первый проход', 'Тот же длинный вход, повтор',
              'Смешанная нагрузка: длинные запросы', 'Смешанная нагрузка: короткие запросы')
    rows = [[label, *[number(p['summary'][i]['first_visible_median_s'], 3) for p in profiles]]
            for i, label in enumerate(labels)]
    output_ranges = [[min(s['actual_output_tokens_range'][0] for s in p['summary']),
                      max(s['actual_output_tokens_range'][1] for s in p['summary'])] for p in profiles]
    range_text = lambda span: number(span[0], 0) if span[0] == span[1] else number(span[0], 0) + '–' + number(span[1], 0)
    long_range = [min(r['input_tokens'] for r in data['prompts']['long']), max(r['input_tokens'] for r in data['prompts']['long'])]
    hardware_note = ('Все профили работали на одной физической GPU.' if data['same_physical_gpu_across_profiles']
                     else 'Профили работали на одном узле, но физические GPU различались.')
    block = ('Медиана времени до первого видимого текста ответа, секунды. Рассуждения и heartbeat не входят.\n\n'
             + table(['Нагрузка', 'Base', 'Cache', 'Tune'], rows) + '\n\n'
             + table(['Фактический результат', 'Base', 'Cache', 'Tune'], [
                 ['Завершено естественной остановкой', '40 / 40', '40 / 40', '40 / 40'],
                 ['Выход на запрос, токенов', *[range_text(span) for span in output_ranges]]])
             + '\n\nДлинный вход: ' + range_text(long_range) + ' токенов. Лимит выхода — 32 768, '
             'но ответы завершались сами: это не проверка генерации 32K. '
             'Base, Cache и Tune измерены последовательно с одинаковыми входными токенами. '
             'Длина ответов различается; сравнивается профиль целиком, а не отдельная оптимизация.\n\n'
             'В первом длинном проходе общий префикс мог переиспользоваться уже внутри волны. '
             'В смешанной волне два длинных запроса отправлены первыми, восемь коротких — через секунду. '
             'Пересечение времени запросов не доказывает одновременных вычислений на GPU. '
             + hardware_note + ' Проверки простоя на границах не доказывают отсутствия соседней нагрузки.\n\n'
             'Это прямой API vLLM, не WebUI/Bifrost и не проверка качества ответов по всему документу. '
             '[Исходные ответы в виде хешей, времена и конфигурации](results/pro6000-heavy/gemma-matched-chat.json).')
    d = Diagram('pro6000-chat-result', 'PRO 6000: когда появляется ответ',
                'Медиана до первого видимого текста, секунды. По 10 естественно завершённых ответов в волне.', height=780)
    for x, label, width in ((68, ['Профиль'], 226), (330, ['Короткий', 'вход'], 226),
                            (608, ['Длинный вход', 'первый проход'], 230), (886, ['Длинный вход', 'повтор'], 242)):
        d.text(x, 168, label, 20, MUTED, True, width=width)
    for index, profile in enumerate(profiles):
        y = 226 + index * 112
        d.rect(48, y, 1104, 94, GRAY)
        d.text(68, y + 57, profile['profile'], 26, bold=True, width=226)
        for x, metric in zip((330, 608, 886), profile['summary'][:3]):
            d.text(x, y + 57, number(metric['first_visible_median_s'], 3), 31, BLUE, True, width=226)
    d.text(48, 610, ['Длинный вход: ' + range_text(long_range) + ' токенов. Это не полное окно модели.',
                     'Лимит выхода 32 768 не означает столько же фактически сгенерированных токенов.',
                     'Общий префикс мог переиспользоваться в первой волне. Прямой API, без WebUI.',
                     'Разная длина ответов; вывод относится к профилю целиком, не к отдельной настройке.'],
           19, MUTED, width=1104)
    d.footer('Источник: results/pro6000-heavy/gemma-matched-chat.json. Смешанная нагрузка — в таблице.')
    return {'pro-chat': block}, {'pro6000-chat-result': '\n'.join(d.parts + ['</svg>']) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEFAULT_FOLDER)
    parser.add_argument('--output-dir', type=Path, help='Write new offline files; never update articles')
    args = parser.parse_args()
    blocks, figures = build(args.folder)
    if args.output_dir is not None:
        files = {name + '.md': content for name, content in blocks.items()}
        files.update({name + '.svg': content for name, content in figures.items()})
        require(not any((args.output_dir / name).exists() for name in files),
                'Output exists; choose a new offline directory')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            with (args.output_dir / name).open('x') as target:
                target.write(content + ('\n' if not content.endswith('\n') else ''))
    print('Validated four PRO capacity bundles; articles unchanged.')


if __name__ == '__main__':
    main()
