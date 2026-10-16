import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plot_capacity
import hardware_coverage

spec = importlib.util.spec_from_file_location('heavy_capacity', ROOT / 'scripts/heavy_capacity.py')
heavy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(heavy)


def fixture():
    rows = [dict(session=i, ok=True, done=True, finish_reason='length', ttft_s=10.,
                 elapsed_s=300., last_content_s=299., submitted_at=200.+i/1000,
                 completed_at=500.+i/1000,
                 usage=dict(prompt_tokens=98304, completion_tokens=32768, total_tokens=131072))
            for i in range(10)]
    client = dict(run_id='synthetic-unit-test', ignore_eos=True, passes=[dict(
        ok=True, model='model-under-test', sessions=10, input_tokens=98304, output_tokens=32768,
        elapsed_s=300.009, results=rows)])
    monitor = dict(service='service-under-test', start_unix=199, end_unix=509, step_seconds=10, series={})
    finals = dict(output_tokens=327680, completed_output_tokens=327680, completed_output_count=10,
                  preemptions=0, running=0, waiting=0, gpu_kv_fraction=0)
    for metric in ('ttft', 'tpot', 'queue', 'prefill', 'decode', 'e2e'):
        finals[metric+'_count'] = 10
        finals[metric+'_sum'] = 100
    finals['decode_sum'] = 100*32767
    for key, last in finals.items():
        name, unit = heavy.METRICS[key]
        values = [[t, last if t == 509 else 0] for t in range(199, 510, 10)]
        if key in ('running', 'waiting', 'gpu_kv_fraction'):
            values[1][1] = {'running': 8, 'waiting': 2, 'gpu_kv_fraction': .9}[key]
        monitor['series'][key] = dict(unit=unit, data=[dict(
            labels=dict(__name__=name, service='service-under-test', model_name='model-under-test'), values=values)])
    monitor['series']['success_by_reason'] = dict(unit='requests', data=[dict(
        labels=dict(__name__='vllm:request_success_total', service='service-under-test',
                    model_name='model-under-test', finished_reason=reason),
        values=[[t, 10 if reason == 'length' and t == 509 else 0] for t in range(199, 510, 10)])
        for reason in ('length', 'error', 'abort')])
    return client, monitor


