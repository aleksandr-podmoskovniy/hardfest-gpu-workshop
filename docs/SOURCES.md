# Источники

Версии моделей и образ vLLM закреплены в [models.lock.json](../models.lock.json). Состояние их проверки описано в [готовности примеров](STATUS.md).

## Документация

- [vLLM 0.30.0: optimization](https://docs.vllm.ai/en/v0.30.0/configuration/optimization/) — планировщик, chunked prefill и настройка памяти.
- [vLLM 0.30.0: KV offloading](https://docs.vllm.ai/en/v0.30.0/features/kv_offloading_usage/) — выгрузка KV-кэша и объём памяти коннектора.
- [vLLM 0.30.0: speculative decoding](https://docs.vllm.ai/en/v0.30.0/features/speculative_decoding/) — Gemma assistant через MTP.
- [vLLM 0.30.0: metrics](https://docs.vllm.ai/en/v0.30.0/usage/metrics/) — метрики времени обработки запросов. Имена и метки сверяйте с ответом `/metrics` своего сервера.
- [vLLM 0.30.0: bench serve](https://docs.vllm.ai/en/v0.30.0/cli/bench/serve/) — официальный нагрузочный клиент.
- [NVIDIA MIG deployment considerations](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html) — ограничения MIG и MPS.
- [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/) — топология и показатели работы GPU.
- [Kubernetes DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) — заявки, классы устройств и выделение ресурсов.

## Предыдущие материалы

- Презентация `HardFest-v1_210926.pptx` — теоретические примеры и последовательность тем. [Соответствие слайдов разделам](SLIDES_MAP.md).
- [IT Elements 2025](https://github.com/myskat90/vllm-habr/tree/main/IT%20Elements%202025) и авторский OpenStack Demo-cloud (`cloud-demo/openstack`, версия `85cd089`) — предыдущие мастер-классы, использованные как образцы подачи.
- Подготовленный стенд `hardfest-demo` — версии и размеры файлов моделей, исходные профили запуска. Адреса площадки и учётные данные в публичный проект не включены.
