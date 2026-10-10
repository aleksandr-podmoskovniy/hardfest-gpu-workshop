# Документация по темам мастер-класса

Точные модели, revision и образ vLLM записаны в [models.lock.json](../models.lock.json).
Ссылки ниже помогают проверить механизм или параметр, а не заменяют проверку
запущенного сервиса.

## Память и длинный контекст

**KV cache** хранит результаты attention для уже обработанных токенов.
Prefix cache повторно использует совпадающее начало запроса, offload переносит
KV между GPU и RAM. Это не кэш готовых ответов и не выгрузка весов.

| Вопрос | Источник |
| --- | --- |
| Как учитывать архитектуру и окно модели? | [Расчёты памяти этого стенда](MEMORY_BUDGET.md) и конфигурации моделей из lock-файла |
| Когда prefix cache экономит работу? | [vLLM: Automatic Prefix Caching](https://docs.vllm.ai/en/v0.31.0/features/automatic_prefix_caching/) |
| Как устроена выгрузка KV? | [vLLM: KV offloading](https://docs.vllm.ai/en/v0.31.0/features/kv_offloading_usage/) |

В расчётах `128K = 131072`, `256K = 262144` токена **входа и ответа вместе**.
Размер файла в байтах не равен числу токенов. `GiB = 1024³` байт.

## Вычисления и измерения

**Prefill** обрабатывает вход, **decode** генерирует продолжение, включая
рассуждения. Draft-модель предлагает токены, основная проверяет их.
MTP — механизм предложения нескольких будущих токенов; он требует поддержки
конкретной моделью и runtime.

| Вопрос | Источник |
| --- | --- |
| Как работают chunked prefill и настройки scheduler? | [vLLM: Optimization](https://docs.vllm.ai/en/v0.31.0/configuration/optimization/) |
| Как подключаются MTP и Gemma assistant? | [vLLM: MTP](https://docs.vllm.ai/en/v0.31.0/features/speculative_decoding/mtp/) |
| Что измеряют счётчики? | [vLLM: Metrics](https://docs.vllm.ai/en/v0.31.0/usage/metrics/) и [панели стенда](OBSERVABILITY.md) |
| Как воспроизвести нагрузку? | [vLLM: Bench serve](https://docs.vllm.ai/en/v0.31.0/cli/bench/serve/) и [методика A/B](MEASUREMENTS.md) |

Имена метрик сверяются с `/metrics` работающего vLLM 0.31.0. Документация другой
версии может описывать другие счётчики.

## Размещение на GPU

**MIG** делит GPU на аппаратные части. **MPS** позволяет CUDA-процессам работать
совместно на устройстве. **DRA** описывает заявки на устройства в Kubernetes;
их выделение и подготовку выполняет драйвер.

| Вопрос | Источник |
| --- | --- |
| Какие ограничения у MIG и MPS? | [NVIDIA: MIG deployment considerations](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html) |
| Как проверить карту и её разделы? | [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/) |
| Чем заявки отличаются от extended resources? | [Kubernetes DRA](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/) и [Device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) |
| Как GPU обмениваются данными при TP2? | [NVIDIA NCCL](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/overview.html) |

TP2 делит вычисления **одной модели** между двумя GPU, а не создаёт две копии.
Авторские разборы: [«DRAматургия GPU в Kubernetes»](https://habr.com/ru/companies/flant/articles/1020276/)
и [«DRAйверы для GPU»](https://habr.com/ru/companies/flant/articles/1038000/).
Поддержка конкретной карты проверяется по документации драйвера.

## Следующие шаги — вне текущего стенда

| Механизм | Что меняет | Документация |
| --- | --- | --- |
| Cache-aware routing | Выбирает реплику с учётом уже доступного KV и нагрузки | [NVIDIA Dynamo](https://docs.dynamo.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/kv-aware-routing) |
| Prefill/decode disaggregation | Разносит этапы по исполнителям, добавляя передачу KV | [vLLM](https://docs.vllm.ai/en/v0.31.0/features/disagg_prefill/) |
| HAMi | Предлагает другой стек совместного использования ускорителей | [HAMi](https://project-hami.io/docs) |
| Volcano | Планирует workloads, поддерживает GPU sharing и интеграции с HAMi | [Volcano](https://volcano.sh/docs/keyfeatures/gpuvirtualization/) |

Здесь проверяется путь DRA → драйвер → MIG/MPS. Это граница опыта,
а не результат сравнительного теста альтернатив.