class HeavyCapacityTests(unittest.TestCase):
    def test_gemma_startup_failures_are_not_load_results(self):
        failures = json.loads((ROOT / 'results/rtx5060-heavy/gemma-base-startup-failures.json').read_text())
        self.assertEqual(failures['load_requests_sent'], 0)
        self.assertEqual([a['window_tokens'] for a in failures['attempts']], [131072, 65536, 49152, 40960, 36864])
        for attempt in failures['attempts']:
            self.assertEqual(attempt['phase'], 'startup')
            self.assertTrue(attempt['replicas_zero_verified'])
            if attempt['window_tokens'] == 40960:
                self.assertEqual(attempt['failure'], 'insufficient KV cache budget')
                self.assertIn('ValueError: No available memory for the cache blocks.', attempt['error_excerpt'])
                self.assertEqual(attempt['memory_budget_excerpt'], 'Available KV cache memory: -0.41 GiB')
            elif attempt['window_tokens'] == 36864:
                self.assertEqual(attempt['failure'], 'insufficient KV cache budget')
                self.assertIn('ValueError: To serve at least one request', attempt['error_excerpt'])
                self.assertEqual(attempt['memory_budget_excerpt'], 'Available KV cache memory: 0.12 GiB')
            else:
                self.assertEqual(attempt['failure'], 'CUDA OOM')
                self.assertIn('torch.OutOfMemoryError: CUDA out of memory.', attempt['error_excerpt'])
            self.assertIn('does not officially support disabling chunked prefill', attempt['runtime_warning'])
            self.assertTrue(attempt['server_flags']['no-enable-chunked-prefill'])
            self.assertEqual(attempt['server_flags']['max-num-seqs'], 10)
            self.assertNotIn('completed', attempt)

    def test_completed_wave_is_not_ten_active_claim(self):
        out = heavy.summarize(*fixture())
        self.assertEqual(out['completed'], 10)
        self.assertEqual(out['sampled_max_running'], 8)
        self.assertTrue(out['output_counter_reconciled'])
        self.assertIn('not normal chat', out['comparison_limit'])

    def test_short_response_not_promoted_by_configured_limit(self):
        c, m = fixture()
        c['passes'][0]['results'][0]['usage']['completion_tokens'] = 14
        with self.assertRaisesRegex(ValueError, 'Token count'):
            heavy.summarize(c, m)

    def test_reduced_input_does_not_shorten_the_required_output(self):
        for model in ('rtx-gemma-base', 'rtx-gemma-cache'):
            for tokens in (4096, 8192, 16384):
                with self.subTest(model=model, input_tokens=tokens):
                    c, m = fixture()
                    c['passes'][0]['input_tokens'] = tokens
                    c['passes'][0]['model'] = model
                    for group in m['series'].values():
                        for series in group['data']:
                            series['labels']['model_name'] = model
                    for row in c['passes'][0]['results']:
                        row['usage']['prompt_tokens'] = tokens
                        row['usage']['total_tokens'] = tokens + 32768
                    result = heavy.summarize(c, m)
                    self.assertEqual(result['window_tokens'], tokens + 32768)
                    self.assertEqual(result['output_tokens_each'], 32768)
                    for other_model in ('rtx-qwen35-9b', 'rtx-gemma-tune'):
                        c['passes'][0]['model'] = other_model
                        with self.assertRaisesRegex(ValueError, 'protocol'):
                            heavy.summarize(c, m)
                    c['passes'][0]['model'] = model
                    c['passes'][0]['input_tokens'] = tokens - 1
                    with self.assertRaisesRegex(ValueError, 'protocol'):
                        heavy.summarize(c, m)
                    c['passes'][0]['input_tokens'] = tokens
                    c['passes'][0]['results'][0]['usage']['completion_tokens'] = 32767
                    with self.assertRaisesRegex(ValueError, 'Token count'):
                        heavy.summarize(c, m)

    def test_rejects_partial_or_duplicate_or_failed_streams(self):
        original, m = fixture()
        for key, value in [('done', False), ('ok', False), ('watchdog_reason', 'deadline'), ('session', 9)]:
            with self.subTest(key=key):
                c = copy.deepcopy(original)
                c['passes'][0]['results'][0][key] = value
                with self.assertRaises(ValueError):
                    heavy.summarize(c, m)
        with self.assertRaisesRegex(ValueError, 'Final report'):
            heavy.summarize({'ignore_eos': True, 'results': original['passes'][0]['results']}, m)

    def test_wrong_metrics_cannot_validate_client(self):
        c, original = fixture()
        for change in ('counter', 'model', 'unit', 'name', 'gap', 'nan'):
            with self.subTest(change=change):
                m = copy.deepcopy(original)
                metric = m['series']['output_tokens']
                series = metric['data'][0]
                if change == 'counter': series['values'][-1][1] = 327679
                if change == 'model': series['labels']['model_name'] = 'another-model'
                if change == 'unit': metric['unit'] = 'seconds'
                if change == 'name': series['labels']['__name__'] = 'another_metric'
                if change == 'gap': series['values'].pop(5)
                if change == 'nan': series['values'][-1][1] = float('nan')
                with self.assertRaises(ValueError):
                    heavy.summarize(c, m)

    def test_h100_reference_does_not_masquerade_as_capacity(self):
        reference = json.loads((ROOT / 'results/h100-reference.json').read_text())
        self.assertFalse(reference['new_run'])
        self.assertEqual(reference['common']['clients'], 1)
        self.assertEqual(reference['common']['output_tokens_each'], 2048)
        a, b = reference['runs']
        self.assertLess(a['ttft_seconds'], b['ttft_seconds'])
        self.assertGreater(a['e2e_seconds'], b['e2e_seconds'])

    def test_archived_rtx_evidence_reconciles_with_real_runtime(self):
        folder = ROOT / 'results/rtx5060-heavy'
        clients = sorted(folder.glob('*-client.json'))
        self.assertTrue(clients)
        for client_path in clients:
            with self.subTest(file=client_path.name):
                prefix = client_path.name.removesuffix('-client.json')
                monitor_path = folder / (prefix + '-prometheus.json')
                client, monitor = json.loads(client_path.read_text()), json.loads(monitor_path.read_text())
                summary = json.loads((folder / (prefix + '-summary.json')).read_text())
                expected = heavy.summarize(client, monitor)
                self.assertEqual({k: v for k, v in summary.items() if k != 'source_sha256'}, expected)
                self.assertEqual(summary['source_sha256'], {
                    'client': hashlib.sha256(client_path.read_bytes()).hexdigest(),
                    'monitor': hashlib.sha256(monitor_path.read_bytes()).hexdigest()})
                runtime = json.loads((folder / (prefix + '-runtime.json')).read_text())
                flags = runtime['server_flags_from_actual_pod']
                self.assertEqual(expected['window_tokens'], int(flags['--max-model-len']))
                self.assertLessEqual(expected['sampled_max_running'], int(flags['--max-num-seqs']))
                self.assertEqual(runtime['runtime_restart_count_after'], 0)
                self.assertEqual(runtime['vllm_version'], '0.31.0')
                provenance = runtime['launch_provenance']
                self.assertTrue(provenance['owner_uids_verified'])
                self.assertIn(provenance['mode'], ('ai-inference', 'manual-deployment'))
                expected_root = 'InferenceService' if provenance['mode'] == 'ai-inference' else 'Deployment'
                self.assertEqual(provenance['owner_chain_kinds'][0], 'Pod')
                self.assertEqual(provenance['owner_chain_kinds'][-1], expected_root)
                client_source = runtime.get('client_source_directory', 'examples/pro6000-load-client')
                self.assertIn(client_source, ('examples/pro6000-load-client', 'examples/capacity-load-client'))
                for name, digest in runtime['client_script_sha256'].items():
                    self.assertIn(name, ('inference-probe.py', 'probe_transport.py', 'run_probe_with_hold.py'))
                    self.assertEqual(hashlib.sha256((ROOT / client_source / name).read_bytes()).hexdigest(), digest)
                hardware = json.loads((folder / (prefix + '-hardware.json')).read_text())
                coverage = hardware_coverage.coverage((folder / (prefix + '-hardware.json')).read_bytes())
                self.assertEqual(coverage, json.loads((folder / (prefix + '-hardware-coverage.json')).read_text()))
                if prefix == 'qwen-256k':
                    # One recorded kube-state scrape gap, not a general waiver.
                    expected_gaps = [
                        {'group': 'conditions', 'labels': {
                            '__name__': 'kube_node_status_condition',
                            'condition': condition, 'status': 'true'},
                         'missing_evaluations': 3,
                         'missing_timestamps_unix': [1792068060, 1792068070, 1792068080]}
                        for condition in ('DiskPressure', 'MemoryPressure', 'PIDPressure', 'Ready')]
                    expected_gaps.append({'group': 'restarts', 'labels': {
                        '__name__': 'kube_pod_container_status_restarts_total', 'container': 'runtime'},
                        'missing_evaluations': 3,
                        'missing_timestamps_unix': [1792068060, 1792068070, 1792068080]})
                    self.assertEqual(coverage['gaps'], expected_gaps)
                    self.assertEqual(coverage['affected_series'], 5)
                    self.assertEqual(coverage['missing_evaluations'], 15)
                else:
                    self.assertTrue(coverage['fully_covered'])
                self.assertTrue(hardware['series']['runtime_memory_working_set'])
                start = min(r['submitted_at'] for r in expected['requests'])
                end = max(r['completed_at'] for r in expected['requests'])
                for group in hardware['series'].values():
                    for series in group:
                        self.assertLessEqual(series['values'][0][0], start)
                        self.assertGreaterEqual(series['values'][-1][0], end)
                        # Exact gap policy is checked above; no interpolation.
                        self.assertTrue(all(b[0] > a[0] for a, b in zip(series['values'], series['values'][1:])))
                for series in hardware['series']['conditions']:
                    required = 1 if series['labels']['condition'] == 'Ready' else 0
                    self.assertTrue(all(v == required for t, v in series['values']))
                self.assertTrue(all(v == 0 for s in hardware['series']['restarts'] for t, v in s['values']))

    def test_hardware_coverage_records_missing_points_and_source_hash(self):
        fixture = {'start_unix': 100, 'end_unix': 140, 'step_seconds': 10,
                   'series': {'example': [{'labels': {'metric': 'fixture'},
                                          'values': [[100, 0], [140, 0]]}]}}
        raw = json.dumps(fixture).encode()
        report = hardware_coverage.coverage(raw)
        self.assertEqual(report['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(report['fully_covered'])
        self.assertEqual(report['gaps'][0]['missing_timestamps_unix'], [110, 120, 130])
        self.assertEqual(fixture['series']['example'][0]['values'], [[100, 0], [140, 0]])
        for invalid in ([[100, 0], [100, 1]], [[105, 0]], [[100, float('nan')]]):
            fixture['series']['example'][0]['values'] = invalid
            with self.assertRaises(ValueError):
                hardware_coverage.coverage(json.dumps(fixture).encode())

    def test_rtx128_mtp_acceptance_is_observed_not_assumed(self):
        monitor = json.loads((ROOT / 'results/rtx5060-heavy/qwen-128k-prometheus.json').read_text())
        for key in ('spec_accepted', 'spec_proposed'):
            series = monitor['series'][key]['data']
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]['values'][-1][1] - series[0]['values'][0][1], 163827)

    def test_rtx128_was_created_by_ai_inference(self):
        runtime = json.loads((ROOT / 'results/rtx5060-heavy/qwen-128k-runtime.json').read_text())
        provenance = runtime['launch_provenance']
        self.assertEqual(provenance['mode'], 'ai-inference')
        self.assertEqual(provenance['owner_chain_kinds'], ['Pod', 'StatefulSet', 'InferenceService'])
        self.assertEqual(provenance['launch_strategy'], 'Throughput')

    def test_rtx128_container_ram_is_not_node_ram_or_configured_offload(self):
        hardware = json.loads((ROOT / 'results/rtx5060-heavy/qwen-128k-hardware.json').read_text())
        series = hardware['series']['runtime_memory_working_set']
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]['labels'], {
            '__name__': 'container_memory_working_set_bytes', 'container': 'runtime'})
        self.assertEqual(max(v for _, v in series[0]['values']), 26684538880)

    def test_capacity_figures_are_generated_from_archived_evidence(self):
        for client in sorted((ROOT / 'results/rtx5060-heavy').glob('*-client.json')):
            with self.subTest(file=client.name):
                subprocess.run([sys.executable, str(ROOT / 'scripts/plot_capacity.py'),
                                '--prefix', client.name.removesuffix('-client.json'), '--check'],
                               check=True, capture_output=True, text=True)

    def test_figure_names_the_actual_model_gpu_count_and_launch_method(self):
        runtime = json.loads((ROOT / 'results/rtx5060-heavy/qwen-128k-runtime.json').read_text())
        title, launch = plot_capacity.describe({'model': 'rtx-qwen35-9b', 'window_tokens': 131072}, runtime)
        self.assertIn('Qwen, 2 GPU, 128K', title)
        self.assertIn('AI Inference.', launch)
        for stage in ('base', 'cache', 'tune'):
            gemma = copy.deepcopy(runtime)
            gemma.pop('model')
            gemma['models'] = [{'repository': 'google/gemma-4-E2B-it'}]
            if stage == 'tune':
                gemma['models'].append({'repository': 'google/gemma-4-E2B-it-assistant'})
            gemma['served_model_names'] = ['rtx-gemma-' + stage]
            gemma['server_flags_from_actual_pod']['--tensor-parallel-size'] = 1
            gemma['launch_provenance'] = dict(mode='manual-deployment', owner_uids_verified=True,
                owner_chain_kinds=['Pod', 'ReplicaSet', 'Deployment'])
            title, launch = plot_capacity.describe({'model': 'rtx-gemma-' + stage, 'window_tokens': 131072}, gemma)
            self.assertIn('Gemma ' + stage.title() + ', 1 GPU', title)
            self.assertIn('не проверка платформы', launch)
            self.assertIn('без WebUI/Bifrost', launch)
            gemma['models'][0]['repository'] = 'Qwen/Qwen3.5-9B'
            with self.assertRaisesRegex(ValueError, 'model identity'):
                plot_capacity.describe({'model': 'rtx-gemma-' + stage, 'window_tokens': 131072}, gemma)
        runtime['launch_provenance']['owner_chain_kinds'][-1] = 'Deployment'
        with self.assertRaisesRegex(ValueError, 'Unverified platform'):
            plot_capacity.describe({'model': 'rtx-qwen35-9b', 'window_tokens': 131072}, runtime)

    def test_pro_figure_keeps_auto_window_exact_and_checks_actual_hardware(self):
        runtime = {
            'models': [{'repository': 'google/gemma-4-31B-it'}],
            'served_model_names': ['selectel-gemma-a'],
            'gpu_model': 'NVIDIA RTX PRO 6000 Blackwell Server Edition',
            'gpu_count': 1, 'effective_max_model_len_from_api': 167520,
            'server_flags_from_actual_pod': {'--tensor-parallel-size': 1, '--max-model-len': -1},
            'launch_provenance': {'mode': 'manual-deployment', 'owner_uids_verified': True,
                                 'owner_chain_kinds': ['Pod', 'ReplicaSet', 'Deployment']},
        }
        result = {'model': 'selectel-gemma-a', 'window_tokens': 167520}
        title, launch = plot_capacity.describe(result, runtime, 'pro6000')
        self.assertIn('167 520 токенов', title)
        self.assertNotIn('163K', title)
        self.assertIn('не проверка платформы', launch)
        for key, value in [('gpu_count', 2), ('gpu_model', 'NVIDIA H100'),
                           ('effective_max_model_len_from_api', 262144)]:
            changed = copy.deepcopy(runtime)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                plot_capacity.describe(result, changed, 'pro6000')
        runtime['server_flags_from_actual_pod']['--max-model-len'] = 131072
        with self.assertRaises(ValueError):
            plot_capacity.describe(result, runtime, 'pro6000')

    def test_pro_qwen_figure_requires_the_platform_and_correct_model(self):
        runtime = {
            'models': [{'repository': 'nvidia/Qwen3.8-Flash-Next-NVFP4'}],
            'served_model_names': ['qwen3-8-flash-next-nvfp4'],
            'gpu_model': 'NVIDIA RTX PRO 6000 Blackwell Server Edition',
            'gpu_count': 2, 'effective_max_model_len_from_api': 262144,
            'server_flags_from_actual_pod': {'--tensor-parallel-size': 2, '--max-model-len': 262144},
            'launch_provenance': {'mode': 'ai-inference', 'owner_uids_verified': True,
                                 'owner_chain_kinds': ['Pod', 'StatefulSet', 'InferenceService']},
        }
        result = {'model': 'qwen3-8-flash-next-nvfp4', 'window_tokens': 262144}
        title, launch = plot_capacity.describe(result, runtime, 'pro6000')
        self.assertIn('Qwen, 2 GPU, 262 144 токенов', title)
        self.assertIn('AI Inference.', launch)
        runtime['launch_provenance']['mode'] = 'manual-deployment'
        with self.assertRaisesRegex(ValueError, 'launch method'):
            plot_capacity.describe(result, runtime, 'pro6000')


if __name__ == '__main__':
    unittest.main()
