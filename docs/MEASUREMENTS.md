# Как измерять производительность

У замера есть три части: фиксированная нагрузка, сохранённая конфигурация
и проверка всех ответов. Сравнение только времени одного удачного запроса
не показывает ёмкость сервиса.

## Какие величины сравниваем

| Термин | Простое объяснение |
| --- | --- |
| Concurrency | Сколько запросов клиент отправляет одновременно |
| TTFT — Time To First Token | Сколько ждать первого токена |
| TPOT — Time Per Output Token | Среднее время на токен продолжения после первого |
| ITL — Inter-Token Latency | Интервал между соседними токенами потока |
| Throughput | Сколько токенов или запросов сервис обрабатывает в секунду |

## Перед началом

- [ ] Выбран API: прямой vLLM или полный путь через чат/шлюз. Эти серии не смешиваются.
- [ ] Заданы длины запросов, concurrency, число запросов и правила прогрева.
- [ ] На время серии нет посторонней нагрузки.
- [ ] Клиент не ограничен CPU или сетью.
- [ ] Выбраны допустимые TTFT, TPOT и доля ошибок **до** измерений.

## 1. Зафиксировать стенд и профиль

Заполните [шаблон отчёта](../results/REPORT.template.md).

| Что записать | Зачем |
| --- | --- |
| GPU: тип, HBM (High Bandwidth Memory), UUID, частоты, температура, мощность | Исключить смену устройства и троттлинг; HBM — память видеокарты, UUID — её постоянный идентификатор |
| Драйвер, CUDA, vLLM, digest образа | Сделать запуск воспроизводимым |
| Ревизии весов/токенизатора, формат весов | Сравнивать одну модель |
| Аргументы движка, RAM/shm, GPU KV-пул | Сохранить реальную конфигурацию |
| Вход, выход, число запросов, concurrency | Описать нагрузку |
| Endpoint, клиентская нода, состояние кэшей | Объяснить границы измерения |
| Git SHA, сырые JSON, логи ошибок | Проверить результат повторно |

У A и B одинаковый тип GPU без конкурирующей нагрузки. Для одновременного
запуска нужны разные физические карты и достаточная RAM.
На VM 128 GiB серии с offload выполняются последовательно.
Имя Service не закрепляет UUID — проверяйте выделенное устройство.

## 2. Выбрать сравнимые серии

| Серия | Что меняется относительно предыдущей |
| --- | --- |
| `a-base` | BF16 KV, без prefix cache/offload/CUDA graphs; без chunked prefill |
| `b-cache` | FP8 KV, Triton attention, prefix cache, CPU KV 32 GiB |
| `b-spec` | Те же кэши; prefill 2048, CUDA graphs, assistant |

A — намеренно выбранная исходная конфигурация современного runtime.
Это не штатные настройки vLLM 0.31 и не воспроизведение старой версии.
Строгий baseline использует окно 16K; больший контекст B проверяется отдельно.

### Скорость A/B и вместимость длинного контекста

| Проверка | Нагрузка | На какой вопрос отвечает |
| --- | --- | --- |
| Парное сравнение A/B | Окно 16 384, вход 8192, выход 2048, concurrency 4 | Быстрее ли B на том же запросе? |
| Длинный контекст B | 128K (131 072), включая ответ | Помещается ли длинная история и сколько занимает обработка? |
| Окно Qwen | До 256K (262 144), если позволяет фактический профиль | Какова вместимость другой модели на двух GPU? |

Вклад одной оптимизации проверяйте отдельной парой запусков, где меняется только она.
Нельзя сравнивать скорость короткого запроса A с длинным запросом B как ускорение.

| Дополнительный опыт | Что фиксировать |
| --- | --- |
| Повтор длинного входа | Одинаковые документы и порядок; состояние кэша GPU/RAM |
| Расширение B до 128K | Вместимость отдельно от скорости A/B |
| Длинная генерация | Фактический выход, не только `max_tokens` |
| Рост concurrency | Неизменные длины; границу по заранее выбранным TTFT/TPOT/ошибкам |

Отказ по памяти показывает границу вместимости, но не даёт скорости
отказавшей конфигурации. Вход, шаблон чата и ответ должны вместе помещаться в окно.

## 3. Выполнить нагрузку и проверить ответы

