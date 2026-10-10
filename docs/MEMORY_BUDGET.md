# Бюджет памяти: Gemma и Qwen

Этот расчёт отвечает на два вопроса: поместится ли история в GPU и хватит ли
RAM узлу при загрузке и работе. Это разные бюджеты; свободная GPU не гарантирует
возможность запуска ещё одного сервиса.

| Память | Расход | Проверка |
| --- | --- | --- |
| GPU | Веса, активный KV, буферы, CUDA graphs | Стартовые логи vLLM и метрики |
| RAM | Загрузка, компиляция, процессы и CPU KV | Allocatable, MemAvailable, память контейнера |

**KV cache** хранит ключи и значения уже обработанных токенов.
Offload сохраняет блоки в RAM между обращениями, но для attention возвращает их
на GPU. Веса Gemma остаются на GPU.

## Окно модели и окно профиля

| Модель / стенд | Base и Cache | Tune / рабочий профиль | Максимум модели |
| --- | ---: | ---: | ---: |
| Gemma 31B / H100 | 16K | 128K | 256K |
| Gemma E2B / RTX | 4K | 128K, 2 последовательности | 128K |
| Qwen3.5-9B / RTX TP2 | — | 128K, 8 последовательностей | 256K |

Отключение chunked prefill у E2B официально не поддерживается:
Base/Cache — строгий контроль 4K, не рекомендуемый рабочий режим.
Для Qwen3.8 на H100 отдельный [профиль](../values/qwen-tp2.yaml) задаёт
256K и до 16 последовательностей.

**Окно включает вход, шаблон и ответ.** При 128K и выходе 8192 токена
вход ограничен `131072 − 8192 = 122880`.
Число активных последовательностей не обещает столько же полностью заполненных окон.

## KV: обозначения и границы расчёта

Для одинаковых слоёв:

```text
KV = 2 × b × L × Hkv × D × S × N байт
```

| Символ | Значение |
| --- | --- |
| `2` | Ключи K и значения V |
| `b` | Байт на элемент: BF16 — 2, FP8 — 1 |
| `L` | Слои с собственным KV |
| `Hkv`, `D` | Число KV-голов и размерность |
| `S`, `N` | Вся длина истории и число независимых историй |

При разных длинах вместо `S × N` используется их сумма.
Общий префикс считается по уникальным блокам, а не умножается на `N`.
Далее — полезные K/V: буферы, выравнивание блоков, assistant и пики prefill
учитываются дополнительно.

## Gemma 31B: 128K и 256K

![Gemma 31B: полный контекст растёт, локальное окно остаётся ограниченным](../assets/11-gemma-kv.svg)

