# Бюджет памяти

На GPU должны поместиться веса, активный KV cache, буферы и CUDA graphs.
В RAM — процессы, загрузка весов и CPU KV. Свободная GPU не гарантирует,
что на ноде хватит RAM для второго сервиса.

KV cache хранит результаты attention для уже обработанных токенов.
Offload сохраняет блоки в RAM, но возвращает их на GPU для вычислений.
Это не выгрузка весов и не увеличение активного GPU-пула на размер RAM-кеша.

## Окно и число запросов

Окно включает вход, шаблон чата и ответ: `128K = 131072`, `256K = 262144` токена.
Например, при окне 128K и резерве ответа 8192 вход ограничен 122880 токенами.
`max-num-seqs=10` разрешает десять последовательностей, но не обещает десять
полных окон одновременно в GPU.

| Площадка | Профиль | Окно и назначение |
| --- | --- | --- |
| PRO 6000 | Gemma A | 134752 входа + 32768 выхода в capacity-серии, 10 запросов |
| PRO 6000 | Gemma B, Qwen TP2 | 229376 + 32768 = 262144, 10 запросов |
| H100 | Gemma Base/Cache → Tune | 16K → 128K |
| H100 | Qwen3.8 TP2 | 256K, до 16 последовательностей |
| RTX 5060 | Gemma E2B Base/Cache → Tune | 4K → 128K, Tune до двух последовательностей |
| RTX 5060 | Qwen3.5-9B TP2 | 128K, до восьми последовательностей |

Capacity-серия PRO 6000 использовала принудительную генерацию `ignore-eos`.
Зафиксированный максимум active — 1/2/6 для A/B/Qwen: десять отправленных
запросов не означают десять одновременно активных. Это проверка прямого runtime,
а не интерактивной задержки чата. [Результаты](../results/pro6000/README.md).

## Формула KV

Для одинаковых слоёв и независимых историй:

```text
KV = 2 × b × L × Hkv × D × S × N байт
```

`2` — ключи K и значения V; `b` — байт на элемент (BF16: 2, FP8: 1);
`L` — слои с собственным KV; `Hkv` — KV-головы, `D` — их размерность;
`S` — длина истории, `N` — число историй. При разных длинах берётся их сумма.
Общий префикс считается по уникальным блокам. `GiB = 1024³` байт.

Формула даёт полезные K/V. Округление блоков, allocator, assistant и пики prefill
добавляются отдельно; фактический пул берётся из логов runtime.

## Gemma 31B

[Схема full и sliding KV](../assets/11-gemma-kv.svg) показывает, какая часть
кеша растёт вместе с контекстом.

