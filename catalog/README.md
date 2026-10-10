# Каталог моделей: импорт и доставка весов

ai-models импортирует закреплённые веса в `Model` — общий источник
для ручного runtime и AI Inference.

Нужны ai-models с хранилищем, namespace `hardfest-demo`, доступ и лицензии.
Команды — из частного `k8s-config`;
[контексты](../docs/GITOPS.md#1-задать-контексты).

**Веса H100 занимают около 183 GiB.** Проверьте свободный диск и квоту,
оставив место под артефакты, временную загрузку и доставку.

## 1. Подготовить источник

Для **нового** каталога:

```bash
mkdir -p "$DEMO_DIR/charts" "$DEMO_DIR/catalog" "$DEMO_DIR/argo-app"
cp -R ../hardfest-gpu-workshop/charts/model-catalog "$DEMO_DIR/charts/"
cp ../hardfest-gpu-workshop/catalog/models.yaml "$DEMO_DIR/catalog/models.yaml"
```

[Ревизии](models.yaml) закреплены в [lock-файле](../models.lock.json), не на `main`.
Для закрытой модели:

```yaml
models:
  - name: gemma-4-31b
    repository: google/gemma-4-31B-it
    revision: 842da3794eaa0b77d5f08bae87a17459d91ff475
    authSecretName: hf-model-read
```

Это одна запись. Secret `hf-model-read`, поле `token`, доставляется отдельно
в тот же namespace; токен не попадает в Git, values или аргументы команд.

## 2. Создать Application

В `$DEMO_DIR/argo-app/models.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: hardfest-models
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://gitlab.example.com/team/k8s-config.git
    targetRevision: hardfest-demo
    path: argo-projects/gpu-cluster/hardfest-demo/charts/model-catalog
    helm:
      releaseName: hf-models
      valueFiles:
        - ../../catalog/models.yaml
  destination:
    name: gpu-cluster
    namespace: hardfest-demo
  syncPolicy:
    syncOptions:
      - CreateNamespace=false
      - ServerSideApply=true
```

Замените Git-адрес, ветку, проект, путь, namespace Argo и destination.
`destination.name` — имя кластера в Argo.

**Sync запускает импорт сразу:** переключателя `replicaCount: 0` здесь нет.
Autosync выключен.

## 3. Проверить и запустить импорт

```bash
helm lint "$DEMO_DIR/charts/model-catalog" --strict \
  -f "$DEMO_DIR/catalog/models.yaml" || exit 1
helm template hf-models "$DEMO_DIR/charts/model-catalog" -n hardfest-demo \
  -f "$DEMO_DIR/catalog/models.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git add -- "$DEMO_DIR/charts/model-catalog" "$DEMO_DIR/catalog/models.yaml" "$DEMO_DIR/argo-app/models.yaml"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Add pinned model catalog" || exit 1
git push || exit 1
```

Регистрация — через родительский Application, либо, если его нет:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server -f "$DEMO_DIR/argo-app/models.yaml" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$DEMO_DIR/argo-app/models.yaml"
```

В обоих случаях импорт запускается адресным Sync:

```bash
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-models \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io -w
```

Завершите наблюдение `Ctrl+C`, когда модели готовы. При Failed:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model gemma-4-31b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model qwen3-8-flash-next-nvfp4
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io \
  -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,BYTES:.status.artifact.sizeBytes,DIGEST:.status.artifact.digest'
```

Нужны **Ready, ненулевой размер и digest**. Это готовность артефакта,
не проверка CUDA или ответа модели.

## 4. Подключить потребителя

### AI Inference

В заказе используется Model **того же namespace**:

```yaml
model:
  src: ai-models
  ref:
    kind: Model
    name: qwen3-8-flash-next-nvfp4
```

Для Gemma: `name: gemma-4-31b`.
Рецепт и GPU выбираются отдельно.
[Запуск Gemma](../README.md#platform), [Qwen TP2](../README.md#tp2).

### Ручной runtime

`modelRefs` в
[site-файле](../examples/site-gemma-catalog.yaml) или
[варианте с assistant](../examples/site-gemma-assistant-catalog.yaml)
формирует аннотацию **верхнего metadata Deployment**, не Pod template:

```yaml
metadata:
  annotations:
    ai.deckhouse.io/model: gemma-4-31b,gemma-4-31b-assistant
```

`modelVolumes` остаётся пустым. Пути vLLM:

| Model | Путь |
| --- | --- |
| Gemma | `/data/modelcache/models/gemma-4-31b` |
| Assistant | `/data/modelcache/models/gemma-4-31b-assistant` |

На назначенной ноде проверяются события доставки и доступность файлов.
`Model Ready` не означает прогретый NodeCache всех нод:
`artifact ... is not ready` — доставка весов, `Pulling image` — отдельный кеш образа.

## Другие площадки

| Площадка | Каталог и условия |
| --- | --- |
| A30 | [a30.yaml](a30.yaml), `$A30_DIR`, `$MIG_CONTEXT`, Application `hardfest-models-a30`, namespace `hardfest-demo` |
| RTX | [rtx.yaml](rtx.yaml), namespace `hardfest-rtx`; [порядок подготовки](../docs/SETUP.md#rtx) |

Для A30 повторяются шаги 1–3 с другим каталогом, `source.path` и destination:
импортируются Embedding 4B, Reranker 4B и Whisper large-v3.
В Console выбирается **кластер A30**, не H100.

RTX импортирует Gemma E2B, совместимый assistant и Qwen3.5-9B.
Model соседнего кластера или namespace не заменяет локальный.

## Смена ревизии и сохранность

`spec.source` существующего Model неизменяем: новая ревизия получает новое имя.
После Ready потребители переключаются через GitOps.

Чарт сохраняет Models при Argo prune и Helm uninstall, но не защищает
от явного удаления. **При остановке стенда модели и хранилище не удаляются.**
