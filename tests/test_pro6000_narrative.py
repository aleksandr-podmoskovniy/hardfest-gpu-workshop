"""Keep the PRO6000 route readable, honest, and runnable without cluster access."""
import ast
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "examples/pro6000-load-client"


class PRO6000Narrative(unittest.TestCase):
    def test_base_tariff_receipt_does_not_accept_aggregate_billing(self):
        receipt = json.loads((ROOT / 'results/pro6000-heavy/base-tariff-check.json').read_text())
        request, tariff = receipt['request'], receipt['tariff_usd_per_million_tokens']
        expected = (Decimal(request['prompt_tokens']) * Decimal(tariff['input'])
                    + Decimal(request['completion_tokens']) * Decimal(tariff['output'])) / Decimal(1000000)
        self.assertLess(abs(expected - Decimal(request['gateway_cost_usd'])), Decimal('0.000000000001'))
        self.assertEqual(expected, Decimal(request['expected_cost_usd']))
        self.assertEqual(request['total_tokens'], request['prompt_tokens'] + request['completion_tokens'])
        self.assertTrue(request['sse_done'])
        self.assertFalse(receipt['aggregate_budget_verified'])
        self.assertEqual(receipt['base_pricing_verified_on_current_replicas'], receipt['gateway_replica_count'])
        checks = receipt['replica_checks']
        self.assertEqual(len(checks), receipt['gateway_replica_count'])
        self.assertEqual(len({r['replica'] for r in checks}), len(checks))
        for row in checks:
            cost = (Decimal(row['prompt_tokens']) * Decimal(tariff['input'])
                    + Decimal(row['completion_tokens']) * Decimal(tariff['output'])) / Decimal(1000000)
            self.assertEqual(cost, Decimal(row['expected_cost_usd']))
            self.assertEqual(cost, Decimal(row['gateway_cost_usd']))
            self.assertEqual(row['total_tokens'], row['prompt_tokens'] + row['completion_tokens'])
            self.assertTrue(row['sse_done'])
            self.assertTrue(row['request_id_matched_to_log'])
        self.assertFalse(receipt['pricing_cache_after_restart_verified'])
        self.assertFalse(receipt['stopped_models_new_request_cost_verified'])
        self.assertFalse(receipt['budget_usage_manually_changed'])

    def test_story_order_and_terms(self):
        text = (ROOT / "RTXPRO6000.md").read_text()
        self.assertIn("# Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере", text)
        sections = re.findall(r"^## (\d+)\.", text, re.M)
        self.assertEqual(sections, list("1234567"))
        platform = text.index("## 5.")
        self.assertNotIn("InferenceService", text[:platform])
        for term in ("Key–Value cache", "Dynamic Resource Allocation", "Multi-Instance GPU", "Multi-Process Service"):
            self.assertIn(term, text)
        # Preserve exact UI labels while rejecting decorative prose separators.
        for name in ("Gemma A — PRO 6000 · базовая", "Gemma B — PRO 6000 · KV cache", "Gemma B — PRO 6000 · полный тюнинг",
                     "Qwen 3.8 — PRO 6000 · TP2 · 256K"):
            text = text.replace(f"**{name}**", "")
        self.assertNotIn("·", text)

    def test_optional_platform_gemma_closes_access_and_drains_before_scale(self):
        text = (ROOT / "RTXPRO6000.md").read_text().split('<a id="platform-gemma"></a>', 1)[1]
        text = text.split('</details>', 1)[0]
        stop = text.index('У Deployment `selectel-gemma-a`')
        for guard in ('`workshop.vllm/selectel-gemma-a`', '**⋯ / Make Private**',
                      '**⋯ / Доступ / Частное**', 'разрешения участников и групп удаляются',
                      '**Running = 0 и Waiting = 0**'):
            self.assertLess(text.index(guard), stop)

    def test_stop_guidance_accounts_for_requests_waiting_before_vllm(self):
        guide = (ROOT / 'docs/CHAT_AND_ACCESS.md').read_text()
        before_stop = guide.split('<a id="before-stop"></a>', 1)[1].split('\n## ', 1)[0]
        for point in ('завершение отправленных сообщений в WebUI',
                      'связанных запросов шлюза', 'включая фоновые',
                      '**Пустая очередь vLLM недостаточна:**',
                      'или удалять InferenceService нельзя'):
            self.assertIn(point, before_stop)
        article = (ROOT / 'RTXPRO6000.md').read_text()
        repeat = article.split('<summary>Удалить свой заказ и создать его снова с тем же именем</summary>', 1)[1]
        repeat = repeat.split('</details>', 1)[0]
        self.assertLess(repeat.index('docs/CHAT_AND_ACCESS.md#before-stop'),
                        repeat.index('В Console удалить только'))

    def test_visible_limits_and_long_budget(self):
        text = (ROOT / "RTXPRO6000.md").read_text()
        for value in ("229 376", "32 768", "262 144", "107,81", "207,81", "103,91"):
            self.assertIn(value, text)
        for caveat in ("их совместный результат", "ручная B и платформенная Gemma",
                       "не попадают в учёт VK", "YAML сервиса\nне фиксирует все параметры",
                       "Десять одновременно отправленных запросов ещё не означают десять активных"):
            self.assertIn(caveat, text)
        self.assertIn("examples/pro6000-load-client/README.md", text)
        self.assertIn("results/pro6000-heavy/README.md", text)
        self.assertNotIn("results/pro6000/", text)
        evidence = (ROOT / "results/pro6000-heavy/README.md").read_text()
        self.assertIn("Обычный ответ на одинаковых входах", evidence)
        self.assertIn("gemma-matched-chat.json", evidence)

    def test_archived_clients_have_exact_checksums_and_parse(self):
        entries = (CLIENT / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(entries), 5)
        for row in entries:
            checksum, name = row.split()
            data = (CLIENT / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), checksum, name)
            ast.parse(data, filename=name)

    def test_public_lifecycle_separates_functional_success_from_degraded_performance(self):
        folder = ROOT / 'results/pro6000-heavy/qwen-console-lifecycle'
        manifest = json.loads((folder / 'manifest.json').read_text())
        lifecycle = manifest['lifecycle']
        self.assertEqual(lifecycle['status'], 'passed')
        self.assertEqual(lifecycle['acceptance_scope'], 'functional_create_delete_recreate')
        self.assertEqual(lifecycle['performance']['status'], 'degraded')
        self.assertFalse(lifecycle['performance']['latency_slo_accepted'])
        self.assertFalse(lifecycle['performance']['reliability_slo_accepted'])
        replies = lifecycle['replies']
        self.assertEqual([r['gateway_latency_ms'] for r in replies], [6530, 135978])
        self.assertEqual([(r['actual_usage']['prompt_tokens'], r['actual_usage']['completion_tokens'])
                          for r in replies], [(77, 481), (77, 132)])
        background = replies[1]['observed_background_requests']
        self.assertEqual(background['observed_error_count'], 1)
        self.assertFalse(background['pagination_completeness_verified'])
        self.assertIn({'status': 'error', 'gateway_latency_ms': 133267, 'provider_key_verified': False},
                      background['requests'])
        self.assertEqual(manifest['image'], 'qwen-console-initial-reply.png')
        self.assertEqual(hashlib.sha256((folder / manifest['image']).read_bytes()).hexdigest(), manifest['image_sha256'])
        self.assertFalse(manifest['gateway_cost']['aggregate_billing_verified'])
        for filename in ('RTXPRO6000.md', 'results/pro6000-heavy/README.md'):
            text = (ROOT / filename).read_text()
            for duration in ('6,530 с', '135,978 с', '133,267 с'):
                self.assertIn(duration, text)
            self.assertIn('не TTFT', text)
            self.assertNotIn('Полный цикл через Console пока не подтверждён', text)

    def test_matched_gate_is_offline_without_execute(self):
        command = [sys.executable, str(CLIENT / "chat_comparison_probe.py")]
        for stage in ("a", "b"):
            command += ["--url-" + stage, "http://127.0.0.1:1",
                        "--capacity-report-" + stage,
                        str(ROOT / f"results/pro6000/gemma-{stage}-capacity-sse32k.final.json")]
        command += ["--report", "unused-pro6000-offline-report.json"]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Capacity gates passed", completed.stdout)
        self.assertFalse((ROOT / "unused-pro6000-offline-report.json").exists())

    def test_bootstrap_and_manual_ui_scaling_are_explicit(self):
        text = (ROOT / "docs/GITOPS.md").read_text()
        for token in ("pro6000-bootstrap", "values/pro6000/", "argocd/pro6000/", "site/pro6000.yaml",
                      "hardfest-pro6000-gemma-cache", "RespectIgnoreDifferences=true"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
