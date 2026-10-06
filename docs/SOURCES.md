# Источники

Версии моделей и образ vLLM закреплены в [models.lock.json](../models.lock.json).
Ниже — документация для проверки параметров и механизмов из мастер-класса.

## vLLM 0.31.0

| Раздел | Что проверять |
| --- | --- |
| [Optimization](https://docs.vllm.ai/en/v0.31.0/configuration/optimization/) | Планировщик, chunked prefill и настройку памяти |
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
| [Kubernetes DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) | Заявки, классы устройств и выделение ресурсов |
