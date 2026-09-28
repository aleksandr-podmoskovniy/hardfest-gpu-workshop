# Измерения, которые можно сравнивать

## Контракт A/B

Зафиксировать: GPU SKU/VRAM, clocks/power/temperature, версии driver/CUDA/vLLM и image digest, revision весов и tokenizer, precision весов, одинаковые `max-model-len`, sampling, фактические input/output tokens, запросы/сек и concurrency, endpoint, effective runtime args. A/B не должны конкурировать за одну физическую GPU или общий CPU bottleneck.

A — **штатная** vLLM выбранной версии, не намеренно сломанный запуск. Если 256K не помещается у A, разделить эксперименты:

- **Скорость:** общее реально работающее окно A и B. Не менять одновременно длину входа.
- **Вместимость:** насколько увеличивается доступное окно/число сессий. OOM — результат вместимости, но не числовая скорость A.

Полезная серия:

| Серия | Вход | Выход | Concurrency | Условие |
| --- | --- | --- | --- | --- |
| Smoke | Короткий | до 128 | 1 | Только корректность API |
| Long cold | Около половины согласованного окна | до 2048 | 1, 4, 8 | Разные префиксы, одинаковый dataset для A/B |
| Long repeat | Те же документы | до 2048 | 1, 4, 8 | Порядок и состояние GPU/CPU cache записаны |
| Decode | Умеренный вход | до 8192 | 1, 8 | Реальный выход, а не только установленный max_tokens |
| Capacity curve | Фиксированные длины | Фиксированный предел | 1, 2, 4, 8, 16, 32, 50 | Прекратить при ошибках/неприемлемом SLA |

Это **план**, не полученные результаты. Half-window вход при 256K — примерно 128K, а не несколько тысяч токенов. Выход плюс вход плюс chat-template должны помещаться в окно. Бесконечную генерацию ради цифры не включаем.

## Подготовка синтетической нагрузки

Обычный путь из корня проекта, после Ready нашего Pod:

```bash
python3 scripts/hf.py dataset a --input-fraction 0.5 --output-tokens 2048 \
  --documents 32 --out .local/long.jsonl
```

Wrapper выполняет генератор с tokenizer внутри контейнера и сохраняет stdout локально; не нужно собирать kubectl exec самостоятельно. Следующий пример — низкоуровневая альтернатива для читателя со своей средой.

В контейнере с **тем же** runtime/tokenizer либо отдельной среде с совместимым transformers:

```bash
python3 scripts/make_workload.py --tokenizer /models/gemma \
  --input-tokens 131072 --output-tokens 2048 --context 262144 \
  --documents 32 > .local/long.jsonl
```

Пути относятся к среде, где запущена команда: локальный Mac не видит `/models/gemma` из Pod. Для генерации на Pod передать script через `kubectl exec -i … -- python - …`, а stdout сохранить локально. Требуемые kubeconfig/context/namespace брать из site, не из текущего глобального context.

Генератор не скачивает tokenizer; требует локальные файлы. Он создаёт уникальный ID в начале каждого документа и считает длину через chat template. Сервер может применять дополнительные преобразования: проверить возвращённый `usage.prompt_tokens` перед серией. Синтетический текст не представляет качество реальных ответов — для quality-check нужен отдельный набор вопросов.

Если запросов больше строк dataset, harness циклически повторяет документы: это **уже не полностью холодный тест**. Для cold дать не меньше уникальных строк, чем запросов. Перед cold — свежий engine либо проверенная процедура очистки кэша; warming/compilation выполнять на другом префиксе. Перезапуск считается отдельно от latency запросов.

## Что считает scripts/bench.py

Harness использует `chat/completions` SSE, ограничение concurrent HTTP requests, одинаковую temperature=0, SHA256 dataset. TTFT начинается при отправке реально активного запроса, не при ожидании слота в клиентском ThreadPool. Это **closed-loop** нагрузка, не Poisson arrivals и не измерение всех пользовательских очередей.

Первый непустой content/reasoning/tool chunk даёт client TTFT. Число SSE chunks не считается числом токенов. Output tok/s берётся только из server usage; без usage результат `null`. Ошибки, обрывы и finish_reason сохраняются. Малое число запросов даёт лишь sample p95, не надёжный production SLA.

Для масштабного теста можно использовать [официальный vLLM bench serve](https://docs.vllm.ai/en/v0.30.0/cli/bench/serve/) той же версии. Нельзя смешивать его `/completions` token-ID dataset с нашим chat workload и подписывать одним «до/после».

## Server queue, prefill и decode

Для локального изолированного замера добавить `--metrics` к bench.py: сохраняются сырые endpoint snapshots и средние queue/prefill по разнице sum/count. При отсутствии series/reset среднее не вычисляется. `python3 scripts/report.py --directory results/raw/ab` выводит эти средние рядом с client TTFT p95 — не смешивает их.

На отдельном engine снимаем `/metrics` до/после серии, сохраняем raw snapshots. Метрики доступного runtime сверяем по фактическим `HELP/TYPE`. [Справочник vLLM](https://docs.vllm.ai/en/v0.30.0/usage/metrics/).

Среднее время queue для **завершённых запросов интервала**:

```promql
sum(increase(vllm:request_queue_time_seconds_sum{model_name="$model"}[5m]))
/
sum(increase(vllm:request_queue_time_seconds_count{model_name="$model"}[5m]))
```

Для prefill заменить metric на `vllm:request_prefill_time_seconds`. Фильтр уточнить по реальным labels, чтобы не сложить разные реплики/прогоны. При нулевом count показывать «нет данных», не ноль секунд.

Server TTFT p95:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(vllm:time_to_first_token_seconds_bucket{model_name="$model"}[5m]))
)
```

Не вычислять `TTFT_p95 − queue_p95` и не подписывать это prefill. Для времени до первого токена после выхода из очереди нужны связанные timestamps **того же запроса** из trace; агрегатная Prometheus-гистограмма этой связи не даёт. Queue/preemption могут иметь дополнительные интервалы ожидания — читать semantics данной версии.

## Отчёт

Минимум три повторения основной серии. Сохранять individual measurements и медиану результатов прогонов, а не среднее p95. Одинаково считать прогрев, errors и cancelled. Фиксировать принудительное усечение выходов.

CPU hit показывать только с доказанным reload после GPU eviction. Высокий prefix hit не подтверждает работу offload. Gateway response cache на пути A/B отключить либо обходить его прямым endpoint.

Заполнить [REPORT.template.md](../results/REPORT.template.md). Никаких перенесённых 1,28 с, 7,09 с или tok/s с RTX: другой стенд, модель, runtime и workload.
