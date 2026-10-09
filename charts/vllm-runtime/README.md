# vLLM runtime

Чарт для ручных запусков Gemma A, B Cache и B Tune.
Он создаёт ConfigMap, Deployment, ResourceClaimTemplate, Service и NetworkPolicy.
AI Inference использует собственный контроллер и рецепты: его дочерние ресурсы этот чарт не захватывает.

## Профили

| Values | Опыт |
| --- | --- |
| [gemma-a](../../values/gemma-a.yaml) | 16K, BF16 KV, без prefix cache, chunked prefill, graphs и offload |
| [gemma-b](../../values/gemma-b.yaml) | Итерация 1: 16K, FP8 KV, prefix cache, CPU KV 32 GiB, полный prefill |
| [gemma-b-spec](../../values/gemma-b-spec.yaml) | Итерация 2: 16K, кэши первой, prefill 2048, graphs и assistant |
| [qwen-tp2](../../values/qwen-tp2.yaml) | Ручной эталон параметров; основной запуск — AI Inference |

64K/128K проверяются изменением окна активной второй B, без смены остальных настроек.
Все ручные профили закреплены на vLLM 0.31.0.

Конфигурация не заменяет проверку ответа API на своей площадке.
[Опубликованный опыт KV-offload](../../results/kv-ram/README.md) содержит исходные измерения.

## Рендеринг

Из корня публичного репозитория, без подключения к кластеру:

```bash
helm lint charts/vllm-runtime --strict -f values/gemma-b.yaml
helm template hf-gemma-b charts/vllm-runtime -n hardfest-demo -f values/gemma-b.yaml
```

Для реального стенда добавьте после профиля `-f site/gemma.yaml`:
[пример ai-models](../../examples/site-gemma-catalog.yaml). При нулевых репликах placeholders
разрешены для просмотра; перед запуском их нужно заменить.

Для B Tune используйте [ai-models с assistant](../../examples/site-gemma-assistant-catalog.yaml):
эта привязка содержит обе модели. Финальный Qwen запускается через
[заказ AI Inference](../../platform/qwen.yaml), а не через этот чарт.

## Контракт values

- `vllm` — параметры движка, без второго набора flags в Deployment.
- `replicaCount` — 0 или 1; по умолчанию 0.
- `image` — образ с digest, не плавающий tag.
- `dra` — существующий DeviceClass, количество, capacity и driver-specific config.
- `dra.selectors` — необязательные CEL-фильтры устройств из этого класса, например по UUID для повторяемого теста. Имена атрибутов берутся из ResourceSlice установленного драйвера; отсутствующие атрибуты проверяйте через `has()`.
- `modelRefs` — Model в ai-models; аннотация ставится на верхнее metadata Deployment.
- `modelVolumes` — альтернативный источник: готовые PVC и read-only пути весов.
- Пути ai-models: `/data/modelcache/models/<Model>`; доступны после доставки на ноду.
- `resources`, `shmSize` — согласованный бюджет процесса и CPU KV.
- `nodeSelector`, `tolerations`, `imagePullSecrets` — привязка к площадке.
- `networkPolicy.extraIngress` — дополнительные точечные разрешения Bifrost/мониторинга.

Чарт автоматически меняет checksum Pod при изменении vllm и имя DRA-шаблона при
изменении его спецификации. Recreate не требует свободной третьей GPU при замене Pod.
Имена и selectors A/B сохранены, варианты B заменяют **один** сервис.

На холодном старте чтение весов и компиляция могут занимать больше 10 минут.
Startup probe допускает 30 минут, а Deployment — 40 минут с запасом на размещение
и загрузку образа. Это предельное ожидание, не обещанное время готовности.

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

Три сервиса A30 и финальный Qwen запускаются через [чарт заказа AI Inference](../inference-service/README.md).
Их workloads не принадлежат этому чарту.