[Закреплённая конфигурация](https://huggingface.co/google/gemma-4-31B-it/blob/842da3794eaa0b77d5f08bae87a17459d91ff475/config.json):
10 full-attention слоёв с 4 KV-головами по 512 и 50 sliding-слоёв
с 16 головами по 256, локальное окно 1024. Межслойного sharing нет.

```text
KV(S,N,b) = N × 2 × b × [10 × 4 × 512 × S + 50 × 16 × 256 × min(S,1024)]
```

`attention_k_eq_v` не убирает множитель 2:
[vLLM хранит оба тензора](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/gemma4.py#L513).
После заполнения локального окна full-часть растёт на 80 KiB/токен BF16
или 40 KiB FP8; sliding-часть остаётся 0,78125/0,390625 GiB.

| История | Одна: BF16 / FP8 | 8 независимых, H100: BF16 / FP8 | 10 независимых: BF16 / FP8 |
| --- | ---: | ---: | ---: |
| 128K | 10,78125 / 5,390625 GiB | 86,25 / 43,125 GiB | 107,8125 / 53,90625 GiB |
| 256K | 20,78125 / 10,390625 GiB | 166,25 / 83,125 GiB | 207,8125 / 103,90625 GiB |

Это расчёт памяти, не результат конкурентной нагрузки. Десять независимых
полных окон 256K требуют 103,90625 GiB полезного FP8 KV ещё до весов и буферов.
Повторы общего префикса и очередь меняют расход, поэтому их нужно учитывать
в [описании нагрузки](MEASUREMENTS.md).

## Gemma E2B на RTX 5060

У [E2B](https://huggingface.co/google/gemma-4-E2B-it/blob/3e22461f65e89153144f8adb70e3b8c2cc9845a7/config.json)
35 слоёв; последние 20 используют KV ранних. Собственный KV имеют три full-слоя
(одна голова по 512) и 12 sliding-слоёв (одна по 256, окно 512).
`num_attention_heads=8` — query-головы, не KV.

```text
KV(S,N,b) = N × 2 × b × [3 × 512 × S + 12 × 256 × min(S,512)]
```

| История | Одна: BF16 / FP8 | Восемь: BF16 / FP8 |
| --- | ---: | ---: |
| 4K | 30 / 15 MiB | 240 / 120 MiB |
| 64K | 390 / 195 MiB | 3120 / 1560 MiB |
| 128K | 774 / 387 MiB | 6192 / 3096 MiB |

У закреплённой E2B нет окна 256K. Восемь историй — расчёт, а профиль допускает
две последовательности. Отключение chunked prefill для E2B не поддерживается:
Base/Cache 4K используются как контроль. E2B здесь работает на одной карте;
при TP2 её единственная KV-голова реплицировалась бы на каждом rank.
[Реализация sharing](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/gemma4.py#L473).

## Qwen3.5-9B на RTX 5060

[Конфигурация](https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/config.json)
содержит 8 full- и 24 linear-attention слоя. [MTP](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/qwen3_5_mtp.py#L111)
добавляет один full-слой. При четырёх KV-головах по 256 и TP2:

```text
FP8 KV на GPU = 2 × 1 × 9 × (4 / 2) × 256 × S × N = 9 KiB × S × N
```

| История | Одна на GPU | Восемь на GPU |
| --- | ---: | ---: |
| 32K | 0,28125 GiB | 2,25 GiB |
| 128K | 1,125 GiB | 9 GiB |
| 256K, только расчёт | 2,25 GiB | 18 GiB |

Состояние linear attention, веса и буферы добавляются отдельно. Восемь полных
128K с весами не помещаются на этих RTX; проверяются восемь по 32K и один длинный
вход. Формула не переносится на Qwen3.8 H100/PRO 6000.

<a id="ram-выбрать-порядок-запуска"></a>
## RAM: порядок запуска

| H100-профиль | RAM request / limit | `/dev/shm` | CPU request |
| --- | ---: | ---: | ---: |
| Gemma A | 24 / 48 GiB | 8 GiB | 4 |
| B Cache/Tune, CPU KV 32 GiB | 56 / 80 GiB | 40 GiB | 4 |
| Qwen TP2, CPU KV 16 GiB | 80 / 104 GiB | 24 GiB | 12 |

На 128 GiB сначала остановите A, затем запускайте B; перед платформенной Gemma
остановите B. Две B требуют 112/160 GiB request/limit: планируйте от 192 GiB
и проверяйте остаток для ОС и других Pod. Qwen занимает обе GPU.

CPU KV находится внутри `/dev/shm`, а использованные страницы tmpfs входят
в RAM limit контейнера: складывать 32 + 40 + 80 GiB нельзя.
У Qwen 16 GiB CPU KV — бюджет TP2-группы, по 8 GiB на rank
([реализация](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/kv_offload/cpu/spec.py#L97)).
Увеличение CPU KV требует согласованного изменения requests, limits и shm.

Размер файлов весов не равен пику RAM: lazy-загрузчик отображает файлы в память,
но загрузка, компиляция и работа всё равно могут вызвать OOM.
Сверяйте Allocatable, MemAvailable, cgroup и пики; одного `kubectl top` недостаточно.

<a id="verify-kv"></a>
## Сверить расчёт с runtime

Укажите namespace, Pod и контейнер модели:

```bash
export NS=hardfest-demo POD=REPLACE_RUNTIME_POD CONTAINER=REPLACE_RUNTIME_CONTAINER
kubectl --context "$GPU_CONTEXT" -n "$NS" get pod "$POD" \
  -o jsonpath='{range .spec.containers[*]}{.name}{"\t"}{.image}{"\n"}{.command}{"\n"}{.args}{"\n"}{end}'
kubectl --context "$GPU_CONTEXT" -n "$NS" logs "$POD" -c "$CONTAINER" --tail=-1
kubectl --context "$GPU_CONTEXT" -n "$NS" get pod "$POD" \
  -o jsonpath='{range .spec.volumes[*]}{.name}{"\t"}{.configMap.name}{"\n"}{end}'
export PROFILE_CONFIGMAP=REPLACE_PROFILE_CONFIGMAP
kubectl --context "$GPU_CONTEXT" -n "$NS" get configmap "$PROFILE_CONFIGMAP" \
  -o go-template='{{index .data "profile.yaml"}}'
```

Проверьте принятые `max-model-len`, `max-num-seqs`, `max-num-batched-tokens`,
KV dtype, offload и speculative config. `Available KV cache memory` и
`GPU KV cache size` — выделенный пул; `vllm:kv_cache_usage_perc` — его доля.
Заранее выделенный пул объясняет почти неизменный расход в `nvidia-smi`.

Сохраните данные каждого TP rank. `Maximum concurrency` в startup-логе — оценка.
Реальная вместимость подтверждается законченной нагрузкой с известными длинами,
Running/Waiting, вытеснениями и отсутствием OOM/рестартов.
