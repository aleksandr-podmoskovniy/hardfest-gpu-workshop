# Запуски перенесены в Helm

Вместо восьми копий Kubernetes-манифестов используется один
[чарт vllm-runtime](../charts/vllm-runtime/README.md) и отдельные
[values-профили](../values/gemma-b.yaml).

[Перенос действующей GitOps-установки](../docs/GITOPS.md#миграция-с-прежних-yaml).
Старые YAML доступны в истории Git; они не являются вторым источником конфигурации.
