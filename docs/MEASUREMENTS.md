# Измерения

Сравнивайте профили при одинаковых входах, генерации и состоянии кеша.
Прямой runtime и путь WebUI/Bifrost измеряются отдельно.
Сырые результаты сохраняются вместе с конфигурацией в [отчёте](../results/REPORT.template.md).

## Где посмотреть результат

| Площадка | Что измерено | Чего это не доказывает |
| --- | --- | --- |
| [H100: возврат KV из RAM](../README.md#h100-result) | Первый запрос и повтор одного документа | Ускорение Base → Tune или ёмкость нескольких сессий |
| [RTX 5060 Ti: Base → Cache → Tune](../RTX5060.md#tuning-results) | TTFT, TPOT и ток/с при одинаковой короткой нагрузке | Скорость полных окон 128K |
| [PRO 6000: одинаковые сообщения A/B](../RTXPRO6000.md#pro-result) | Начало ответа для короткого, длинного и повторного входа | Скорость выдачи ответа 32K |

Карточки и таблицы пересчитываются из опубликованных JSON:

```bash
python3 scripts/article_results.py --check
```

Без `--check` команда обновляет только иллюстрации и блоки чисел в статьях.
Она не запускает нагрузку. Полные ответы и графики остаются рядом с источниками,
а в статье — условия, сравнение и вывод.

## Что проверяет серия

| Серия | Что можно заключить |
| --- | --- |
| A/B с одинаковой нагрузкой | Разницу задержки, скорости и ошибок целых профилей |
| Длинный вход и фиксированный выход | Возможность закончить эту нагрузку, включая очередь |
| Повтор общего префикса | Эффект повторного использования кеша при этих условиях |
| [Повтор после вытеснения](#offload-replay) | Возврат KV из RAM при подтверждённом чтении |
| [Длинный и короткий запрос вместе](#mixed-prefill) | Поведение короткого запроса при конкуренции с длинным |

До запуска сохраните GPU UUID, версии драйвера/runtime, ревизии модели и
токенизатора, параметры vLLM, GPU KV-пул, RAM/shm, endpoint и клиентскую ноду.
Задайте число запросов, concurrency, допустимые задержки и ошибки.
Исключите соседнюю нагрузку и кеш готовых ответов шлюза.

`--ready-check-timeout-sec 0` убирает пробный запрос, `--num-warmups 0` — прогрев.
Они не очищают серверный кеш. Новый префикс и повтор того же входа — разные серии.
`--ignore-eos` принудительно продолжает генерацию до заданной длины;
обычный пользовательский ответ может закончиться раньше.

## Нагрузка для трёх площадок

Короткий A/B сравнивает настройки. Полное окно проверяет вместимость;
разные входы A/B нельзя использовать для расчёта ускорения.

| Профиль | H100 | RTX 5060 |
| --- | --- | --- |
| Base | 16K, BF16 KV, без prefix/offload/graphs/chunking | Тот же контроль, окно 4K |
| Cache | 16K, FP8 KV, Triton, prefix cache, CPU KV 32 GiB | 4K, CPU KV 4 GiB |
| Tune | 128K, кеши Cache, assistant, graphs, prefill 2048 | 128K, prefill 512 |
| Короткий A/B | 8192/2048, 8 запросов, concurrency 4 | 2048/128, 8 запросов, concurrency 2 |
| Полное окно Tune 128K | 98304/32768, concurrency 1 | Те же длины |

На PRO 6000 короткий A/B — **2048/512, 10 запросов, concurrency 10**.
Полное окно Tune/Qwen — **229376/32768**, десять поданных запросов;
активные запросы и очередь измеряются отдельно. Base подбирает окно по памяти.
[Клиент PRO 6000](../examples/pro6000-load-client/README.md),
[сохранённые измерения и графики](../results/pro6000/README.md).

На H100 с RAM 128 GiB A/B запускаются последовательно. Для одновременного
запуска нужны разные физические GPU и запас RAM. Короткая нагрузка одинакова,
но профили меняют несколько параметров, включая окно: результат относится
к профилю целиком, не к отдельной оптимизации.

Проверьте фактические input/output tokens, `completed`, `failed`, обрывы,
отмены и новые ECC/Xid. `max_tokens` — предел, а не фактическая длина.
Сохраните метрики до/после серии, TTFT, очередь, TPOT/ITL и throughput.
Процентили разных величин не вычитаются; p95 разных прогонов не усредняются.

<a id="mixed-prefill"></a>
## Длинный и короткий запрос вместе

Клиент vLLM 0.31 `timed_trace` отправляет длинный вход в момент 0,
короткий через 50 мс. Сравниваются Cache/Tune. Пример H100 выполняется в Bash.
Для RTX: `NS=hardfest-rtx`, `SERVICE=rtx-gemma-cache`, `MODEL=rtx-gemma-cache`,
`TOKENIZER_PATH=/data/modelcache/models/rtx-gemma-e2b`, `LONG_INPUT=3072`, `OUTPUT=128`.

```bash
export NS=hardfest-demo SERVICE=hf-gemma-cache MODEL=gemma-4-31b
export TOKENIZER_PATH=/data/modelcache/models/gemma-4-31b
export LONG_INPUT=8192 OUTPUT=512 PHASE=cache
export TRACE_ID=$(date +%s)
export TRACE="results/mixed-$TRACE_ID.jsonl"
mkdir -p results
printf '{"timestamp":0,"input_length":%s,"output_length":%s,"hash_ids":[%s]}\n' \
  "$LONG_INPUT" "$OUTPUT" "$TRACE_ID" > "$TRACE"
printf '{"timestamp":0.05,"input_length":128,"output_length":%s,"hash_ids":[%s]}\n' \
  "$OUTPUT" "$((TRACE_ID + 1))" >> "$TRACE"
```

Создайте trace один раз на пару. Выполните блок на Cache, переключите профиль
и повторите с `PHASE=tune SERVICE=hf-gemma-b`;
на RTX — `PHASE=tune SERVICE=rtx-gemma-tune MODEL=rtx-gemma-tune`.

```bash
export RESULT="mixed-$TRACE_ID-$PHASE.json"
set -o pipefail
kubectl --context "$GPU_CONTEXT" -n "$NS" exec -i "deployment/$SERVICE" -c vllm -- \
  env PYTHONHASHSEED=0 vllm bench serve \
    --backend vllm --base-url "http://$SERVICE.$NS.svc.cluster.local:8000" \
    --endpoint /v1/completions --model "$MODEL" --tokenizer "$TOKENIZER_PATH" \
    --dataset-name timed_trace --dataset-path /dev/stdin \
    --timed-trace-chunk-hash-size "$LONG_INPUT" --self-timed \
    --num-prompts 2 --max-concurrency 2 --temperature 0 --ignore-eos \
    --ready-check-timeout-sec 0 --num-warmups 0 \
    --save-result --save-detailed --result-dir /tmp --result-filename "$RESULT" \
  < "$TRACE" 2>&1 | tee "results/mixed-$TRACE_ID-$PHASE.log"
kubectl --context "$GPU_CONTEXT" -n "$NS" exec "deployment/$SERVICE" -c vllm -- \
  cat "/tmp/$RESULT" > "results/$RESULT"
```

Нужны два успешных ответа с заданными длинами и перекрытием:

```text
end[i] = start_times[i] + latencies[i]
overlap = min(end[0], end[1]) − max(start_times[0], start_times[1]) > 0
start_times[1] < start_times[0] + ttfts[0]
```

Последнее условие означает, что короткий стартовал до первого токена длинного.
Иначе уменьшите задержку и используйте новый trace. Клиентские отметки не задают
границы GPU-prefill. Сравните TTFT короткого, ITL и время обоих ответов.
[Формат trace](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/datasets/datasets.py),
[поля JSON](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/serve.py).

<a id="offload-replay"></a>
## Повтор после вытеснения из GPU

Опыт на ручной B Cache подтверждает перенос KV, не ускорение. Исключите
посторонние запросы. Через Git/Argo временно добавьте `kv-cache-memory-bytes`
в `vllm:`; поле заменяет расчёт пула по доле GPU:

| Площадка | GPU KV-пул, байт | Вход | Штатный RAM KV |
| --- | ---: | ---: | ---: |
| H100 | `2147483648` | 8192 | 32 GiB |
| RTX 5060 | `268435456` | 3072 | 4 GiB |

Окно остаётся 16K/4K, KV — FP8. После Ready проверьте в логах, что один запрос
помещается. Для RTX задайте `NS=hardfest-rtx`, `SERVICE=rtx-gemma-cache`,
`MODEL=rtx-gemma-cache`, `TOKENIZER=/data/modelcache/models/rtx-gemma-e2b`, `INPUT=3072`.
Клиенту нужен `jq`.

```bash
export NS=hardfest-demo SERVICE=hf-gemma-cache MODEL=gemma-4-31b
export TOKENIZER=/data/modelcache/models/gemma-4-31b INPUT=8192
mkdir -p results/offload
set -o pipefail
offload_request() {
  kubectl --context "$GPU_CONTEXT" -n "$NS" exec "deployment/$SERVICE" -c vllm -- \
    vllm bench serve --backend vllm \
      --base-url "http://$SERVICE.$NS.svc.cluster.local:8000" \
      --endpoint /v1/completions --model "$MODEL" --tokenizer "$TOKENIZER" \
      --dataset-name random --seed "$1" \
      --random-input-len "$INPUT" --random-output-len 1 --random-range-ratio 0 \
      --num-prompts 1 --max-concurrency 1 --ignore-eos --temperature 0 \
      --ready-check-timeout-sec 0 --num-warmups 0 \
      --save-result --save-detailed --result-dir /tmp --result-filename "offload-$2.json" \
    2>&1 | tee "results/offload/$2.txt" || return 1
  kubectl --context "$GPU_CONTEXT" -n "$NS" exec "deployment/$SERVICE" -c vllm -- \
    cat "/tmp/offload-$2.json" > "results/offload/$2.json" || return 1
  jq -e --argjson input "$INPUT" \
    '.completed == 1 and .failed == 0 and .total_input_tokens == $input and .total_output_tokens == 1' \
    "results/offload/$2.json" > /dev/null
}
offload_request 9001 original || exit 1
for SEED in $(seq 9002 9033); do
  offload_request "$SEED" "evict-$SEED" || exit 1
done
```

После исходного запроса идут 32 независимых префикса. Если результат неполный,
проверка останавливает опыт даже при успешном exit code клиента.
В отдельном терминале откройте метрики той же B:

```bash
kubectl --context "$GPU_CONTEXT" -n "$NS" port-forward "svc/$SERVICE" 18002:8000
```

Затем в основном терминале:

```bash
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/before.prom || exit 1
offload_request 9001 replay || exit 1
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/after.prom || exit 1
```

У OffloadingConnector нужны приращения `CPU_to_GPU` и external-prefix hits
именно при replay. У Simple CPU — `vllm:simple_kv_offload_load_blocks_total`.
Без чтения возврат не показан: префикс мог остаться на GPU или исчезнуть из RAM.
Удалите временный лимит через Git/Argo и проверьте штатный пул после Ready.
Дисковый NodeCache весов сохраняется.

<a id="qwen-load"></a>
## Qwen: окно и параллельные запросы

TP2 распределяет одну модель между двумя GPU. Скорость Qwen не является
ускорением Gemma. Для PRO 6000 используйте параметры из начала страницы: десять
запросов с окном 262144; максимум active и очередь указываются отдельно.

| Площадка | Полное окно, вход/выход | Отдельная проверка параллельности |
| --- | --- | --- |
| H100 | 229376/32768, один запрос | 8192/512, concurrency 1, 4, 8 |
| RTX 5060 | 98304/32768, один запрос | Восемь запросов по 32768/512 |

В копии [CPU Job H100](../examples/qwen-benchmark-job.yaml) для полного окна
задаются `INPUT_TOKENS=229376 OUTPUT_TOKENS=32768 NUM_PROMPTS=1 CONCURRENCY=1`
и отдельное имя Job. По умолчанию шаблон отправляет восемь запросов.
Для восьми запросов RTX нужны `completed=8`, `failed=0`, суммарно 262144
входных и 4096 выходных токенов. Это не восемь полных окон 128K.

Сетка concurrency 1, 2, 4, 8, 16, 32, 50 применяется при неизменных длинах,
генерации и кеше. Рабочая ёмкость — повторяемая ступень, укладывающаяся
в заранее выбранные задержки и долю ошибок.

<a id="gemma-client-h100"></a>
## CPU-клиент: ручная и платформенная Gemma

[Job](../examples/gemma-benchmark-job.yaml) сравнивает прямые API с полным Tune 128K:
первый проход и повтор для каждой цели. Клиент GPU не запрашивает.
Из частного `k8s-config` подготовьте копию:

```bash
export BENCH_DIR="$(mktemp -d)"
cp ../hardfest-gpu-workshop/examples/gemma-benchmark-job.yaml "$BENCH_DIR/job.yaml"
```

Задайте CPU-ноду с доступом к Service, уникальное имя Job, платформенный Model ID
из `/v1/models` и ссылки на Secrets. `HF_TOKEN` нужен токенизатору, ключ платформы
обязателен даже при optional-ссылке. Ручному API без авторизации ключ не требуется.
Секретные значения в YAML не записываются.

H100: `hardfest-demo`, вход 8192, выход 2048, 8 запросов, concurrency 4.
`BENCH_TARGET=both` используется, только когда обе цели готовы и помещаются;
на 128 GiB выполните отдельные Job `manual` и `platform` последовательно.

<a id="gemma-client-rtx"></a>
### RTX 5060

В той же копии сохраните `NUM_PROMPTS=8`, выберите готовые цели и замените:

| Поле | Значение |
| --- | --- |
| `metadata.namespace` | `hardfest-rtx` |
| `MANUAL_BASE_URL` / `MANUAL_MODEL` | `http://rtx-gemma-tune.hardfest-rtx.svc.cluster.local:8000` / `rtx-gemma-tune` |
| `PLATFORM_BASE_URL` / `PLATFORM_MODEL` | `http://rtx-gemma-platform.hardfest-rtx.svc.cluster.local:80` / `rtx-gemma-e2b` |
| `TOKENIZER_ID` | `google/gemma-4-E2B-it` |
| `TOKENIZER_REVISION` | `3e22461f65e89153144f8adb70e3b8c2cc9845a7` |
| `INPUT_TOKENS` / `OUTPUT_TOKENS` / `CONCURRENCY` | `2048` / `128` / `2` |

### Запуск и проверка

Проверьте копию, задайте `spec.suspend: false`, namespace и имя Job:

```bash
export NS=hardfest-demo BENCH_JOB=gemma-benchmark
kubectl --context "$GPU_CONTEXT" create --dry-run=server -f "$BENCH_DIR/job.yaml" || exit 1
kubectl --context "$GPU_CONTEXT" create -f "$BENCH_DIR/job.yaml" || exit 1
kubectl --context "$GPU_CONTEXT" -n "$NS" logs -f "job/$BENCH_JOB" \
  --pod-running-timeout=5m | tee "$BENCH_DIR/benchmark.log"
kubectl --context "$GPU_CONTEXT" -n "$NS" get job "$BENCH_JOB"
```

Сохраните блоки `RESULT_JSON_BEGIN` / `RESULT_JSON_END` до удаления Job по TTL:
по два на цель. В каждом `completed=8`, `failed=0`; H100 — 65536/16384 токенов,
RTX — 16384/1024. Повторите трижды с новыми именами, сохраняя условия.
Первый проход не гарантирует холодный кеш, а короткая серия не проверяет длинный вход.
