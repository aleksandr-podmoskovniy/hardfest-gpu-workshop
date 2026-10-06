# Заказ AI Inference через Helm

Чарт создаёт только `InferenceService` по схеме `ai.deckhouse.io/v1alpha1`.
Модель, InferenceServiceClass и DeviceClass уже должны существовать.
Deployment/StatefulSet, Service и claims создаёт контроллер платформы.

Публичные [values](../../platform/gemma.yaml) содержат `order.enabled: false`:
рендер пуст, GPU не выделяется. При включении заполните placeholders.

```bash
helm lint charts/inference-service --strict -f platform/gemma.yaml
helm template hf-platform-gemma charts/inference-service -n hardfest-demo -f platform/gemma.yaml
```

Примеры: [Gemma](../../platform/gemma.yaml), [Qwen](../../platform/qwen.yaml),
[эмбеддер](../../platform/embedding.yaml), [реранкер](../../platform/reranker.yaml),
[Whisper](../../platform/whisper.yaml). Это values, не манифесты для прямого apply.

`order.spec` передаётся в InferenceService без runtime flags. Не добавляйте
`recipeName` или параметры Deployment: рецепт выбирает платформа.
Проверяйте server-side dry-run по API установленного модуля.

Чарт не меняет классы, модели, аутентификацию, MIG или драйверы.
Отключение заказа убирает его из render; существующий объект требует
адресного Prune после просмотра diff. [GitOps и остановка](../../docs/GITOPS.md).