Архитектура из [закреплённого config.json](https://huggingface.co/google/gemma-4-31B-it/blob/842da3794eaa0b77d5f08bae87a17459d91ff475/config.json):

| Attention | Слоёв | KV-голов | Размерность | Хранимая длина |
| --- | ---: | ---: | ---: | --- |
| Full | 10 | 4 | 512 | `S` |
| Sliding | 50 | 16 | 256 | `min(S,1024)` |

1024 — локальное окно слоя, не длина запроса. Межслойного sharing нет.

```text
KV(S, N, b) = N × 2 × b × (
    10 × 4 × 512 × S
  + 50 × 16 × 256 × min(S, 1024)
) байт
```

Флаг `attention_k_eq_v` не убирает множитель 2:
[vLLM хранит оба тензора](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/gemma4.py#L513).

При заполненном локальном окне full-часть растёт на **80 KiB/токен в BF16**
или **40 KiB в FP8**. Sliding-часть остаётся 0,78125 / 0,390625 GiB.

| Вся история | Историй | BF16 | FP8 |
| --- | ---: | ---: | ---: |
| 128K | 1 | 10,78125 GiB | 5,390625 GiB |
| 128K | 8 | 86,25 GiB | 43,125 GiB |
| 256K | 1 | 20,78125 GiB | 10,390625 GiB |
| 256K | 8 | 166,25 GiB | 83,125 GiB |

При контрольных 16K одна история занимает 2,03125 / 1,015625 GiB.
Восемь историй 128K требуют **43,125 GiB активного FP8 KV**: сравнивать это
нужно с оставшимся GPU-пулом, не с RAM-offload.
[Расчёт через awk](../README.md#memory).

## Gemma E2B: собственный и общий KV слоёв

У [E2B](https://huggingface.co/google/gemma-4-E2B-it/blob/3e22461f65e89153144f8adb70e3b8c2cc9845a7/config.json)
35 слоёв; последние 20 переиспользуют KV ранних слоёв.
Собственный KV имеют:

| Attention | Слоёв | KV-голов | Размерность | Хранимая длина |
| --- | ---: | ---: | ---: | --- |
| Full | 3 | 1 | 512 | `S` |
| Sliding | 12 | 1 | 256 | `min(S,512)` |

`num_attention_heads=8` — число query-голов, не KV-голов.

```text
KV(S,N,b) = N × 2 × b × [3 × 512 × S + 12 × 256 × min(S,512)] байт

При S ≥ 512, одна история BF16:
KV(S) = 6 KiB × S + 6 MiB
```

При 128K: full 768 MiB + sliding 6 MiB = **774 MiB BF16**, или **387 MiB FP8**.

| Вся история | Одна: BF16 / FP8 | Восемь: BF16 / FP8 |
| --- | ---: | ---: |
| 4K | 30 / 15 MiB | 240 / 120 MiB |
| 64K | 390 / 195 MiB | 3120 / 1560 MiB |
| 128K | 774 / 387 MiB | 6192 / 3096 MiB |

**256K у закреплённой E2B нет.** Восемь историй в таблице — расчёт, не режим
RTX-профиля с двумя последовательностями. Нужны также память assistant и резерв
движка. В TP2 единственная KV-голова E2B реплицировалась бы на каждом rank,
а не делилась пополам. Здесь E2B работает на одной карте.
[Реализация sharing](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/gemma4.py#L473).

## Qwen3.5-9B на RTX: расчёт на одну GPU

У [Qwen](https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/config.json)
8 full-attention и 24 linear-attention слоя.
[MTP добавляет один full-слой](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/models/qwen3_5_mtp.py#L111).
У full-слоя 4 KV-головы размерности 256; TP2 оставляет две на GPU.

```text
FP8 KV на GPU = 2 × 1 × 9 × (4 / 2) × 256 × S × N байт
             = 9 KiB × S × N
```

| Вся история | Одна на GPU | Восемь на GPU |
| --- | ---: | ---: |
| 32K | 0,28125 GiB | 2,25 GiB |
| 128K | 1,125 GiB | 9 GiB |
| 256K, только расчёт | 2,25 GiB | 18 GiB |

Состояние linear attention, веса и буферы добавляются отдельно.
**Восемь полных 128K с весами не помещаются на этих RTX.**
Проверяются один длинный вход и восемь по 32K; рецепт остаётся 128K.
Эта формула не переносится на другую модель Qwen3.8/H100.

<a id="ram-выбрать-порядок-запуска"></a>
## RAM: порядок запуска

Лимиты [профилей H100](../values/) — бюджет размещения, не постоянное потребление:

| Профиль | RAM request / limit | /dev/shm | CPU request |
| --- | ---: | ---: | ---: |
| Gemma A | 24 / 48 GiB | 8 GiB | 4 |
| B Cache или Tune, CPU KV 32 GiB | 56 / 80 GiB | 40 GiB | 4 |
| Qwen TP2, CPU KV 16 GiB | 80 / 104 GiB | 24 GiB | 12 |

| Совместный запуск | Сумма RAM request / limit | Решение |
| --- | ---: | --- |
| A + B | 80 / 128 GiB | На узле 128 GiB — последовательно |
| Ручная B + платформенная Gemma | 112 / 160 GiB | Планировать минимум 192 GiB |

**На 128 GiB A останавливается до запуска B; B — до платформенной Gemma.
Перед Qwen освобождаются обе GPU.** Остаток RAM нужен ОС и другим Pod.
Allocatable должен вмещать requests, а MemAvailable — пики реального расхода,
включая загрузку assistant. При 192 GiB после limits двух B остаётся 32 GiB;
это не гарантия отсутствия пиков.

### Не считать shared memory дважды

32 GiB CPU KV находятся внутри tmpfs `/dev/shm` ёмкостью 40 GiB.
Использованные страницы входят в limit контейнера:
**32 + 40 + 80 GiB складывать нельзя**.
40 GiB — предел tmpfs, не немедленно выделенная память.

У Qwen 16 GiB CPU KV — бюджет всей группы TP2, по 8 GiB на rank,
не по 16 GiB на каждую карту.
[Расчёт CPU-пула](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/kv_offload/cpu/spec.py#L97),
[mmap-область](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/kv_offload/cpu/shared_offload_region.py#L64).

При увеличении CPU KV на X GiB **requests, limits и shmSize увеличиваются
на X GiB в одном коммите**; Helm не делает это автоматически.

### Размер файлов не равен пику RAM

В `models.lock.json`: Gemma — 62 546 338 248 байт (58,25 GiB),
Qwen H100 — 132 680 249 378 байт (123,57 GiB).
При `safetensors-load-strategy: lazy` файлы отображаются в память,
страницы могут освобождаться; поэтому файл бывает больше RAM limit.
Это не исключает OOM при загрузке, компиляции и работе.
[Загрузчик vLLM](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/model_loader/weight_utils.py#L800).

Qwen-профиль рассчитан на 16 vCPU / 128 GiB, не 64 GiB.
После его limit остаётся 24 GiB для узла и соседей; фактические пиковые значения
проверяются до нагрузки.

<a id="verify-kv"></a>
## Проверка: формула → runtime → нагрузка

| Величина | Где смотреть | Что не подменять |
| --- | --- | --- |
| Полезный KV истории | Формула и фактические токены | Не вся память GPU |
| Выделенный KV-пул | `Available KV cache memory`, `GPU KV cache size` в логах | Не текущая занятость |
| Использованная доля пула | `vllm:kv_cache_usage_perc` | Не GiB |
| Запас RAM | Allocatable, MemAvailable, cgroup | Не один снимок `kubectl top` |

### Конфигурация и логи

Нужен Pod runtime, не контроллера. Для H100 используется `NS=hardfest-demo`.

```bash
export NS=hardfest-rtx
kubectl --context "$GPU_CONTEXT" -n "$NS" get pods
export POD=REPLACE_RUNTIME_POD
kubectl --context "$GPU_CONTEXT" -n "$NS" get pod "$POD" \
  -o jsonpath='{range .spec.containers[*]}{.name}{"\t"}{.image}{"\n"}{.command}{"\n"}{.args}{"\n"}{end}'
export CONTAINER=REPLACE_RUNTIME_CONTAINER
kubectl --context "$GPU_CONTEXT" -n "$NS" logs "$POD" -c "$CONTAINER" --tail=-1
```

Ручной профиль читается из ConfigMap, связанной с volumeMount `/etc/vllm`:

```bash
kubectl --context "$GPU_CONTEXT" -n "$NS" get pod "$POD" \
  -o jsonpath='{range .spec.volumes[*]}{.name}{"\t"}{.configMap.name}{"\n"}{end}'
export PROFILE_CONFIGMAP=REPLACE_PROFILE_CONFIGMAP
kubectl --context "$GPU_CONTEXT" -n "$NS" get configmap "$PROFILE_CONFIGMAP" \
  -o go-template='{{index .data "profile.yaml"}}'
```

Проверяются checksum Pod и принятые в startup-логах параметры:
`max-model-len`, `max-num-seqs`, `max-num-batched-tokens`,
`kv-cache-dtype`, offload и speculative config.
Для AI Inference сверяются заказ, план и runtime. Данные TP2 сохраняются
по каждому rank; `Maximum concurrency` из логов — оценка, не измерение.

### Пики RAM и известная нагрузка

Команды ниже читающие; имена узла и Pod заменяются на проверяемые:

```bash
export GPU_NODE=REPLACE_GPU_NODE
kubectl --context "$GPU_CONTEXT" describe node "$GPU_NODE"
kubectl --context "$GPU_CONTEXT" -n "$NS" top pods
kubectl --context "$GPU_CONTEXT" -n "$NS" get events --sort-by=.lastTimestamp

kubectl --context "$GPU_CONTEXT" -n "$NS" get pods
kubectl --context "$GPU_CONTEXT" -n "$NS" describe pod "$POD"
```

В Prometheus сравниваются `node_memory_MemAvailable_bytes`,
`container_memory_working_set_bytes`, `container_memory_rss`,
`container_memory_cache`; они не складываются.
В событиях ищутся OOMKilled, в Pod — тип и предел `/dev/shm`.
Shell внутри distroless runtime не требуется.

Нагрузка: один запрос → два независимых → повтор префикса.
Сохраняются фактические токены, очередь, вытеснения, prefix hits и CPU→GPU bytes.
[Методика](MEASUREMENTS.md). Заранее выделенный пул объясняет, почему
`nvidia-smi` почти не меняется при новом запросе.

Расхождение с формулой проверяется по shared layers, формату KV, буферам
assistant, округлению и пику prefill
([гибридный allocator](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/core/kv_cache_utils.py#L1265),
[sliding window](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/kv_cache_interface.py#L793)).
Длинное окно подтверждается **полным длинным запросом без OOM и рестартов**,
не коротким ответом при `max-model-len: 131072`.
