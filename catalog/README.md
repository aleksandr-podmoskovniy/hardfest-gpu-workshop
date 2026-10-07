# Подготовка моделей в ai-models

ai-models загружает закреплённую ревизию весов и публикует проверенный артефакт.
На объект `Model` затем ссылаются потребители: ручной Deployment или AI Inference.
Новый способ запуска не требует заново выбирать репозиторий и ревизию модели.

## Перед началом

- Установлен ai-models и настроены хранилище артефактов и доставка.
- В GPU-кластере существует namespace `hardfest-demo`.
- Проверены лицензии моделей и доступ к их весам.
- Команды выполняются из `k8s-config`; переменные заданы по [GitOps](../docs/GITOPS.md#1-задать-контексты).

В [models.yaml](models.yaml) закреплены те же ревизии, что в
[models.lock.json](../models.lock.json): Gemma, её assistant и Qwen NVFP4.
Только веса занимают около **183 GiB**. Артефакты, временные загрузки и доставка
могут храниться отдельно и расходовать дополнительное место.

> [!IMPORTANT]
> Квота бакета не означает наличие свободного диска. До импорта проверьте оба
> ограничения и оставьте запас для доставки и уже работающих моделей.

## 1. Скопировать чарт и catalog/models.yaml

Для нового каталога в частной репе:

```bash
mkdir -p "$DEMO_DIR/charts" "$DEMO_DIR/catalog" "$DEMO_DIR/argo-app"
cp -R ../hardfest-gpu-workshop/charts/model-catalog "$DEMO_DIR/charts/"
cp ../hardfest-gpu-workshop/catalog/models.yaml "$DEMO_DIR/catalog/models.yaml"
```

Откройте `$DEMO_DIR/catalog/models.yaml`. Не заменяйте закреплённые ревизии на
`main`: у повторного запуска должны быть те же веса.

Если источник требует авторизацию, добавьте к нужной модели `authSecretName`:

```yaml
models:
  - name: gemma-4-31b
    repository: google/gemma-4-31B-it
    revision: 842da3794eaa0b77d5f08bae87a17459d91ff475
    authSecretName: hf-model-read
```

Это фрагмент одной записи, не замена всего списка. Secret `hf-model-read` с полем
`token` создаётся отдельно в `hardfest-demo` через менеджер секретов. Значение
токена не должно попадать в values, Git или аргументы команд.

## 2. Создать argo-app/models.yaml

Создайте файл `$DEMO_DIR/argo-app/models.yaml` в редакторе:

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

Замените `repoURL`, ветку, проект, путь и destination своими. Namespace Application
должен совпадать с `$ARGO_NAMESPACE`. `destination.name` — имя GPU-кластера в Argo.

> [!IMPORTANT]
> Sync этого Application запускает загрузку моделей. В отличие от runtime-чартов
> здесь нет переключателя `replicaCount: 0`. Autosync не включайте.

## 3. Проверить манифесты и отправить commit

```bash
helm lint "$DEMO_DIR/charts/model-catalog" --strict \
  -f "$DEMO_DIR/catalog/models.yaml"
helm template hf-models "$DEMO_DIR/charts/model-catalog" -n hardfest-demo \
  -f "$DEMO_DIR/catalog/models.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
git add -- "$DEMO_DIR/charts/model-catalog" "$DEMO_DIR/catalog/models.yaml" "$DEMO_DIR/argo-app/models.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Add pinned model catalog"
git push
```

Проверка должна показать три `Model` с закреплёнными URL. В diff не должно быть
токенов. Server dry-run проверяет API, но пока не скачивает веса.

## 4. Запустить импорт через Argo

Если Application управляется родительским GitOps-приложением, сначала доставьте
его через родителя. Иначе зарегистрируйте файл в управляющем кластере:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server -f "$DEMO_DIR/argo-app/models.yaml"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$DEMO_DIR/argo-app/models.yaml"
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-models \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io -w
```

Завершите наблюдение `Ctrl+C`, когда все три модели перешли в `Ready`.
При `Failed` остановитесь и посмотрите причину:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model gemma-4-31b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model qwen3-8-flash-next-nvfp4
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io \
  -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,BYTES:.status.artifact.sizeBytes,DIGEST:.status.artifact.digest'
```

У каждой модели должны быть фаза `Ready`, ненулевой размер и digest артефакта.
Это готовность весов; CUDA, память GPU и ответ движка проверяются отдельно.

## 5. Подключить подготовленную модель

### В AI Inference

Вместо повторного URL используйте ссылку на `Model` того же namespace:

```yaml
model:
  src: ai-models
  ref:
    kind: Model
    name: qwen3-8-flash-next-nvfp4
```

Для Gemma меняется только `name: gemma-4-31b`. Источник задаёт веса, но не заменяет
рецепт, DeviceClass и число GPU. Следующие шаги —
[Gemma через AI Inference](../README.md#platform) и [Qwen TP2](../README.md#tp2).

### В ручном Deployment

Модуль ai-models читает аннотацию **верхнего `metadata` Deployment**, не аннотацию
в `spec.template.metadata`:

```yaml
metadata:
  annotations:
    ai.deckhouse.io/model: gemma-4-31b,gemma-4-31b-assistant
```

После доставки веса доступны по путям:

| Модель | Путь внутри контейнера |
| --- | --- |
| Gemma | `/data/modelcache/models/gemma-4-31b` |
| Assistant | `/data/modelcache/models/gemma-4-31b-assistant` |

В чарте этот режим включается через `modelRefs`.
Используйте [site-gemma-catalog.yaml](../examples/site-gemma-catalog.yaml) и
[вариант с assistant](../examples/site-gemma-assistant-catalog.yaml).
`modelVolumes` оставьте пустым; пути в `vllm` должны совпасть с таблицей.
Чарт формирует аннотацию, вручную патчить Deployment не нужно.

Для каждого назначенного Pod проверьте события доставки. `Model Ready`
не означает, что NodeCache уже прогрет на всех нодах.
`MountVolume ... artifact ... is not ready` относится к доставке весов;
`Pulling image` — к контейнерному образу runtime. Это разные кеши.

## Каталог A30

Повторите шаги 1–4 в каталоге `$A30_DIR` и контексте `$MIG_CONTEXT`,
используя [a30.yaml](a30.yaml) вместо `models.yaml`.
Application назовите `hardfest-models-a30`, destination укажите кластер A30;
source.path должен вести к `$A30_DIR/charts/model-catalog`.
Не назначайте двум Applications одно имя или один каталог.

В этом каталоге три закреплённых модели: Qwen3 Embedding 4B W4A16,
Qwen3 Reranker 4B W4A16 и Whisper large-v3.
В Console откройте **кластер A30 → hardfest-demo → AI-модели**.
Каталог H100-кластера не показывает Models из соседнего кластера.
После импорта переходите к [трём сервисам](../README.md#placement).

## Каталог RTX 5060 Ti

Для двух RTX используйте [rtx.yaml](rtx.yaml) в namespace `hardfest-rtx`:
Gemma 4 E2B, её отдельный assistant и Qwen3.5-9B с нативным MTP.
Модель из другого namespace не заменяет локальный `Model` для заказа.
Команды, Argo Application и отдельные условия готовности доставки/assistant —
в [подготовке RTX](../docs/SETUP.md#rtx).
Теория и полный порядок опытов — в [сценарии RTX](../RTX5060.md).

## Обновление ревизии

`spec.source` существующего `Model` неизменяем. Для другой ревизии создайте новое
имя, дождитесь `Ready` и переключите потребителей через GitOps.
Чарт помечает модели как сохраняемые при Argo prune и Helm uninstall. Это не
защищает от явного удаления ресурса: не удаляйте модели и их хранилище при cleanup.
