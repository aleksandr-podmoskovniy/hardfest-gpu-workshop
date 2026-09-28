# 2. KV-кэш в RAM

Гипотеза: повтор большого документа после вытеснения из HBM обходится дешевле полного prefill. Это **не** гипотеза о неограниченном контексте или переносе активных сессий по приоритету пользователя.

Профиль [gemma-b-cache.json](../manifests/profiles/gemma-b-cache.json) добавляет к tuned **один механизм**:

```json
{"kv-transfer-config":{"kv_connector":"OffloadingConnector","kv_role":"kv_both","kv_connector_extra_config":{"cpu_bytes_to_use":68719476736}}}
```

64 GiB — стартовый бюджет эксперимента, не автоматический вывод из марки H100. В данном connector бюджет общий на workers. Сопоставить его с доступной RAM и memory limit Pod. Это не `--cpu-offload-gb` и не размер выгружаемых весов. [Версионная документация](https://docs.vllm.ai/en/v0.30.0/features/kv_offloading_usage/).

## Эксперимент

Для готового `.local/long.jsonl` из 32 документов есть точная последовательность запросов. Она может не поместиться в live-слот; сначала выполнить на репетиции. B свежий, port-forward T2 переподключён:

```bash
python3 scripts/bench.py --url http://127.0.0.1:18002 --dataset .local/long.jsonl \
  --offset 0 --requests 1 --concurrency 1 --metrics --label x-first --out-dir results/raw/cache
python3 scripts/bench.py --url http://127.0.0.1:18002 --dataset .local/long.jsonl \
  --offset 1 --requests 31 --concurrency 4 --cold --metrics --label other-docs --out-dir results/raw/cache
python3 scripts/bench.py --url http://127.0.0.1:18002 --dataset .local/long.jsonl \
  --offset 0 --requests 1 --concurrency 1 --metrics --label x-return --out-dir results/raw/cache
python3 scripts/report.py --directory results/raw/cache
```

Сначала та же серия на tuned без CPU-tier, затем на cache с новым engine и отдельной папкой результатов. Не смешивать X и other-docs в одну цифру speedup. 31 документ — воспроизводимая нагрузка, **не гарантия eviction**: подтвердить вытеснение и CPU reload.

1. Сохранить tuned-серию без CPU-tier: документ X → документы Y… → X.
2. Остановить B; применить cache-профиль; запустить и переподключить port-forward.
3. Прогреть engine отдельным документом, не X.
4. Снять начальные counters. Выполнить X, дождаться его завершения и offload store.
5. Нагрузить различными префиксами, чтобы X перестал целиком помещаться в GPU prefix cache. Не гадать по количеству документов: подтвердить по metrics/trace.
6. Повторить точно X, сравнить CPU load counters, prefill, queue и client TTFT.
7. Повторить серию; убедиться, что очереди и input lengths сопоставимы.

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-cache --ack
python3 scripts/hf.py start b-cache --ack
python3 scripts/hf.py logs b-cache
```

Для снимка метрик через port-forward:

```bash
curl --fail --max-time 15 http://127.0.0.1:18002/metrics
```

Точные названия connector counters выписать из **работающего** `/metrics` на репетиции. Отсутствие instrumentation — причина пометить CPU-hit неподтверждённым, а не придумать имя метрики.

**Неуспех:** OOM host RAM, bandwidth/latency ухудшились, connector не поддержал модель, повтор остался GPU hit. Все эти результаты содержательны; RAM-offload не обязан ускорять холодный запрос.
