# Как измерять производительность

Для сравнения нужны одинаковая нагрузка, конфигурация и проверенные ответы.
Ожидаемый эффект настройки ещё не результат замера.

## Какой результат нужен

| Задача | Что выполнить |
| --- | --- |
| Сравнить A, Cache и Tune | Общие шаги 1–4 ниже: условия, серия, ответы и метрики |
| Проверить короткий запрос рядом с длинным | [Смешанная нагрузка](#mixed-prefill), отдельно от обычной A/B-серии |
| Подтвердить возврат KV из RAM | [Повтор после вытеснения](#offload-replay); скорость повтора сама по себе не доказательство |
| Сравнить ручной и платформенный запуск | [CPU Job для двух целей](#gemma-client-h100) |
| Проверить Qwen на двух GPU | [Длинный вход и параллельные сессии](#qwen-load) |

Дополнительные опыты независимы: для обычного A/B не требуется сначала
ограничивать KV-пул или запускать Qwen. У всех серий сохраняются условия
и исходные результаты из шага 1.

## 1. Зафиксировать условия A/B

В [отчёте](../results/REPORT.template.md) сохраните:

- GPU: тип, объём памяти, UUID, частоты, температуру и мощность;
- версии драйвера, CUDA, vLLM, digest образа, ревизии весов и токенизатора;
- Git SHA, аргументы движка, RAM/shm и фактический GPU KV-пул;
- endpoint, клиентскую ноду, длины, число запросов, concurrency и состояние кешей;
- сырые JSON, метрики и логи ошибок каждого прогона.

До серии задайте concurrency — число одновременных запросов, допустимые задержки
и долю ошибок. Исключите постороннюю нагрузку и ограничения CPU/сети клиента.
Прямой vLLM и чат/шлюз измеряйте отдельно; кеш ответов шлюза отключите.

У A/B одинаковый тип GPU. Одновременному запуску нужны разные карты и запас RAM;
на VM 128 GiB offload-серии идут последовательно. Проверяйте UUID выделенной карты.

## 2. Выбрать серию

| Профиль | Настройки |
| --- | --- |
| `a-base` | BF16 KV; без prefix cache, offload, CUDA graphs и chunked prefill |
| `b-cache` | FP8 KV, Triton attention, prefix cache; CPU KV: H100 32 GiB, RTX 4 GiB |
| `b-spec` | Кеши Cache; окно 128K, prefill: H100 2048, RTX 512; CUDA graphs, assistant |

A — выбранный baseline vLLM 0.31, не настройки по умолчанию или старая версия.
Окно A/Cache: H100 16K, RTX 4K; Tune — 128K. A/B сравнивает целые профили;
для вклада одной настройки меняйте только её.

| Проверка | Вход / выход | Запросов / одновременно |
| --- | --- | --- |
| Короткий A/B, H100 | 8192 / 2048 | 8 / 4 |
| Короткий A/B, RTX | 2048 / 128 | 8 / 2 |
| Gemma Tune, длинный вход | 57 344 / 512, затем 122 880 / 512 | 1 / 1 |
| Gemma Tune, полное окно 128K | 122 880 / 8192 | 1 / 1 |

128K = 131 072, 256K = 262 144 токена. В окно входят запрос, шаблон чата и ответ.
Длинная серия проверяет вместимость, а отказ по памяти не даёт скорости профиля.
Для Gemma 256K — только расчёт памяти: Tune использует 128K.

Для контроля с равным окном выключенному Tune через Git/Argo задайте окно Cache,
на RTX также долю GPU 0.90. После замера верните полный Tune 128K,
на RTX — также `gpu-memory-utilization: 0.95`. Другие отличия сохраняются:
вклад MTP, chunked prefill и CUDA graphs не изолирован. Исторические результаты 4K не переименовывайте.

## 3. Запустить нагрузку и проверить ответы

В [`vllm bench serve`](../README.md#monitoring) подставьте длины серии.
Токенизатор уже в контейнере. Синтетика проверяет нагрузку, не качество;
Chat API с шаблоном и completions API измеряйте отдельно.

`--ready-check-timeout-sec 0` убирает пробный запрос, `--num-warmups 0` — прогрев.
`--ignore-eos` игнорирует конец ответа ради фиксированного выхода; для чата не нужен.

Флаги не очищают серверный кеш. Холодному входу нужен новый префикс;
повтор с тем же seed отмечайте отдельно, сохраняя документы, порядок и состояние GPU/RAM-кеша.

Проверьте завершение всех запросов, фактические input/output tokens, ошибки,
отмены, обрывы и новые ECC/Xid в логах. `max_tokens` не доказывает длину ответа.

## 4. Отделить очередь от вычислений

| Метрика | Что измеряет |
| --- | --- |
| TTFT | Ожидание первого токена; у клиента включает весь путь запроса |
| Queue / prefill | Ожидание scheduler / обработку входных токенов |
| TPOT / ITL | Среднее время выходного токена после первого / интервалы соседних токенов |
| Throughput | Суммарные токены или запросы в секунду |

Снимайте `/metrics` до и после серии. Для A с port-forward на 18001,
из корня `k8s-config`:

```bash
mkdir -p results/hardfest/a-base
curl --fail http://127.0.0.1:18001/metrics > results/hardfest/a-base/before.prom
```

После серии — `after.prom`; для B — порт 18002 и отдельный каталог.
Имена/labels сверяйте с `HELP/TYPE` и [справочником](https://docs.vllm.ai/en/v0.31.0/usage/metrics/).
Среднее ожидание:

```promql
sum(increase(vllm:request_queue_time_seconds_sum{model_name="$model",instance="$instance"}[5m]))
/
sum(increase(vllm:request_queue_time_seconds_count{model_name="$model",instance="$instance"}[5m]))
```

Для prefill замените имя на `vllm:request_prefill_time_seconds`.
Ноль наблюдений означает отсутствие результата; учитывайте сбросы счётчиков.
Серверный TTFT p95:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(vllm:time_to_first_token_seconds_bucket{model_name="$model",instance="$instance"}[5m]))
)
```

На H100 у A/B одинаковый `model_name`: различайте Service/Pod; на RTX served names разные.
`TTFT_p95 − queue_p95` не даёт prefill: нужны отметки одного запроса,
а процентили могут относиться к разным. Учитывайте ожидание после вытеснения.

<a id="mixed-prefill"></a>
## Отдельный опыт: длинный и короткий запрос вместе

`timed_trace` vLLM 0.31 отправляет из одного клиента длинный запрос в момент 0,
короткий через 50 мс. Сравниваются Cache/Tune.

| Стенд | Длинный / короткий вход | Выход каждого | Окно Cache / Tune |
| --- | ---: | ---: | ---: |
| H100 | 8192 / 128 | 512 | 16K / 128K |
| RTX | 3072 / 128 | 128 | 4K / 128K |

Команды для H100 выполняются в Bash. Для RTX задайте `NS=hardfest-rtx`,
`SERVICE=rtx-gemma-cache`, `MODEL=rtx-gemma-cache`,
`TOKENIZER_PATH=/data/modelcache/models/rtx-gemma-e2b`, `LONG_INPUT=3072`, `OUTPUT=128`.

```bash
export NS=hardfest-demo SERVICE=hf-gemma-b MODEL=gemma-4-31b
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

Trace создаётся один раз на пару. Выполните блок на Cache, переключите профиль
и повторите с `PHASE=tune`; на RTX также задайте
`SERVICE=rtx-gemma-tune MODEL=rtx-gemma-tune`.

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

Фиксированные `PYTHONHASHSEED`, trace и токенизатор воспроизводят входы.
Следующей паре нужен новый trace без prefix-cache hits. В JSON проверьте
два успешных ответа, `input_lens`, `output_lens`, пустые `errors` и перекрытие:

```text
end[i] = start_times[i] + latencies[i]
overlap = min(end[0], end[1]) − max(start_times[0], start_times[1])
```

Нужны `overlap > 0` и `start_times[1] < start_times[0] + ttfts[0]`:
короткий начался до первого токена длинного. Иначе повторите с меньшей задержкой
и новым trace. Клиентские отметки не задают границы GPU-prefill.
Сравните TTFT короткого, ITL и время обоих ответов: это эффект всего Tune.
[Формат trace](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/datasets/datasets.py),
[поля результатов](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/serve.py).

## Как подтвердить работу оптимизации

| Утверждение | Доказательство |
| --- | --- |
| Использован GPU prefix cache | Приращение локальных cache hits |
| KV вернулся из RAM | CPU → GPU байты после вытеснения префикса из GPU |
| Assistant/MTP работает | Приращения предложенных и принятых токенов |
| Assistant/MTP ускоряет | Парный тест с ним и без: TPOT, throughput, ошибки |

MTP предлагает несколько токенов за шаг; assistant — отдельная черновая модель.
Доля принятия не учитывает её затраты и не определена без предложений.
Быстрый повтор не доказывает RAM-offload.

<a id="offload-replay"></a>
### Вернуть конкретный префикс из RAM

Опыт выполняется на ручной B Cache без посторонних запросов и проверяет перенос,
не ускорение. Через Git/Argo временно добавьте в `vllm:` поле
[`kv-cache-memory-bytes`](https://docs.vllm.ai/en/v0.31.0/configuration/engine_args/#--kv-cache-memory-bytes):

| Стенд | GPU KV-пул, байт | Вход | Штатный RAM KV |
| --- | ---: | ---: | ---: |
| H100 | `2147483648` (2 GiB) | 8192 | 32 GiB |
| RTX | `268435456` (256 MiB) | 3072 | 4 GiB |

Поле заменяет расчёт по доле GPU; окно остаётся 16K / 4K, KV — FP8.
После Ready проверьте в логах вместимость одного запроса. Иначе восстановите
профиль и отметьте неуспешный опыт.

Отправьте исходный префикс, 32 независимых, затем исходный повторно.
Для RTX замените `NS=hardfest-rtx`, `SERVICE=rtx-gemma-cache`,
`MODEL=rtx-gemma-cache`, `TOKENIZER=/data/modelcache/models/rtx-gemma-e2b`, `INPUT=3072`.
Клиенту нужен `jq`.

```bash
export NS=hardfest-demo SERVICE=hf-gemma-b MODEL=gemma-4-31b
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

Проверка требует `completed=1`, `failed=0` и заданные длины; иначе опыт останавливается.
Исходный префикс между вытеснениями не повторяется; seed меняет начало входа.
В отдельном терминале откройте метрики той же B:

```bash
kubectl --context "$GPU_CONTEXT" -n "$NS" port-forward "svc/$SERVICE" 18002:8000
```

В основном терминале снимите счётчики вокруг повтора:

```bash
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/before.prom || exit 1
offload_request 9001 replay || exit 1
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/after.prom || exit 1
```

Для OffloadingConnector нужны приращения `CPU_to_GPU` и external-prefix hits
именно на повторе. Ноль означает, что возврат не показан: префикс мог остаться
на GPU или исчезнуть из RAM. Ёмкость и округление блоков берите из runtime.
У другого backend, Simple CPU, подтверждение —
`vllm:simple_kv_offload_load_blocks_total`; отсутствие чужой метрики не означает сбой.

Удалите временное поле через Git/Argo. Продолжайте Cache/Tune после Ready
и проверки штатного пула. NodeCache с весами не очищается.

<a id="qwen-load"></a>
## Qwen: длинный вход и восемь сессий

TP2 распределяет модель между двумя GPU. Скорость Qwen не является ускорением Gemma.
После корректного ответа используйте [CPU Job H100](../examples/qwen-benchmark-job.yaml)
или [команду RTX](../RTX5060.md#окно-128k-не-означает-восемь-полных-окон).

| Стенд | Серия | Вход / выход | Одновременно |
| --- | --- | --- | ---: |
| H100 | Контрольная | 8192 / 512 | 1, затем 4 и 8 |
| H100 | Длинная | 122 880 / до 8192 | 1 |
| RTX | Длинная | 57 344 / 512, затем 122 880 / 512 | 1 |
| RTX | Восемь запросов | 32 768 / 512 каждый | 8 |

На RTX восемь запросов требуют `completed=8`, `failed=0`, суммарно 262 144 входных
и 4096 выходных токенов. Сохраняйте Running/Waiting, KV, вытеснения и перезапуски.
Восемь клиентов не означают восемь полных окон. Окно Qwen: H100 256K, RTX 128K;
серии выше проверяют не более 128K, измерений полного окна 256K здесь нет.

Сетка concurrency: 1, 2, 4, 8, 16, 32, 50; длины, число запросов, генерация и кеш
не меняются. При `max-num-seqs=16` часть клиентов ждёт. Ёмкость — максимальная
повторяемая ступень в выбранных пределах TTFT/TPOT и ошибок.

<a id="gemma-client-h100"></a>
## Ручная и автоматическая Gemma: CPU Job

[Общий Job](../examples/gemma-benchmark-job.yaml): прямой API, полный Tune 128K,
один токенизатор; первый проход и повтор для каждой цели. Он не запрашивает GPU
и не меняет runtime. Из корня частного `k8s-config` подготовьте отдельную копию:

```bash
export BENCH_DIR="$(mktemp -d)"
cp ../hardfest-gpu-workshop/examples/gemma-benchmark-job.yaml "$BENCH_DIR/job.yaml"
```

Задайте CPU-ноду с доступом к Service, ID платформенной модели из `/v1/models`,
уникальное имя Job и ссылки на Secrets. `HF_TOKEN` нужен только токенизатору.
Для платформы ключ обязателен даже при optional-ссылке; авторизация сохраняется.
Ручному runtime без авторизации допустима optional-ссылка на отсутствующий
`replace-manual-runtime-api-secret`. Ключи в YAML не записываются.

H100: namespace `hardfest-demo`, 8 запросов, вход 8192, выход 2048, concurrency 4.
В `env` выберите `BENCH_TARGET=both`, если обе модели Ready и помещаются.
На 128 GiB запустите отдельные Job `manual`, затем `platform`, переключая модели
через GitOps. Невыбранная модель и её ключ не нужны.

<a id="gemma-client-rtx"></a>
### Параметры RTX

В той же копии замените поля; `NUM_PROMPTS=8` и `BENCH_TARGET=both` сохраняются:

| Поле | Значение |
| --- | --- |
| `metadata.namespace` | `hardfest-rtx` |
| `MANUAL_BASE_URL` / `MANUAL_MODEL` | `http://rtx-gemma-tune.hardfest-rtx.svc.cluster.local:8000` / `rtx-gemma-tune` |
| `PLATFORM_BASE_URL` / `PLATFORM_MODEL` | `http://rtx-gemma-platform.hardfest-rtx.svc.cluster.local:80` / `rtx-gemma-e2b` |
| `TOKENIZER_ID` | `google/gemma-4-E2B-it` |
| `TOKENIZER_REVISION` | `3e22461f65e89153144f8adb70e3b8c2cc9845a7` |
| `INPUT_TOKENS` / `OUTPUT_TOKENS` / `CONCURRENCY` | `2048` / `128` / `2` |

CPU-нода должна достигать обоих Service; Secrets находятся в `hardfest-rtx`.
Обе Gemma используют полный Tune 128K.

### Запуск и результат на обоих стендах

После проверки задайте `spec.suspend: false`. Ниже укажите namespace и уникальное
имя копии; для RTX замените `NS` на `hardfest-rtx`:

```bash
export NS=hardfest-demo BENCH_JOB=gemma-benchmark
kubectl --context "$GPU_CONTEXT" create --dry-run=server -f "$BENCH_DIR/job.yaml" || exit 1
kubectl --context "$GPU_CONTEXT" create -f "$BENCH_DIR/job.yaml" || exit 1
kubectl --context "$GPU_CONTEXT" -n "$NS" logs -f "job/$BENCH_JOB" \
  --pod-running-timeout=5m | tee "$BENCH_DIR/benchmark.log"
kubectl --context "$GPU_CONTEXT" -n "$NS" get job "$BENCH_JOB"
```

До удаления Job по TTL через сутки сохраните четыре блока
`RESULT_JSON_BEGIN` / `RESULT_JSON_END`: по два на цель, включая отдельные Job.
Неполная серия завершает Job ошибкой. Критерии каждого блока:

| Стенд | Запросы | Входных / выходных токенов |
| --- | --- | ---: |
| H100 | `completed=8`, `failed=0` | 65 536 / 16 384 |
| RTX | `completed=8`, `failed=0` | 16 384 / 1024 |

Первый проход не гарантирует холодный кеш. Сравнивайте отдельно первые проходы
и повторы. Запустите трижды с новыми именами Job и неизменными CPU-нодой,
токенизатором, длинами и concurrency. Короткий Job не проверяет длинный вход.

## Проверить отчёт

Сохраните исходные прогоны и медиану минимум трёх повторов с одинаковым прогревом;
p95 разных прогонов не усредняйте. В A/B должны различаться только запланированные
параметры. Укажите ошибки, отмены и обрывы. Ожидания и результаты другого стенда
не выдаются за измерения текущего.
