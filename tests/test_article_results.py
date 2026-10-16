"""Article scorecards must remain traceable to the recorded workload, not marketing ratios."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import article_results as results
from test_pro6000_capacity_results import write_bundle, write_matched


class ArticleResults(unittest.TestCase):
    def test_generated_tables_and_figures_match_source_measurements(self):
        # Existing H100/RTX is independently reproducible while fresh PRO runs finish.
        for path, expected in results.outputs(scope="h100-rtx").items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(), expected)

    def test_rtx_controls_reject_partial_or_different_workloads(self):
        original = results.read("results/rtx5060-rehearsal-20261008.json")
        for key, value in (("failed", 1), ("completed", 7), ("max_concurrency", 8),
                           ("total_output_tokens", 32768), ("total_input_tokens", 65536)):
            with self.subTest(key=key):
                changed = deepcopy(original)
                changed["results"][0]["result"][key] = value
                with self.assertRaisesRegex(ValueError, "Incomparable workload"):
                    results.rtx_results(changed)
        changed = deepcopy(original)
        changed["profiles"]["tune-4k"]["max_model_len"] = 131072
        with self.assertRaisesRegex(ValueError, "Changed control profile"):
            results.rtx_results(changed)
        with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
            results.rtx_results(original | {"results": original["results"][1:]})

    def test_retained_figure_labels_include_workload_limits(self):
        _, figures = results.build(scope="h100-rtx")
        texts = {name: " ".join(ET.fromstring(svg).itertext()) for name, svg in figures.items()}
        h100 = texts["h100-offload-result"]
        for phrase in ("Offload включён в обоих", "65 536", "128 выхода", "vLLM 0.30", "не ёмкости"):
            self.assertIn(phrase, h100)
        rtx = texts["rtx5060-tuning-result"]
        for phrase in ("Все профили 4K", "средние", "ток/с — вся серия", "не измеряет текущий Tune"):
            self.assertIn(phrase, rtx)
        self.assertNotIn("pro6000-chat-result", texts)
        self.assertNotIn("pro6000-heavy-result", texts)

    def test_heavy_load_not_short_diagnostic_is_the_primary_claim(self):
        pro = (ROOT / "RTXPRO6000.md").read_text()
        diagnostic_heading = '<summary>Одинаковые входы для Base, Cache и Tune: обычный ответ'
        self.assertLess(pro.index('assets/pro6000-heavy-result.svg'), pro.index(diagnostic_heading))
        diagnostic = pro.split(diagnostic_heading, 1)[1].split('</details>', 1)[0]
        if 'assets/pro6000-chat-result.svg' not in diagnostic:
            chat = diagnostic.split('<!-- result:pro-chat -->', 1)[1].split('<!-- /result:pro-chat -->', 1)[0].strip()
            self.assertEqual(chat, results.CAPACITY_ONLY_CHAT_NOTICE)
            self.assertNotIn('gemma-matched-chat.json', diagnostic)
        else:
            self.assertLess(pro.index('assets/pro6000-heavy-result.svg'), pro.index('assets/pro6000-chat-result.svg'))
        protocol = (ROOT / "docs/MEASUREMENTS.md").read_text().split('id="heavy-load"', 1)[1].split('## Короткие', 1)[0]
        for term in ('Base-long', 'Cache-long', '32 768', '327680',
                     '--num-prompts 10 --max-concurrency 10', 'не новые результаты',
                     '12 часов', 'не является хорошим результатом для чата'):
            self.assertIn(term, protocol)

    def test_incomplete_new_pro_blocks_default_and_never_reads_historical_pro(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            write_bundle(folder, results.pro6000.SERIES[1])
            for scope in ("all", "pro", "pro-capacity-only"):
                with self.subTest(scope=scope), patch.object(results, 'read', side_effect=AssertionError('No historical fallback')):
                    with self.assertRaisesRegex(ValueError, 'Missing evidence'):
                        results.build(scope=scope, pro_folder=folder)
                    with self.assertRaisesRegex(ValueError, 'Missing evidence'):
                        results.outputs(scope=scope, pro_folder=folder)

    def test_h100_rtx_scope_does_not_touch_pro_or_its_files(self):
        with patch.object(results.pro6000, 'build', side_effect=AssertionError('PRO must be excluded')):
            generated = results.outputs(scope='h100-rtx')
        self.assertEqual({path.name for path in generated},
                         {'README.md', 'RTX5060.md', 'h100-offload-result.svg', 'rtx5060-tuning-result.svg'})

    def test_full_capacity_without_matched_does_not_create_partial_new_pro(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            for spec in results.pro6000.SERIES:
                write_bundle(folder, spec)
            for scope in ('all', 'pro'):
                with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'gemma-matched-chat.json'):
                    results.outputs(scope=scope, pro_folder=folder)

    def test_explicit_capacity_only_replaces_old_chat_block_without_touching_files(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            for spec in results.pro6000.SERIES:
                write_bundle(folder, spec)
            paths = (ROOT / 'RTXPRO6000.md', ROOT / 'README.md', ROOT / 'RTX5060.md',
                     ROOT / 'assets/pro6000-chat-result.svg')
            originals = {path: path.read_bytes() if path.exists() else None for path in paths}
            with patch.object(results, 'read', side_effect=AssertionError('No historical fallback')), \
                    patch.object(results.pro6000, 'build_matched', side_effect=AssertionError('No matched data')):
                generated = results.outputs(scope='pro-capacity-only', pro_folder=folder)
            self.assertEqual({path.name for path in generated},
                             {'RTXPRO6000.md', 'pro6000-heavy-result.svg'})
            text = generated[ROOT / 'RTXPRO6000.md']
            chat = text.split('<!-- result:pro-chat -->', 1)[1].split('<!-- /result:pro-chat -->', 1)[0].strip()
            self.assertEqual(chat, results.CAPACITY_ONLY_CHAT_NOTICE)
            for phrase in ('ещё не завершена', 'разные окна', 'нельзя вычислять ускорение'):
                self.assertIn(phrase, chat)
            for obsolete in ('gemma-matched-chat.json', '0,308', '330,565', '1,724', '|', ']('):
                self.assertNotIn(obsolete, chat)
            self.assertIn('| Gemma: полное окно | Base | Cache | Tune |', text)
            self.assertIn('| Qwen TP2: полное окно | Измерено |', text)
            self.assertEqual(originals, {path: path.read_bytes() if path.exists() else None for path in paths})

    def test_capacity_only_does_not_hide_existing_matched_export(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            for spec in results.pro6000.SERIES:
                write_bundle(folder, spec)
            for content in ('not JSON', '{}'):
                with self.subTest(content=content):
                    (folder / 'gemma-matched-chat.json').write_text(content)
                    with self.assertRaisesRegex(ValueError, 'Matched evidence exists'):
                        results.outputs(scope='pro-capacity-only', pro_folder=folder)
            write_matched(folder)
            with self.assertRaisesRegex(ValueError, 'Matched evidence exists'):
                results.outputs(scope='pro-capacity-only', pro_folder=folder)
            self.assertIn(ROOT / 'assets/pro6000-chat-result.svg',
                          results.outputs(scope='pro', pro_folder=folder))

    def test_capacity_only_cli_checks_without_writing_or_relaxing_validation(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            command = [sys.executable, str(ROOT / 'scripts/article_results.py'), '--check',
                       '--scope', 'pro-capacity-only', '--pro-folder', str(folder)]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('Missing evidence', missing.stderr)
            for spec in results.pro6000.SERIES:
                write_bundle(folder, spec)
            paths = (ROOT / 'RTXPRO6000.md', ROOT / 'assets/pro6000-heavy-result.svg',
                     ROOT / 'assets/pro6000-chat-result.svg')
            originals = {path: path.read_bytes() if path.exists() else None for path in paths}
            stale = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn('Stale article results', stale.stderr)
            self.assertNotIn('Missing evidence', stale.stderr)
            self.assertEqual(originals, {path: path.read_bytes() if path.exists() else None for path in paths})

    def test_fresh_generation_in_memory_preserves_unselected_sections(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            for spec in results.pro6000.SERIES:
                write_bundle(folder, spec)
            write_matched(folder)
            originals = {path: path.read_bytes() for path in (ROOT / 'RTXPRO6000.md', ROOT / 'README.md', ROOT / 'RTX5060.md')}
            pro = results.outputs(scope='pro', pro_folder=folder)
            self.assertEqual({path.name for path in pro}, {'RTXPRO6000.md', 'pro6000-heavy-result.svg', 'pro6000-chat-result.svg'})
            fresh_text = pro[ROOT / 'RTXPRO6000.md']
            self.assertIn('| Gemma: полное окно | Base | Cache | Tune |', fresh_text)
            self.assertIn('gemma-matched-chat.json', fresh_text)
            self.assertIn('40 / 40', fresh_text)
            self.assertIn('Пик Running по метрикам', fresh_text)
            self.assertEqual(originals, {path: path.read_bytes() for path in originals})
            all_outputs = results.outputs(pro_folder=folder)
            for path, text in results.outputs(scope='h100-rtx').items():
                self.assertEqual(all_outputs[path], text)
            for path, text in pro.items():
                self.assertEqual(all_outputs[path], text)

    def test_cli_scope_is_explicit_and_missing_pro_is_an_error(self):
        command = [sys.executable, str(ROOT / 'scripts/article_results.py'), '--check']
        result = subprocess.run(command + ['--scope', 'h100-rtx'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('scope: h100-rtx', result.stdout)
        with tempfile.TemporaryDirectory() as name:
            result = subprocess.run(command + ['--pro-folder', name], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing evidence', result.stderr)
        with self.assertRaisesRegex(ValueError, 'Unknown article results scope'):
            results.build(scope='legacy-pro')

    def test_h100_card_rejects_a_local_gpu_hit_presented_as_ram_replay(self):
        original_read = results.read

        def changed_read(name):
            value = original_read(name)
            if name == "results/kv-ram/observations.json":
                row = next(row for row in value["return_request_counter_samples"]
                           if row["name"] == "vllm:prefix_cache_hits_total")
                row["after"] += 32
            return value

        with patch.object(results, "read", changed_read):
            with self.assertRaisesRegex(ValueError, "H100 replay counters"):
                results.build(scope='h100-rtx')

    def test_main_prose_does_not_hide_negative_or_capacity_findings(self):
        # New PRO statements are checked against fixtures above, not stale prose.
        rtx = (ROOT / "RTX5060.md").read_text()
        for phrase in ("первые проходы", "не проверка полного окна", "CPU-лимиты профилей различались"):
            self.assertIn(phrase, rtx)
        h100 = (ROOT / "README.md").read_text()
        self.assertIn("Сопоставимого отчёта по всем трём профилям H100 — Base, Cache и Tune — в материалах нет", h100)


if __name__ == "__main__":
    unittest.main()
