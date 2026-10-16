"""Offline-only fixtures: synthetic evidence is never written into results/."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pro6000_capacity_results as pro
from heavy_capacity import METRICS, summarize
from hardware_coverage import coverage


def fixture(spec):
    tokens = spec.window - 32768
    rows = [dict(session=i, ok=True, done=True, finish_reason='length', ttft_s=10.,
                 elapsed_s=300., last_content_s=299., submitted_at=200. + i / 1000,
                 completed_at=500. + i / 1000,
                 usage=dict(prompt_tokens=tokens, completion_tokens=32768, total_tokens=spec.window))
            for i in range(10)]
    client = dict(run_id='synthetic-unit-test-' + spec.prefix, ignore_eos=True, passes=[dict(
        ok=True, model=spec.model, sessions=10, input_tokens=tokens, output_tokens=32768,
        elapsed_s=300.009, results=rows)])
    monitor = dict(service=spec.service, start_unix=199, end_unix=509, step_seconds=10, series={})
    finals = dict(output_tokens=327680, completed_output_tokens=327680, completed_output_count=10,
                  preemptions=0, running=0, waiting=0, gpu_kv_fraction=0)
    for metric in ('ttft', 'tpot', 'queue', 'prefill', 'decode', 'e2e'):
        finals[metric + '_count'] = 10
        finals[metric + '_sum'] = 100
    finals['decode_sum'] = 100 * 32767
    for key, last in finals.items():
        name, unit = METRICS[key]
        values = [[t, last if t == 509 else 0] for t in range(199, 510, 10)]
        if key in ('running', 'waiting', 'gpu_kv_fraction'):
            values[1][1] = {'running': 2, 'waiting': 8, 'gpu_kv_fraction': .9}[key]
        monitor['series'][key] = dict(unit=unit, data=[dict(labels=dict(
            __name__=name, service=spec.service, model_name=spec.model), values=values)])
    monitor['series']['success_by_reason'] = dict(unit='requests', data=[dict(labels=dict(
        __name__='vllm:request_success_total', service=spec.service, model_name=spec.model,
        finished_reason=reason), values=[[t, 10 if reason == 'length' and t == 509 else 0]
                                       for t in range(199, 510, 10)])
        for reason in ('length', 'error', 'abort')])
    repositories = ['google/gemma-4-31B-it']
    if spec.label == 'Gemma Tune':
        repositories.append('google/gemma-4-31B-it-assistant')
    if spec.gpu_count == 2:
        repositories = ['nvidia/Qwen3.8-Flash-Next-NVFP4']
    runtime = dict(
        gpu_model=pro.GPU_MODEL, gpu_count=spec.gpu_count, gpu_memory_mib_each=97887,
        runtime_image_digest='sha256:' + 'a' * 64, runtime_restart_count_after=0,
        served_model_names=[spec.model], effective_max_model_len_from_api=spec.window,
        models=[dict(repository=repo, revision='b' * 40, artifact_digest_in_actual_pod='sha256:' + 'c' * 64)
                for repo in repositories],
        server_flags_from_actual_pod={'--max-model-len': -1 if spec.label == 'Gemma Base' else spec.window,
                                      '--tensor-parallel-size': spec.gpu_count},
        launch_provenance=dict(mode='ai-inference' if spec.gpu_count == 2 else 'manual-deployment',
                               owner_uids_verified=True,
                               owner_chain_kinds=['Pod', 'InferenceService' if spec.gpu_count == 2 else 'Deployment']))
    container = 'runtime' if spec.gpu_count == 2 else 'vllm'

    def hardware_series(labels, value=0):
        return dict(labels=labels, values=[[t, value] for t in range(199, 510, 10)])

    hardware = dict(start_unix=199, end_unix=509, step_seconds=10, series={
        'gpu': [hardware_series(dict(__name__=metric, gpu=gpu, device='nvidia' + gpu,
                                    modelName=pro.GPU_MODEL))
                for gpu in ('0', '1') for metric in ('DCGM_FI_DEV_FB_FREE', 'DCGM_FI_DEV_FB_USED',
                'DCGM_FI_DEV_GPU_TEMP', 'DCGM_FI_DEV_GPU_UTIL', 'DCGM_FI_DEV_POWER_USAGE', 'DCGM_FI_DEV_XID_ERRORS')],
        'memory_available': [hardware_series(dict(__name__='node_memory_MemAvailable_bytes'))],
        'runtime_memory_working_set': [hardware_series(dict(__name__='container_memory_working_set_bytes', container=container))],
        'restarts': [hardware_series(dict(__name__='kube_pod_container_status_restarts_total', container=container))],
        'runtime_scrape_up': [hardware_series(dict(__name__='up', container=container, service=spec.service), 1)],
        'conditions': [hardware_series(dict(__name__='kube_node_status_condition', condition=condition,
                                             status='true'), 1 if condition == 'Ready' else 0)
                       for condition in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')],
        'engine_sample_age_seconds': [hardware_series(dict(container=container, model_name=spec.model,
                                                           service=spec.service), 1)],
    })
    return dict(client=client, prometheus=monitor, runtime=runtime, hardware=hardware)


def write_json(folder, spec, suffix, value):
    path = folder / f'{spec.prefix}-{suffix}.json'
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    return path


def write_bundle(folder, spec, data=None):
    data = fixture(spec) if data is None else data
    paths = {suffix: write_json(folder, spec, suffix, value) for suffix, value in data.items()}
    summary = summarize(data['client'], data['prometheus'])
    summary['source_sha256'] = {key: hashlib.sha256(paths[suffix].read_bytes()).hexdigest()
                                for key, suffix in (('client', 'client'), ('monitor', 'prometheus'))}
    write_json(folder, spec, 'summary', summary)
    write_json(folder, spec, 'hardware-coverage', coverage(paths['hardware'].read_bytes()))
    return data


def matched_fixture():
    """A redacted schema-v1 fixture, held in memory or TemporaryDirectory only."""
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in pro.MATCHED_SOURCES}
    data = dict(schema_version=1, experiment='PRO 6000 matched natural chat', sessions_per_wave=10,
                completed_requests=120, output_cap=32768, ignore_eos=False,
                input_identity_verified=True, same_host_verified=True, same_physical_gpu_across_profiles=False,
                sequential_profile_order=['Base', 'Cache', 'Tune'],
                document=dict(path=pro.MATCHED_SOURCES[-1], sha256=source_hashes[pro.MATCHED_SOURCES[-1]]),
                timing_definitions=dict(first_generated_text_s='Generated', first_visible_text_s='Visible', elapsed_s='Complete'),
                limitations=['Synthetic fixture; not a measurement'], prompts={}, profiles=[])
    for kind in ('short', 'long', 'mixed'):
        data['prompts'][kind] = [dict(session=i, token_ids_sha256=sha(kind + str(i)),
            input_tokens=(32600 if kind == 'long' or kind == 'mixed' and i < 2 else 1000) + i) for i in range(10)]
    labels = ('runtime_capture', 'ownership_capture', 'service_capture', 'timestamp_capture',
              'mounted_profile', 'served_model_api', 'version_api', 'runtime_log', 'idle_metrics', 'model_artifacts', 'gpu_capture')
    for index, spec in enumerate(pro.SERIES[:3]):
        start = 1000 + index * 1000
        identities = fixture(spec)['runtime']['models']
        models = [dict(repository=m['repository'], revision=m['revision'], artifact_digest=m['artifact_digest_in_actual_pod'])
                  for m in identities]
        runtime = dict(gpu_model=pro.GPU_MODEL, gpu_memory_mib=97887, gpu_count=1, driver_version='615.71.09',
                       vllm_version='0.31.0', runtime_image_digest='sha256:' + 'd' * 64, restart_count=0, models=models,
                       ownership=dict(kind='manual-deployment', chain=['Pod', 'ReplicaSet', 'Deployment'], controller_chain_verified=True),
                       configured_parameters={'tensor-parallel-size': 1, 'max-num-seqs': 10,
                           'max-model-len': -1 if index == 0 else spec.window, 'enable-prefix-caching': index > 0},
                       effective_runtime=dict(max_model_len_from_api=spec.window, gpu_kv_pool_tokens=410660,
                                              max_num_batched_tokens=4096, chunked_prefill=True, prefix_caching=index > 0))
        captures = {stage + '_' + name: sha(stage + name) for stage in ('before', 'after') for name in labels}
        captures.update({name: sha(name) for name in ('launch_boundary', 'actual_client_objects', 'collector_report')})
        for path in pro.MATCHED_SOURCES[:-1]:
            captures['client_' + Path(path).name] = source_hashes[path]
            captures['collector_' + Path(path).name] = sha(path)
        profile = dict(profile=spec.label.removeprefix('Gemma '), started_at=start, ended_at=start + 110,
                       runtime=runtime, capture_sha256=captures, source_report_sha256=sha(spec.prefix),
                       runtime_stable_before_launch_after=True,
                       runtime_capture_interval=dict(before=start-20, launch=start-10, after=start+120),
                       client=dict(actual_mount_and_command_verified=True, collector_container_identity_verified=True,
                                   client_image_digest='sha256:' + 'e' * 64,
                                   sources=[dict(path=path, sha256=value) for path, value in source_hashes.items()]), waves=[])
        for wave_index, (kind, iteration) in enumerate(pro.MATCHED_WAVES):
            begin = start + 10 + wave_index * 30
            rows = []
            for session in range(10):
                delay = 1.0 if kind == 'mixed' and session >= 2 else 0.0
                submitted = begin + .01 * session + delay
                prompt = data['prompts'][kind][session]['input_tokens']
                rows.append(dict(session=session, scheduled_delay_s=delay, submitted_at=submitted, completed_at=submitted+3,
                    ok=True, done=True, finish_reason='stop', first_generated_text_s=1., first_visible_text_s=1.5,
                    last_content_s=2., elapsed_s=3., usage=dict(prompt_tokens=prompt, completion_tokens=100+session,
                                                               total_tokens=prompt+100+session),
                    answer_sha256=sha('fixture answer'), answer_characters=14, reasoning_sha256=sha(''), reasoning_characters=0))
            profile['waves'].append(dict(kind=kind, iteration=iteration, started_at=begin, ended_at=begin+5, results=rows,
                short_requests_submitted_while_long_in_flight=8 if kind == 'mixed' else None))
        profile['summary'] = pro.matched_summaries(profile, data['prompts'])
        data['profiles'].append(profile)
    return data


def write_matched(folder, data=None):
    data = matched_fixture() if data is None else data
    (folder / 'gemma-matched-chat.json').write_text(json.dumps(data, ensure_ascii=False, allow_nan=False) + '\n')
    return data


class ProCapacityResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def populate(self):
        for spec in pro.SERIES:
            write_bundle(self.folder, spec)

    def test_real_completed_cache_is_revalidated_not_a_full_build(self):
        bundle = pro.load_bundle(pro.DEFAULT_FOLDER, pro.SERIES[1])
        result = bundle['summary']
        self.assertEqual((result['submitted_concurrently'], result['completed']), (10, 10))
        self.assertEqual((result['input_tokens_each'], result['output_tokens_each']), (229376, 32768))
        self.assertEqual(result['sampled_max_running'], 2)
        self.assertAlmostEqual(result['wall_seconds'], 11475.955, places=3)
        self.assertTrue(bundle['coverage']['fully_covered'])

    def test_four_bundles_produce_two_blocks_and_one_shared_design_svg(self):
        self.populate()
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        blocks, figures = pro.build(self.folder)
        self.assertEqual(set(blocks), {'pro-capacity', 'pro-qwen'})
        self.assertEqual(set(figures), {'pro6000-heavy-result'})
        for text in blocks.values():
            for expected in ('32 768', 'Подано одновременно', 'Завершено', 'Пик Running по метрикам',
                             'TTFT клиента, медиана', 'TTFT клиента, максимум', 'TPOT движка',
                             'токенов/с', 'Время всей серии', 'не проверка скорости обычного чата',
                             'без WebUI и Bifrost', 'нельзя вычислять ускорение A/B'):
                self.assertIn(expected, text)
        self.assertIn('134 752', blocks['pro-capacity'])
        self.assertIn('229 376', blocks['pro-capacity'])
        svg = ET.fromstring(figures['pro6000-heavy-result'])
        self.assertEqual(svg.attrib['data-design'], 'hardfest-v2')
        figure_text = ' '.join(svg.itertext())
        for label in ('Gemma Base', 'Gemma Cache', 'Gemma Tune', 'Qwen TP2', 'Пик Running', 'по метрикам'):
            self.assertIn(label, figure_text)
        self.assertNotIn('≤', figure_text)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})

    def test_every_required_file_is_mandatory(self):
        self.populate()
        for spec in pro.SERIES:
            for suffix in pro.SUFFIXES:
                with self.subTest(prefix=spec.prefix, suffix=suffix):
                    path = self.folder / f'{spec.prefix}-{suffix}.json'
                    contents = path.read_bytes()
                    path.unlink()
                    with self.assertRaisesRegex(ValueError, 'Missing evidence'):
                        pro.build(self.folder)
                    path.write_bytes(contents)

    def test_no_historical_fallback(self):
        write_bundle(self.folder, pro.SERIES[1])
        with self.assertRaisesRegex(ValueError, 'gemma-base-167520ctx-client'):
            pro.build(self.folder)

    def test_stored_summary_and_both_source_hashes_are_verified(self):
        spec = pro.SERIES[1]
        write_bundle(self.folder, spec)
        path = self.folder / f'{spec.prefix}-summary.json'
        original = json.loads(path.read_text())
        for change in ('number', 'client_hash', 'monitor_hash', 'extra'):
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                if change == 'number': data['sampled_max_running'] = 10
                elif change == 'extra': data['unverified_claim'] = True
                else: data['source_sha256'][change.removesuffix('_hash')] = '0' * 64
                write_json(self.folder, spec, 'summary', data)
                with self.assertRaisesRegex(ValueError, 'summary or source SHA256'):
                    pro.load_bundle(self.folder, spec)

    def test_raw_source_change_is_detected_even_if_json_is_identical(self):
        spec = pro.SERIES[1]
        for suffix in ('client', 'prometheus', 'hardware'):
            with self.subTest(suffix=suffix):
                write_bundle(self.folder, spec)
                path = self.folder / f'{spec.prefix}-{suffix}.json'
                path.write_text(path.read_text() + '\n')
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    pro.load_bundle(self.folder, spec)

    def test_partial_output_cannot_be_promoted_to_32k(self):
        spec = pro.SERIES[1]
        data = write_bundle(self.folder, spec)
        data['client']['passes'][0]['results'][0]['usage']['completion_tokens'] = 4096
        write_json(self.folder, spec, 'client', data['client'])
        with self.assertRaisesRegex(ValueError, 'Token count'):
            pro.load_bundle(self.folder, spec)

    def test_actual_runtime_window_tp_and_gpu_identity_are_required(self):
        spec = pro.SERIES[1]
        for change in ('api', 'flag', 'tp', 'gpu', 'count', 'memory', 'owner', 'model', 'digest', 'restart'):
            with self.subTest(change=change):
                data = write_bundle(self.folder, spec)
                runtime = data['runtime']
                if change == 'api': runtime['effective_max_model_len_from_api'] = 8192
                if change == 'flag': runtime['server_flags_from_actual_pod']['--max-model-len'] = 8192
                if change == 'tp': runtime['server_flags_from_actual_pod']['--tensor-parallel-size'] = 2
                if change == 'gpu': runtime['gpu_model'] = 'NVIDIA H100'
                if change == 'count': runtime['gpu_count'] = 2
                if change == 'memory': runtime['gpu_memory_mib_each'] = 16384
                if change == 'owner': runtime['launch_provenance']['owner_uids_verified'] = False
                if change == 'model': runtime['models'][0]['repository'] = 'wrong/model'
                if change == 'digest': runtime['runtime_image_digest'] = 'latest'
                if change == 'restart': runtime['runtime_restart_count_after'] = 1
                write_json(self.folder, spec, 'runtime', runtime)
                with self.assertRaises(ValueError):
                    pro.load_bundle(self.folder, spec)

    def test_recomputed_summary_with_wrong_model_or_window_is_rejected(self):
        spec = pro.SERIES[1]
        for change in ('model', 'window', 'service'):
            with self.subTest(change=change):
                data = fixture(spec)
                run = data['client']['passes'][0]
                if change == 'window':
                    run['input_tokens'] -= 1
                    for row in run['results']:
                        row['usage']['prompt_tokens'] -= 1
                        row['usage']['total_tokens'] -= 1
                else:
                    label = 'model_name' if change == 'model' else 'service'
                    if change == 'model': run['model'] = 'another-model'
                    else: data['prometheus']['service'] = 'another-model'
                    for group in data['prometheus']['series'].values():
                        for series in group['data']:
                            series['labels'][label] = 'another-model'
                write_bundle(self.folder, spec, data)
                with self.assertRaisesRegex(ValueError, 'series identity'):
                    pro.load_bundle(self.folder, spec)

    def test_hardware_gaps_are_not_filled_or_hidden_by_recomputed_coverage(self):
        spec = pro.SERIES[1]
        for change in ('gap', 'gpu', 'missing_gpu', 'interval', 'group', 'service', 'scrape', 'stale'):
            with self.subTest(change=change):
                data = fixture(spec)
                hardware = data['hardware']
                groups = hardware['series']
                if change == 'gap': groups['gpu'][0]['values'].pop(3)
                if change == 'gpu': groups['gpu'][0]['labels']['modelName'] = 'NVIDIA H100'
                if change == 'missing_gpu': groups['gpu'].pop()
                if change == 'interval': hardware['end_unix'] += 10
                if change == 'group': groups.pop('conditions')
                if change == 'service': groups['runtime_scrape_up'][0]['labels']['service'] = 'other-service'
                if change == 'scrape': groups['runtime_scrape_up'][0]['values'][3][1] = 0
                if change == 'stale': groups['engine_sample_age_seconds'][0]['values'][3][1] = 60
                write_bundle(self.folder, spec, data)
                with self.assertRaises(ValueError):
                    pro.load_bundle(self.folder, spec)

    def test_cli_incomplete_bundle_creates_no_output_directory(self):
        write_bundle(self.folder, pro.SERIES[1])
        output = self.folder / 'not-created'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/pro6000_capacity_results.py'),
                                 '--folder', str(self.folder), '--output-dir', str(output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing evidence', result.stderr)
        self.assertFalse(output.exists())

    def test_cli_default_is_read_only_and_explicit_output_is_non_overwriting(self):
        self.populate()
        command = [sys.executable, str(ROOT / 'scripts/pro6000_capacity_results.py'), '--folder', str(self.folder)]
        before = set(self.folder.iterdir())
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, set(self.folder.iterdir()))
        output = self.folder / 'offline-output'
        command += ['--output-dir', str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in output.iterdir()},
                         {'pro-capacity.md', 'pro-qwen.md', 'pro6000-heavy-result.svg'})
        saved = {p.name: p.read_bytes() for p in output.iterdir()}
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Output exists', result.stderr)
        self.assertEqual(saved, {p.name: p.read_bytes() for p in output.iterdir()})


class ProMatchedResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_recomputes_natural_reply_summary_and_keeps_capacity_separate(self):
        data = write_matched(self.folder)
        self.assertEqual(pro.validate_matched(data), data)
        summary = data['profiles'][0]['summary']
        self.assertEqual([s['completed'] for s in summary], [10, 10, 10, 2, 8])
        self.assertEqual(summary[0]['first_visible_median_s'], 1.5)
        self.assertEqual(summary[0]['actual_output_tokens_total'], 1045)
        self.assertEqual(summary[3]['request_lifetime_overlap'], 8)
        blocks, figures = pro.build_matched(self.folder)
        self.assertEqual(set(blocks), {'pro-chat'})
        self.assertEqual(set(figures), {'pro6000-chat-result'})
        for term in ('40 / 40', '100–109', '32 600–32 609', 'не проверка генерации 32K',
                     'физические GPU различались', 'не WebUI/Bifrost', 'Смешанная нагрузка',
                     'Рассуждения и heartbeat не входят', 'не отдельная оптимизация'):
            self.assertIn(term, blocks['pro-chat'])
        svg = ET.fromstring(figures['pro6000-chat-result'])
        self.assertEqual(svg.attrib['data-design'], 'hardfest-v2')
        text = ' '.join(svg.itertext())
        self.assertIn('не полное окно модели', text)
        self.assertIn('Base', text)
        self.assertIn('Cache', text)
        self.assertIn('Tune', text)
        self.assertNotIn('ускорение', text)

    def test_missing_chat_never_uses_capacity_or_historical_chat(self):
        for spec in pro.SERIES:
            write_bundle(self.folder, spec)
        with self.assertRaisesRegex(ValueError, 'Missing evidence: gemma-matched-chat.json'):
            pro.build_matched(self.folder)

    def test_protocol_and_all_three_profiles_are_mandatory(self):
        for change in ('partial', 'duplicate', 'eos', 'output', 'identity', 'host', 'overlap', 'capture', 'provenance'):
            with self.subTest(change=change):
                data = matched_fixture()
                if change == 'partial': data['profiles'].pop()
                if change == 'duplicate': data['profiles'][1]['profile'] = 'Base'
                if change == 'eos': data['ignore_eos'] = True
                if change == 'output': data['completed_requests'] = 119
                if change == 'identity': data['input_identity_verified'] = False
                if change == 'host': data['same_host_verified'] = False
                if change == 'overlap': data['profiles'][1]['started_at'] = 1001
                if change == 'capture': data['profiles'][0]['runtime_capture_interval']['after'] = 1001
                if change == 'provenance': data['profiles'][0]['capture_sha256'].pop('before_runtime_capture')
                with self.assertRaises(ValueError):
                    pro.validate_matched(data)

    def test_actual_replies_timings_usage_and_summary_are_checked(self):
        for change in ('failed', 'length', 'shortened', 'total', 'timing', 'visible', 'session', 'schedule', 'overlap', 'summary', 'hash', 'answer'):
            with self.subTest(change=change):
                data = matched_fixture()
                profile = data['profiles'][0]
                row = profile['waves'][0]['results'][0]
                if change == 'failed': row['done'] = False
                if change == 'length': row['finish_reason'] = 'length'
                if change == 'shortened': row['usage']['prompt_tokens'] = 1
                if change == 'total': row['usage']['total_tokens'] += 1
                if change == 'timing': row['elapsed_s'] = float('nan')
                if change == 'visible': row['first_visible_text_s'] = 0.5
                if change == 'session': row['session'] = 1
                if change == 'schedule': row['scheduled_delay_s'] = 1
                if change == 'overlap': profile['waves'][3]['short_requests_submitted_while_long_in_flight'] = 0
                if change == 'summary': profile['summary'][0]['first_visible_median_s'] = .01
                if change == 'hash': row['answer_sha256'] = 'unverified'
                if change == 'answer': row['answer_characters'] = 0
                with self.assertRaises(ValueError):
                    pro.validate_matched(data)

    def test_public_sources_runtime_and_artifacts_are_revalidated(self):
        for change in ('document', 'source', 'captured_source', 'client', 'gpu', 'tp', 'window', 'owner', 'artifact'):
            with self.subTest(change=change):
                data = matched_fixture()
                profile = data['profiles'][1]
                runtime = profile['runtime']
                if change == 'document': data['document']['sha256'] = '0' * 64
                if change == 'source': profile['client']['sources'][0]['sha256'] = '0' * 64
                if change == 'captured_source': profile['capture_sha256']['client_matched_suite.py'] = '0' * 64
                if change == 'client': profile['client']['actual_mount_and_command_verified'] = False
                if change == 'gpu': runtime['gpu_model'] = 'NVIDIA H100'
                if change == 'tp': runtime['configured_parameters']['tensor-parallel-size'] = 2
                if change == 'window': runtime['effective_runtime']['max_model_len_from_api'] = 8192
                if change == 'owner': runtime['ownership']['controller_chain_verified'] = False
                if change == 'artifact': runtime['models'][0]['artifact_digest'] = 'sha256:' + 'f' * 64
                with self.assertRaises(ValueError):
                    pro.validate_matched(data)


if __name__ == '__main__':
    unittest.main()