Используйте [команду в основном сценарии](../README.md#monitoring) штатного `vllm bench serve`.
Токенизатор находится в контейнере; локальные Python и веса не нужны.

Перед интерпретацией JSON проверьте:

1. Число завершённых запросов совпало с заданным.
2. Ошибок нет; отменённые запросы не потерялись из отчёта.
3. Суммарные input/output tokens совпали с планом.
4. Ответы не оборваны из-за сбоя или неожиданного лимита.
5. В течение серии не появились новые ECC/Xid: ECC (Error-Correcting Code) относится
   к контролю ошибок памяти; Xid — диагностический код драйвера NVIDIA.

Синтетические входы проверяют нагрузку, а не качество.
Chat API добавляет шаблон; completions API использует другой формат.
Эти результаты не объединяются.

### Прогрев и длина ответа

| Параметр | Что он меняет |
| --- | --- |
| `--ready-check-timeout-sec 0` | Клиент не делает отдельный пробный запрос |
| `--num-warmups 0` | Клиент не выполняет отдельный прогрев |
| `--ignore-eos` | Игнорирует EOS (End Of Sequence), сигнал конца ответа, ради фиксированного синтетического выхода |

Эти параметры **не очищают кэш сервера**. Для холодного входа используйте новые
префиксы; повторения с тем же seed отмечайте отдельно.
`--ignore-eos` не нужен пользовательскому чату.

## 4. Разделить очередь, prefill и первый токен

| Метрика | Что измеряется |
| --- | --- |
| Клиентский TTFT | Путь от отправки запроса до первого токена у клиента |
| Queue | Ожидание scheduler |
| Prefill | Обработка входных токенов |
| TPOT / ITL | Время последующих выходных токенов |
| Output tokens/s | Суммарная скорость всех запросов |

Снимайте `/metrics` до и после серии. Пример для A с port-forward
на 18001 из корня `k8s-config`:

```bash
mkdir -p results/hardfest/a-base
curl --fail http://127.0.0.1:18001/metrics > results/hardfest/a-base/before.prom
```

После серии повторите команду с именем `after.prom`.
Для B используйте 18002 и отдельный каталог серии.
Точные имена и labels сверяйте с `HELP/TYPE` и
[справочником vLLM](https://docs.vllm.ai/en/v0.31.0/usage/metrics/).

Среднее ожидание очереди за интервал:

```promql
sum(increase(vllm:request_queue_time_seconds_sum{model_name="$model",instance="$instance"}[5m]))
/
sum(increase(vllm:request_queue_time_seconds_count{model_name="$model",instance="$instance"}[5m]))
```

Для prefill замените имя на `vllm:request_prefill_time_seconds`.
При нулевом числе наблюдений результата нет, а не ноль секунд.
Сброс счётчиков также нужно учесть при сравнении выгрузок.

Серверный TTFT p95:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(vllm:time_to_first_token_seconds_bucket{model_name="$model",instance="$instance"}[5m]))
)
```

На основном стенде у A и B одинаковый `model_name`; различайте их по сервису
и Pod. В RTX-профилях served names разные. Не объединяйте профили только
потому, что они используют одни веса.

> [!IMPORTANT]
> `TTFT_p95 − queue_p95` не даёт prefill: процентили могут относиться
> к разным запросам. Для вычитания нужны отметки одного запроса из трассировки.
> При вытеснении возможны дополнительные интервалы ожидания.

<a id="mixed-prefill"></a>
### Два разных входа в одном запуске клиента

Для сравнения Cache/Tune используется штатный `timed_trace` vLLM 0.31:
длинный запрос в момент 0, короткий через 50 мс. Один клиент исключает разницу
в запуске двух процессов. Входы синтетические, это не проверка качества текста.

| Стенд | Длинный / короткий вход | Выход каждого | Окно обоих профилей |
| --- | ---: | ---: | ---: |
| H100 | 8192 / 128 | 512 | 16K |
| RTX | 3072 / 128 | 128 | 4K |

Пример для H100. Для RTX: `NS=hardfest-rtx`,
`SERVICE=rtx-gemma-cache`, `MODEL=rtx-gemma-cache`,
`TOKENIZER_PATH=/data/modelcache/models/rtx-gemma-e2b`, `LONG_INPUT=3072`, `OUTPUT=128`.
Команды выполняются в Bash:

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

Trace создаётся **один раз для пары**. На Cache выполняется блок ниже;
после переключения профиля тот же блок повторяется с `PHASE=tune`.
В RTX дополнительно меняются `SERVICE=rtx-gemma-tune MODEL=rtx-gemma-tune`.

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

`PYTHONHASHSEED=0`, одинаковый trace и токенизатор воспроизводят те же входы.
Для следующей пары создаётся **новый** trace, чтобы не повторить уже кешированный
prefill. На исследуемых запросах не должно быть prefix-cache hits.

В подробном JSON проверяются два успешных ответа, заданные `input_lens` /
`output_lens`, пустые `errors` и реальные времена:

```text
end[i] = start_times[i] + latencies[i]
overlap = min(end[0], end[1]) − max(start_times[0], start_times[1])
```

`overlap > 0` подтверждает перекрытие запросов у клиента. Короткий должен
начаться до первого токена длинного:
`start_times[1] < start_times[0] + ttfts[0]`. Если условия не выполнены,
результат не показывает нужную конкуренцию; следующая пара использует меньшую
задержку и новый trace. Клиентские отметки не являются границами GPU-prefill.

Сравниваются TTFT короткого запроса, ITL и полное время **обоих** ответов.
Переход Cache → Tune меняет несколько настроек: результат относится ко всему
профилю, не только к chunked prefill.
[Формат TimedTrace](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/datasets/datasets.py),
[подробные результаты клиента](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/benchmarks/serve.py).

## 5. Проверить кэш и speculative decoding отдельно

| Утверждение | Необходимое подтверждение |
| --- | --- |
| Запрос использовал GPU prefix cache | Приращение локальных cache hits |
| KV вернулся из RAM | Переданные CPU → GPU байты после вытеснения префикса из GPU |
| Assistant/MTP работает | Приращения предложенных и принятых токенов |
| Assistant/MTP ускоряет | Парный тест с MTP и без него: TPOT, throughput и ошибки |

Быстрый повтор не доказывает RAM-offload. Высокая доля принятия не учитывает
затраты на черновик. Если предложений не было, доля принятия не определена.
Кэш готовых ответов шлюза на пути A/B отключите или обращайтесь прямо к vLLM.

**MTP (Multi-Token Prediction)** предлагает несколько будущих токенов за шаг.
Assistant — отдельная черновая модель; её затраты тоже входят во время запроса.

<a id="offload-replay"></a>
### Возврат конкретного префикса из RAM

Этот отдельный опыт выполняется на **ручной B Cache**, без чужих запросов.
Он проверяет механизм переноса, а не ускорение рабочего профиля.
Чтобы ограниченное число запросов вытеснило GPU-кеш, в её `vllm:` временно
добавляется [`kv-cache-memory-bytes`](https://docs.vllm.ai/en/v0.31.0/configuration/engine_args/#--kv-cache-memory-bytes):

| Стенд | GPU KV-пул, байт | Вход запроса | Штатный RAM KV |
| --- | ---: | ---: | ---: |
| H100 | `2147483648` (2 GiB) | 8192 | 32 GiB |
| RTX | `268435456` (256 MiB) | 3072 | 4 GiB |

Поле явно задаёт GPU-пул вместо его автоматического расчёта по доле памяти.
Окно остаётся 16K / 4K, формат KV — FP8. Изменение проходит Git/Argo;
запуск продолжается только после Ready и проверки в логах, что пул вмещает
один такой запрос. Если проверка ёмкости не прошла, рабочий профиль возвращается,
а результат опыта не объявляется успешным.

Последовательность: исходный префикс → 32 независимых префикса → тот же исходный.
Для H100 параметры такие; в RTX меняются `NS=hardfest-rtx`,
`SERVICE=rtx-gemma-cache`, `MODEL=rtx-gemma-cache`,
`TOKENIZER=/data/modelcache/models/rtx-gemma-e2b`, `INPUT=3072`.
На клиентской машине для проверки JSON нужен `jq`.

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

У каждого ответа должны быть `completed=1`, `failed=0`, заданный вход и один
выходной токен. При любой неполной серии опыт останавливается. Исходный префикс
между вытесняющими запросами **не повторяется**. Другой seed меняет начало входа.

В отдельном терминале открывается доступ к метрикам этой же B:

```bash
kubectl --context "$GPU_CONTEXT" -n "$NS" port-forward "svc/$SERVICE" 18002:8000
```

В основном терминале сохраняется состояние непосредственно вокруг повтора:

```bash
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/before.prom || exit 1
offload_request 9001 replay || exit 1
curl --fail --max-time 10 http://127.0.0.1:18002/metrics > results/offload/after.prom || exit 1
```

У **OffloadingConnector** подтверждение — приращение `CPU_to_GPU` в метриках
передачи и external-prefix hits именно на повторе. Нулевое приращение означает,
что возврат не показан: префикс мог остаться на GPU либо исчезнуть и из RAM.
Фактическая ёмкость и округление блоков берутся из runtime, не только из формулы.
Счётчики Simple CPU относятся к другому backend и здесь не требуются.

В конце временное поле `kv-cache-memory-bytes` удаляется через Git/Argo.
После Ready сверяется восстановленный штатный пул; только затем продолжаются
сравнения Cache/Tune. NodeCache с весами этот опыт не очищает.

## 6. Найти ёмкость Qwen

**TP2 (Tensor Parallelism на двух GPU)** распределяет вычисления одной модели
между двумя картами. Qwen TP2 — другая модель и другой способ использования двух GPU.
Его скорость не является коэффициентом ускорения Gemma.

Начните с корректного ответа и короткой серии по [сценарию Qwen](../README.md#tp2).
Полная сетка concurrency: 1, 2, 4, 8, 16, 32, 50.
На каждой ступени сохраняйте одинаковые длины, число запросов, параметры
генерации и состояние кэша.

При `max-num-seqs=16` пятьдесят клиентов включают ожидающие запросы,
а не пятьдесят одновременно вычисляемых последовательностей.
Окно 256K также не означает пятьдесят заполненных окон.

Ёмкость — максимальная **повторяемая** ступень, которая укладывается
в заранее выбранные ограничения TTFT/TPOT и ошибок.

## Проверка отчёта

- [ ] Есть исходные JSON и конфигурация каждого прогона.
- [ ] Серии A/B отличаются только запланированными параметрами.
- [ ] Основная серия повторена не менее трёх раз с одинаковым прогревом.
- [ ] Сохранены отдельные прогоны и медиана результатов; p95 разных прогонов не усреднён.
- [ ] Ошибки, отмены и обрывы явно указаны.
- [ ] Измерения другого стенда не выданы за текущие результаты.
