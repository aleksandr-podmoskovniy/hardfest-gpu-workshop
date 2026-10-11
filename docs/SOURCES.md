# Источники

Версии моделей, ревизии и образ runtime закреплены в [models.lock.json](../models.lock.json).
Ссылки объясняют механизмы; измерения площадок находятся в [results](../results/).
Документация другой версии vLLM может описывать другие параметры и метрики.

| Тема | Документация | Справка стенда |
| --- | --- | --- |
| Повторное использование общего начала запроса | [vLLM: Prefix caching](https://docs.vllm.ai/en/v0.31.0/features/automatic_prefix_caching/) | [Память](MEMORY_BUDGET.md) |
| Перенос KV между GPU и RAM | [vLLM: KV offloading](https://docs.vllm.ai/en/v0.31.0/features/kv_offloading_usage/) | [Повтор после вытеснения](MEASUREMENTS.md#offload-replay) |
| Scheduler и chunked prefill | [vLLM: Optimization](https://docs.vllm.ai/en/v0.31.0/configuration/optimization/) | [Смешанная нагрузка](MEASUREMENTS.md#mixed-prefill) |
| Draft-модель и MTP | [vLLM: Speculative decoding](https://docs.vllm.ai/en/v0.31.0/features/speculative_decoding/mtp/) | [Методика измерений](MEASUREMENTS.md) |
| Нагрузка и счётчики | [Bench serve](https://docs.vllm.ai/en/v0.31.0/cli/bench/serve/), [Metrics](https://docs.vllm.ai/en/v0.31.0/usage/metrics/) | [Мониторинг](OBSERVABILITY.md) |
| Аппаратные разделы и совместная работа процессов | [NVIDIA MIG](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html), [MPS](https://docs.nvidia.com/deploy/mps/mpsv2-interface.html) | [Проверка A30](TROUBLESHOOTING.md#a30-claims) |
| Выделение устройств Kubernetes | [DRA](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/), [Device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | [Подготовка](SETUP.md) |
| Обмен двух GPU для одной модели | [NVIDIA NCCL](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/overview.html) | [Qwen TP2](MEASUREMENTS.md#qwen-load) |
| Состояние GPU | [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/) | [Диагностика](TROUBLESHOOTING.md) |

Авторские разборы: [«DRAматургия GPU в Kubernetes»](https://habr.com/ru/companies/flant/articles/1020276/)
и [«DRAйверы для GPU»](https://habr.com/ru/companies/flant/articles/1038000/).
DRA оформляет заявку на устройство; возможность его разделить зависит от карты и драйвера.

## За пределами этого стенда

| Механизм | Что изучить дальше |
| --- | --- |
| Маршрутизация с учётом кеша | [NVIDIA Dynamo](https://docs.dynamo.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/kv-aware-routing) |
| Разделение prefill и decode между исполнителями | [vLLM disaggregated prefill](https://docs.vllm.ai/en/v0.31.0/features/disagg_prefill/) |
| Другие стеки GPU sharing | [HAMi](https://project-hami.io/docs), [Volcano](https://volcano.sh/docs/keyfeatures/gpuvirtualization/) |

Сравнительных измерений этих альтернатив в мастер-классе нет.
