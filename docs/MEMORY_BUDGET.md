# Бюджет памяти

Перед запуском нужно проверить два ограничения: память GPU и RAM узла.
На GPU размещаются веса модели, активный KV-кеш и рабочие буферы, включая
память для CUDA graphs. RAM нужна процессам, загрузчику весов и кешу offload.
Поэтому даже при свободной GPU второму сервису может не хватить RAM.

KV cache хранит результаты attention для уже обработанных токенов.
Offload сохраняет блоки в RAM, но возвращает их на GPU для вычислений.
Это не выгрузка весов и не увеличение активного GPU-пула на размер RAM-кеша.

## Окно и число запросов

Окно включает вход, шаблон чата и ответ: `128K = 131072`, `256K = 262144` токена.
При выходном бюджете **32K** в окне 128K остаётся **96K на вход**,
в окне 256K — **224K**. Входной бюджет включает системный текст и шаблон.
`max-num-seqs=10` разрешает десять последовательностей, но не обещает десять
полных окон одновременно в GPU.

| Площадка | Профиль | Окно и назначение |
| --- | --- | --- |
| PRO 6000 | Gemma A | Автоподбор по памяти: `max-model-len: -1` |
| PRO 6000 | Gemma B, Qwen TP2 | 256K: 224K входа + до 32K выхода; лимит 10 последовательностей |
| H100 | Gemma Base/Cache; Tune | 16K у Base/Cache, 128K у Tune |
| H100 | Qwen3.8 TP2 | 256K, до 16 последовательностей |
| RTX 5060 | Gemma E2B Base/Cache; Tune | 4K у Base/Cache, 128K у Tune; Tune до двух последовательностей |
| RTX 5060 | Qwen3.5-9B TP2 | 128K, до восьми последовательностей |

Число клиентов и число активных запросов различаются: запросы сверх ёмкости
GPU-пула ждут в очереди. Размер RAM-кеша не прибавляется к активному GPU-пулу.

## Формула KV

Для одинаковых слоёв и независимых историй:

```text
KV = 2 × b × L × Hkv × D × S × N байт
```

| Обозначение | Что учитывает |
| --- | --- |
| `2` | Два массива: ключи K и значения V |
| `b` | Байт на элемент: 2 для BF16, 1 для FP8 |
| `L` | Число слоёв, которые хранят собственный KV |
| `Hkv` и `D` | Число KV-голов и размерность каждой головы |
| `S` | Число токенов входа и уже созданного ответа |
| `N` | Число независимых историй одинаковой длины |

При разных длинах вместо `S × N` берётся сумма длин историй.
Если несколько запросов используют общий префикс, его блоки считаются один раз.
Для перевода в GiB результат делится на `1024³`.

Формула даёт полезные K/V. Округление блоков, allocator, assistant и пики prefill
добавляются отдельно; фактический пул берётся из логов runtime.

## Gemma 31B

[Схема full и sliding KV](../assets/11-gemma-kv.svg) показывает, какая часть
кеша растёт вместе с контекстом.

[Закреплённая конфигурация](https://huggingface.co/google/gemma-4-31B-it/blob/842da3794eaa0b77d5f08bae87a17459d91ff475/config.json):
10 слоёв full attention хранят всю историю, ещё 50 слоёв sliding-window
attention — только последние 1024 токена. У первых по 4 KV-головы размерности
512, у вторых — по 16 голов размерности 256. Каждый слой хранит собственный KV.

```text
KV(S,N,b) = N × 2 × b × [10 × 4 × 512 × S + 50 × 16 × 256 × min(S,1024)]
```

`attention_k_eq_v` не убирает множитель 2:
[vLLM хранит оба тензора](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/gemma4.py#L513).
После заполнения локального окна продолжает расти только full-часть:
на 80 KiB за токен в BF16 или на 40 KiB в FP8. Sliding-часть занимает
постоянные 0,78125 GiB в BF16 или 0,390625 GiB в FP8.

| История | Одна: BF16 / FP8 | 10 независимых: BF16 / FP8 |
| --- | ---: | ---: |
| 128K | 10,78125 / 5,390625 GiB | 107,8125 / 53,90625 GiB |
| 256K | 20,78125 / 10,390625 GiB | 207,8125 / 103,90625 GiB |

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

| История | Одна: BF16 / FP8 | Десять: BF16 / FP8 |
| --- | ---: | ---: |
| 4K | 30 / 15 MiB | 300 / 150 MiB |
| 64K | 390 / 195 MiB | 3900 / 1950 MiB |
| 128K | 774 / 387 MiB | 7740 / 3870 MiB |

У закреплённой E2B нет окна 256K. Десять историй — расчёт полезных KV-данных,
не подтверждение десяти одновременных генераций. Отключение chunked prefill для E2B не поддерживается:
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

| История | Одна на GPU | Десять на GPU |
| --- | ---: | ---: |
| 32K | 0,28125 GiB | 2,8125 GiB |
| 128K | 1,125 GiB | 11,25 GiB |
| 256K, только расчёт | 2,25 GiB | 22,5 GiB |

Состояние linear attention, веса и буферы добавляются отдельно. Десять полных
128K с весами не помещаются на этих RTX. В [тяжёлой серии](../results/rtx5060-heavy/README.md)
десять запросов по 98 304 входных + 32 768 выходных токенов завершились,
но часть времени провели в очереди; движок вытеснял активный KV.
Формула не переносится на Qwen3.8 H100/PRO 6000.

<a id="ram-выбрать-порядок-запуска"></a>
## Сколько RAM оставить для сервисов

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
