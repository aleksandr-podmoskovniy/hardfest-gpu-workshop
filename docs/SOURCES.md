# Источники и происхождение

Основной источник истории стенда — подготовленный operational `hardfest-demo`: lock моделей, проверка materialization, candidate profiles и текущие ограничения. Его адреса, kubeconfig, приватный image mirror и raw cluster exports не входят в публичный проект. [Статус проверок](STATUS.md).

## Версионные первичные источники

- [vLLM 0.30.0: optimization](https://docs.vllm.ai/en/v0.30.0/configuration/optimization/) — scheduler, chunked prefill, memory tradeoffs.
- [vLLM 0.30.0: KV offloading](https://docs.vllm.ai/en/v0.30.0/features/kv_offloading_usage/) — connector, CPU budget и повторное использование.
- [vLLM 0.30.0: speculative decoding](https://docs.vllm.ai/en/v0.30.0/features/speculative_decoding/) — Gemma assistant через MTP.
- [vLLM 0.30.0: metrics](https://docs.vllm.ai/en/v0.30.0/usage/metrics/) — server timing metrics; фактический exporter остаётся источником имён/labels.
- [vLLM 0.30.0: bench serve](https://docs.vllm.ai/en/v0.30.0/cli/bench/serve/) — официальный нагрузочный клиент.
- [NVIDIA MIG deployment considerations](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html) — ограничения MIG/MPS; это не описание нашего DRA-контракта.
- [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/) — topology/telemetry и ограничения трактовки показаний.
- [Kubernetes DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) — claims, classes и жизненный цикл выделения.

Конкретные revisions моделей — [models.lock.json](../models.lock.json). Их repository names, артефакты и размеры перенесены из файлового аудита стенда; запуск и производительность требуют самостоятельной проверки. Lock не является рекомендацией автоматически перейти на newest/latest.

## Что взяли из прежних материалов

[IT Elements 2025](https://github.com/myskat90/vllm-habr/tree/main/IT%20Elements%202025): линейное повествование, команды, ожидаемый результат, иллюстрации. Старые версии runtime, values и credentials не перенесены.

Локальная презентация `HardFest-v1_210926.pptx`: название и логика «память → вычисления → размещение → автоматизация». Числовые расчёты для GPT-OSS и EAGLE3 не объявлены характеристиками Gemma/Qwen. Презентация не вендорская спецификация и не инструкция изменять инфраструктуру.
