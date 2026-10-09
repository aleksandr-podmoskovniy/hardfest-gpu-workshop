# Термины и источники

Версии моделей и образ vLLM закреплены в [models.lock.json](../models.lock.json).
Ниже — документация для проверки параметров и механизмов из мастер-класса.

## Как читать графики и формулы

| Термин | Что означает в опыте |
| --- | --- |
| Prefill | Вычисление состояния входных токенов до продолжения истории |
| Decode | Генерация продолжения; reasoning тоже входит в неё |
| TTFT — Time To First Token | Время до первого токена на указанной границе измерения, не обязательно до финального текста в интерфейсе |
| ITL — Inter-Token Latency | Интервал между соседними токенами потока |
| TPOT — Time Per Output Token | Среднее время на токен продолжения после первого; не суммарный throughput сервиса |
| Throughput | Завершённые запросы или токены в секунду при фиксированной нагрузке |
| GQA — Grouped-Query Attention | Несколько query-голов используют общие K/V; память считаем по KV-головам |
| Sliding-window attention | Слой хранит ограниченное локальное окно, не всю историю |
| KV — Key–Value cache | Сохранённые ключи и значения attention: позволяют не пересчитывать историю с нуля |
| KV pool | Выделенная движком ёмкость кеша; занятые запросами блоки — только часть пула |
| Prefix cache | Повторное использование уже вычисленных блоков совпадающего начала запроса |
| KV offload | Хранение KV вне GPU с возвратом для вычислений; это не выгрузка весов |
| Draft / target | Предлагающая продолжение модель / основная проверяющая модель |
| MTP — Multi-Token Prediction | Предложение нескольких будущих токенов за шаг; требует совместимости модели и runtime |
| TP2 — Tensor Parallelism на двух GPU | Вычисления одной модели разделены между двумя GPU; это не две независимые копии |

`1 KiB = 1024 байта`, `1 MiB = 1024²`, `1 GiB = 1024³`.
В расчётах полного контекста:

- `128K = 131072 токена`;
- `256K = 262144 токена`.

Оба значения включают вход и ответ. Байты файла с текстом не равны токенам.

## Как разделяются GPU и выдаются права

| Термин | Простое объяснение |
| --- | --- |
| MIG — Multi-Instance GPU | Аппаратное разделение GPU на устройства с собственной долей памяти и вычислительных ресурсов |
| MPS — Multi-Process Service | Совместное выполнение CUDA-процессов на одном доступном устройстве; это не отдельные аппаратные разделы |
| DRA — Dynamic Resource Allocation | Механизм Kubernetes для заявок на устройства; драйвер выделяет и подготавливает ресурс |
| Device plugin | Плагин, который публикует доступные устройства в Kubernetes как extended resources |
| VK — Virtual Key | Личный ключ шлюза с разрешёнными моделями, лимитами и учётом запросов |
| OIDC — OpenID Connect | Вход через внешнюю систему учётных записей |
| MCP — Model Context Protocol | Протокол подключения инструментов к модели |
| RBAC — Role-Based Access Control | Права, назначенные пользователям через роли |
| RAG — Retrieval-Augmented Generation | Добавление найденных документов к вопросу модели |

## vLLM 0.31.0

| Раздел | Что проверять |
| --- | --- |
| [Optimization](https://docs.vllm.ai/en/v0.31.0/configuration/optimization/) | Планировщик, chunked prefill и настройку памяти |
| [Automatic Prefix Caching](https://docs.vllm.ai/en/v0.31.0/features/automatic_prefix_caching/) | Точное совпадение префикса, экономию prefill и отсутствие экономии decode |
| [KV offloading](https://docs.vllm.ai/en/v0.31.0/features/kv_offloading_usage/) | Выгрузку KV-кэша и объём памяти коннектора |
| [MTP и Gemma assistant](https://docs.vllm.ai/en/v0.31.0/features/speculative_decoding/mtp/) | Метод MTP, отдельные веса assistant и общий KV с основной Gemma |
| [Metrics](https://docs.vllm.ai/en/v0.31.0/usage/metrics/) | Метрики времени обработки запросов |
| [Bench serve](https://docs.vllm.ai/en/v0.31.0/cli/bench/serve/) | Параметры официального нагрузочного клиента |

> [!IMPORTANT]
> Имена и метки метрик сверяйте с ответом `/metrics` запущенного сервера.
> Приведённые ссылки относятся к vLLM 0.31.0.

## GPU и Kubernetes

| Раздел | Что проверять |
| --- | --- |
| [NVIDIA MIG deployment considerations](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html) | Ограничения MIG и MPS |
| [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/) | Топологию и показатели работы GPU |
| [Kubernetes DRA](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/) | Заявки, классы устройств и выделение ресурсов |
| [Device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | Публикацию устройств через extended resources |

## Что ещё существует, но не разворачивается в этой работе

### Cache-aware routing

Выбор реплики с учётом доступного KV и её нагрузки.
Это следующий уровень после локального prefix cache, а не кеш готовых ответов.
Пример реализации — [KV-aware router NVIDIA Dynamo](https://docs.dynamo.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/kv-aware-routing).

### Prefill/decode disaggregation

Разделение стадий по исполнителям с передачей
между ними KV. Оно позволяет по-разному настраивать эти стадии, но добавляет
цену передачи и координации. [Disaggregated prefilling в vLLM 0.31](https://docs.vllm.ai/en/v0.31.0/features/disagg_prefill/).

### HAMi и Volcano

**HAMi** — стек совместного использования разнородных ускорителей с собственными
механизмами планирования и управления ресурсами.

**Volcano** — планировщик workloads с поддержкой GPU sharing, в том числе интеграций с HAMi.

Мы используем DRA и MIG/MPS установленного драйвера, чтобы проследить заявочную
модель платформы до реального устройства. Это выбор предмета опыта,
не доказательство превосходства над альтернативами.
[HAMi](https://project-hami.io/docs), [Volcano GPU virtualization](https://volcano.sh/docs/keyfeatures/gpuvirtualization/).

**Vulkan** — другой термин: графический и compute API, а не планировщик Kubernetes.
[Официальное описание Vulkan](https://www.vulkan.org/).
