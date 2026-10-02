# vLLM runtime

Один чарт для ручных запусков Gemma, эмбеддеров MIG/MPS и Qwen TP2.
Он создаёт ConfigMap, Deployment, ResourceClaimTemplate, Service и NetworkPolicy.
AI Inference использует собственный контроллер и рецепты: его дочерние ресурсы этот чарт не захватывает.

## Профили

| Values | Опыт |
| --- | --- |
| [gemma-a](../../values/gemma-a.yaml) | 64K, BF16 KV, без prefix cache и CUDA graphs, prefill 4096 |
| [gemma-b](../../values/gemma-b.yaml) | Итерация 1: 64K, FP8 KV, prefix cache, CPU KV 32 GiB, prefill 4096, eager |
| [gemma-b-128k](../../values/gemma-b-128k.yaml) | 128K без CPU KV |
| [gemma-b-ram](../../values/gemma-b-ram.yaml) | 128K и 32 GiB KV в RAM; request 56 GiB, limit 80 GiB, shm 40 GiB |
| [gemma-b-spec](../../values/gemma-b-spec.yaml) | Итерация 2: кэши первой, prefill 2048, CUDA graphs и Gemma assistant; 64K |
| [embed-mig](../../values/embed-mig.yaml) | Один MIG-раздел для эмбеддера |
| [embed-mps](../../values/embed-mps.yaml) | MPS поверх MIG: sharePercent 25, память 4 GiB |
| [qwen-tp2](../../values/qwen-tp2.yaml) | Ручной проверочный профиль Qwen на двух H100 |

Конфигурация не заменяет проверку ответа API на своей площадке.
[Опубликованный опыт KV-offload](../../results/kv-ram/README.md) содержит исходные измерения.

## Рендеринг

Из корня публичного репозитория, без подключения к кластеру:

```bash
helm lint charts/vllm-runtime --strict -f values/gemma-b.yaml
helm template hf-gemma-b charts/vllm-runtime -n hardfest-demo -f values/gemma-b.yaml
```

Для реального стенда добавьте после профиля `-f site/gemma.yaml`:
[пример привязки](../../examples/site-gemma.yaml). При нулевых репликах placeholders
разрешены для просмотра; перед запуском их нужно заменить.

Другие привязки: [Gemma assistant](../../examples/site-gemma-assistant.yaml),
[MIG](../../examples/site-embed-mig.yaml), [MPS](../../examples/site-embed-mps.yaml),
[Qwen TP2](../../examples/site-qwen-tp2.yaml). Копируйте их в `site/` своей GitLab-репы
и подставляйте только параметры нужной площадки.

## Контракт values

- `vllm` — параметры движка, без второго набора flags в Deployment.
- `replicaCount` — 0 или 1; по умолчанию 0.
- `image` — образ с digest, не плавающий tag.
- `dra` — существующий DeviceClass, количество, capacity и driver-specific config.
- `modelVolumes` — готовые PVC и пути весов; mount только read-only.
- `resources`, `shmSize` — согласованный бюджет процесса и CPU KV.
- `nodeSelector`, `tolerations`, `imagePullSecrets` — привязка к площадке.
- `networkPolicy.extraIngress` — дополнительные точечные разрешения Bifrost/мониторинга.

Чарт автоматически меняет checksum Pod при изменении vllm и имя DRA-шаблона при
изменении его спецификации. Recreate не требует свободной третьей GPU при замене Pod.
Имена и selectors A/B сохранены, варианты B заменяют **один** сервис.

Не складывайте несколько файлов B через `-f`: Helm объединяет словари.
Выбирайте один полный профиль, затем site-values. Список modelVolumes заменяется
целиком; для assistant укажите обе модели. CPU KV уже входит в request/limit,
а memory-backed shm расходует этот же лимит.

Namespace, PVC, Secret, GPUClass/GPUPool и DeviceClass чарт не создаёт.
Нет hooks, Ingress и привилегированных Pod. По умолчанию API доступен только Pod
своего namespace. Общий доступ открывается через Bifrost, не напрямую через vLLM.

## Доставка

[GitOps: копирование в GitLab, lint, dry-run, commit и Argo sync](../../docs/GITOPS.md).
Argo использует Helm для рендеринга; отдельного Helm release в кластере не появляется.
Не выполняйте helm upgrade/rollback поверх ресурсов Argo.

MIG/MPS и Qwen имеют отдельные Application в [argocd](../../argocd/embed-mig.yaml).
Пример Qwen предназначен для ручной проверки; финальный запуск мастер-класса —
через рецепт AI Inference.
