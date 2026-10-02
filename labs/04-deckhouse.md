# Запустить Gemma через AI Inference

Перенесём проверенную конфигурацию второй B в сервис платформы.
Рецепт задаёт runtime, параметры движка и бюджет ресурсов; AI Inference
планирует размещение и создаёт workload. Веса берём из ai-models.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; обе ручные итерации и их результаты сохранены.
- [ ] В [ai-models](../catalog/README.md) готовы Gemma и assistant нужных ревизий.
- [ ] Установленная ветка AI Inference содержит рецепты из таблицы ниже.
- [ ] Выбран InferenceServiceClass и существующий DeviceClass целой H100.
- [ ] Application `hardfest-platform` подготовлен по [GitOps](../docs/GITOPS.md)
  и управляет только каталогом `$DEMO_DIR/platform`.

| Рецепт | Параметры для проверки |
| --- | --- |
| Gemma 64K с CPU KV | FP8 KV, prefix cache, CPU KV 32 GiB, prefill 4096, eager |
| Gemma 128K | FP8 KV, prefix cache, prefill 4096, CUDA graphs; без CPU KV |
| Gemma 128K с CPU KV | KV-offload и согласованные бюджеты RAM/shared memory |
| Gemma с assistant, 64K | Кэши первой итерации, prefill 2048, CUDA graphs, assistant; CPU KV 32 GiB |
| Qwen TP2 с MTP | Две H100, MTP, prefix cache, chunked prefill, CUDA graphs, KV-offload |

В этом упражнении нужен рецепт **Gemma с assistant, 64K** и стратегия
`Throughput`. Название рецепта на площадке может отличаться; сравнивайте
его содержимое с `values/gemma-b-spec.yaml`.

