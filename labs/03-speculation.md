# Gemma assistant и спекулятивное декодирование

Используются основная Gemma и совместимый assistant, версии которых закреплены в [models.lock.json](../models.lock.json). Названия семейства недостаточно для совместимости: другую вспомогательную модель подставлять нельзя.

Сравним B tuned с B spec. В [spec-профиле](../manifests/profiles/gemma-b-spec.json) заданы `method: mtp`, локальный assistant и один спекулятивный токен. В обоих профилях выгрузка KV в RAM выключена. [Спекулятивное декодирование в vLLM](https://docs.vllm.ai/en/v0.30.0/features/speculative_decoding/).

Все B-профили относятся к одному Deployment `hf-gemma-b` и по очереди занимают одну карту. Если до этого был запущен cache-профиль, команда `stop b-cache` остановит тот же Deployment.

## Серия без assistant

Используйте `.local/long.jsonl` из работы A/B: 32 разных документа, вход около половины согласованного окна и предел выхода 2048 токена. Файл между сериями не меняйте. В примере — восемь запросов с параллельностью 1. Это пробный замер, не достаточная выборка для оценки p95 рабочего сервиса.

Остановите прежний проброс порта сочетанием Ctrl+C. В основном терминале перезапустите B, чтобы очистить кэш движка:

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-tuned --ack
python3 scripts/hf.py start b-tuned --ack
python3 scripts/hf.py logs b-tuned
```

Дождитесь готовности API. В отдельном терминале:

```bash
python3 scripts/hf.py port-forward b-tuned 18002
```

В основном терминале создайте каталог результатов, выполните одинаковый для обеих серий прогрев на отдельном коротком запросе, затем измерьте нагрузку:

```bash
spec_run_dir="results/raw/spec/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$spec_run_dir/tuned" "$spec_run_dir/spec"
python3 scripts/bench.py --url http://127.0.0.1:18002 \
  --dataset examples/smoke.jsonl --requests 1 --concurrency 1 \
  --label tuned-warmup --out-dir "$spec_run_dir/warmup-tuned"
curl --fail --max-time 15 http://127.0.0.1:18002/metrics \
  --output "$spec_run_dir/tuned/before.prom"
python3 scripts/bench.py --url http://127.0.0.1:18002 \
  --dataset .local/long.jsonl --offset 0 --requests 8 --concurrency 1 \
  --cold --metrics --label tuned --out-dir "$spec_run_dir/tuned"
curl --fail --max-time 15 http://127.0.0.1:18002/metrics \
  --output "$spec_run_dir/tuned/after.prom"
```

`--cold` только проверяет, что строки набора не повторяются. Кэш очищает перезапуск выше, а не этот флаг. Во время измерения другие запросы к B не отправляйте. Реплику A тоже не нагружайте: обе карты используют CPU и память одного узла.

## Серия с assistant

Остановите проброс порта сочетанием Ctrl+C. В том же основном терминале переключите профиль:

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-spec --ack
python3 scripts/hf.py start b-spec --ack
python3 scripts/hf.py logs b-spec
```

После готовности сервиса заново запустите проброс порта в отдельном терминале:

```bash
python3 scripts/hf.py port-forward b-spec 18002
```

В основном терминале повторите тот же прогрев и те же восемь запросов. Переменная `spec_run_dir` должна остаться от первой серии:

```bash
python3 scripts/bench.py --url http://127.0.0.1:18002 \
  --dataset examples/smoke.jsonl --requests 1 --concurrency 1 \
  --label spec-warmup --out-dir "$spec_run_dir/warmup-spec"
curl --fail --max-time 15 http://127.0.0.1:18002/metrics \
  --output "$spec_run_dir/spec/before.prom"
python3 scripts/bench.py --url http://127.0.0.1:18002 \
  --dataset .local/long.jsonl --offset 0 --requests 8 --concurrency 1 \
  --cold --metrics --label spec --out-dir "$spec_run_dir/spec"
curl --fail --max-time 15 http://127.0.0.1:18002/metrics \
  --output "$spec_run_dir/spec/after.prom"
python3 scripts/report.py --directory "$spec_run_dir/tuned"
python3 scripts/report.py --directory "$spec_run_dir/spec"
```

Повторите всю пару серий с `--concurrency 8` в двух измерительных командах. Число запросов, порядок документов, предел генерации и процедура прогрева должны совпадать. Между каждой серией снова нужен перезапуск. Для проверки влияния порядка выполните ещё одну пару, начиная со spec.

`bench.py` сохраняет снимки метрик, но не рассчитывает долю принятия спекулятивных токенов. Найдите соответствующие счётчики в сохранённых `.prom`, проверьте описания `HELP/TYPE` и их наличие именно в этой версии движка. Сравнивайте прирост счётчиков между `before` и `after` одной серии. Если нужных метрик нет, доля принятия остаётся неизвестной — скорость ответа её не заменяет.

## Проверка работы assistant

- Убедитесь по логам, что движок загрузил assistant и включил MTP, а не перешёл к обычному декодированию.
- Используйте несколько видов запросов: естественный язык, код, структурированный ответ. Одного синтетического промпта недостаточно.
- Сохраните одинаковый бюджет генерации и сравните фактическое число выходных токенов.
- Найдите в метриках число предложенных и принятых токенов, включая принятие по позициям. Не смешивайте счётчики разных серий или процессов TP без проверки правил агрегации.
- Сравните выходные токены в секунду, TTFT, ITL и потребление RAM/HBM при одном и восьми одновременных запросах.
- Проверьте содержательность ответов и отсутствие зацикливания.

Затем проведите отдельные серии с 2–3 спекулятивными токенами. Выбирайте по итоговой скорости, а не только по доле принятия: предложение и проверка тоже занимают время.

Если ускорения нет, оставьте tuned-профиль без assistant.