> [!IMPORTANT]
> Таблица описывает требуемую поставку, а не подтверждает наличие всех рецептов.
> В проверенной ветке рецепт Gemma пока не содержит assistant и CPU KV.
> Сначала выполните [проверку поставки](../docs/SETUP.md#inference-readiness).
> До её успешного завершения не останавливайте работающую ручную Gemma.

## 1. Проверить API и каталог

```bash
kubectl --context "$GPU_CONTEXT" api-resources --api-group=ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" explain inferenceservice.spec \
  --api-version=ai.deckhouse.io/v1alpha1
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" get inferenceserviceclasses,deviceclasses
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
```

Выберите выделенный класс для сравнения и проверьте его до создания заказа:

```bash
export INFERENCE_CLASS=REPLACE_INFERENCE_SERVICE_CLASS
kubectl --context "$GPU_CONTEXT" get inferenceserviceclass "$INFERENCE_CLASS" -o yaml
```

В `spec.scalingPolicy` нужны `minReplicas: 1` и `maxReplicas: 1`,
в `spec.acceleratorPolicy` — разрешённые `Dedicated` и `WholeDevice`.
Иначе автоматическое масштабирование или sharing изменят условия сравнения.
Класс не должен разрешать совместное использование тестируемой карты.
`exposurePolicy.authentication: Token` защищает сервисный API.
Если политика не подходит, подготовьте отдельный класс через GitOps;
не меняйте общий класс существующих сервисов.

Затем проверьте конфигурацию рецепта в исходниках пакета **до** освобождения GPU:
prefill 2048, CPU KV 32 GiB, assistant с доставкой и budgets из таблицы ниже.

Если API или рецепт не соответствует примеру, сначала приведите пакет
модуля в нужное состояние штатным способом поставки. Не подменяйте результат
платформенного запуска ручным Deployment.

## 2. Освободить GPU и RAM

Сохраните результаты обеих Gemma. В редакторе установите `replicaCount: 0`
в `$DEMO_DIR/values/gemma-a.yaml`. На узле 128 GiB сделайте то же в
`$DEMO_DIR/values/gemma-b.yaml`.

> [!WARNING]
> Платформенная Gemma с assistant требует RAM request/limit 56/80 GiB.
> Два таких сервиса на VM 128 GiB не оставляют безопасного бюджета.
> Для одновременного сравнения нужен более ёмкий узел.

```bash
git diff -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git add -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git diff --cached --check
git commit -S -s -m "Release resources for platform Gemma"
git push
REVISION=$(git rev-parse HEAD)
for APP in hardfest-gemma-a hardfest-gemma-b; do
  kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
    --type merge \
    --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
done
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
```

Дождитесь успешной синхронизации и освобождения заявок выключенных Pod.
Активные claims не удаляйте вручную.

Если B остаётся на второй карте большого узла, платформа получает свободную
карту A. Когда свободны обе, одного DeviceClass и количества `1` недостаточно
для выбора UUID: нужна поддерживаемая драйвером привязка и проверка выделения.

## 3. Создать заказ в platform/gemma.yaml

```bash
mkdir -p "$DEMO_DIR/platform"
export PLATFORM_APP=hardfest-platform
```

Создайте редактором файл `$DEMO_DIR/platform/gemma.yaml`:

```yaml
apiVersion: ai.deckhouse.io/v1alpha1
kind: InferenceService
metadata:
  name: hf-platform-gemma
  namespace: hardfest-demo
spec:
  inferenceServiceClassName: REPLACE_INFERENCE_SERVICE_CLASS
  launchStrategy: Throughput
  model:
    src: ai-models
    ref:
      kind: Model
      name: gemma-4-31b
  resources:
    accelerator:
      deviceClasses:
        - REPLACE_GENERATED_H100_DEVICECLASS
```

Замените имя класса сервиса проверенным `$INFERENCE_CLASS`,
DeviceClass — именем из шага 1.
Для общего каталога используйте фактический `ClusterModel`.
Рецепт выбирается по модели, оборудованию и стратегии: поля
`recipeName` в заказе нет.

```bash
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f "$DEMO_DIR/platform/gemma.yaml"
```

Только после успешной проверки:

```bash
git add -- "$DEMO_DIR/platform/gemma.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Launch Gemma through AI Inference"
git push
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$PLATFORM_APP" \
  --type merge \
  --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application "$PLATFORM_APP" \
  -o jsonpath='{.status.operationState.phase}{" "}{.status.sync.revision}{"\n"}'
```

## 4. Сверить план с ручной B

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get inferenceservice hf-platform-gemma -o yaml
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get statefulsets,deployments \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name'
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
```

Найдите workload с владельцем `hf-platform-gemma`. Задайте его тип и имя,
например `statefulset/имя-из-вывода`:

```bash
export GEMMA_WORKLOAD=REPLACE_KIND_AND_WORKLOAD_NAME
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get "$GEMMA_WORKLOAD" -o yaml
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status "$GEMMA_WORKLOAD" --timeout=40m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs "$GEMMA_WORKLOAD" --all-containers --tail=200
```

| Проверка в плане, Pod и логах | Требуемое значение |
| --- | --- |
| Веса / API-модель | Те же, что у ручной B |
| GPU / контекст | Одна H100 / 65 536 |
| KV / prefix cache | FP8 / включён |
| CPU KV / shm | 32 GiB / 40 GiB |
| RAM request / limit | 56 GiB / 80 GiB |
| Бюджет prefill / CUDA graphs | 2048 / включены |
| Assistant | Доставлен, смонтирован, загружен; MTP включён |

`Ready` сервиса не заменяет эту сверку. Рецепт отвечает также за доставку
assistant; его отсутствие нельзя исправлять ручным патчем дочернего StatefulSet.

## 5. Подключить API к прежнему чату

Найдите Service с владельцем заказа:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get services \
  -o custom-columns='NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name,PORT:.spec.ports[*].port'
```

Подключите этот Service отдельным маршрутом ai-mcp-gateway по
[инструкции доступа](../docs/CHAT_AND_ACCESS.md). Сохраните отдельные
имена ручной и платформенной Gemma: общий балансируемый маршрут смешает сравнение.
Выполните одинаковый учебный запрос через прежний Open WebUI.

## Проверка

- [ ] Заказ использует `model.src: ai-models`, а не новый URL загрузки.
- [ ] Argo применил нужный Git SHA; сервис и его workload готовы.
- [ ] План и реальный процесс совпадают со второй ручной B по таблице.
- [ ] API и прежний чат дают полный ответ.
- [ ] Повторена одинаковая нагрузка; результаты сохранены отдельно.
- [ ] Assistant подтверждён счётчиками принятия, offload — возвратом KV из RAM.

Ручной Application A не должен управлять дочерними объектами AI Inference.
У заказа один владелец: GitOps или Console, не оба сразу.

После [упражнения с A30](05-mig-mps.md) переходите к
[Qwen TP2](06-tp2.md): он заменит Gemma на обеих H100.
