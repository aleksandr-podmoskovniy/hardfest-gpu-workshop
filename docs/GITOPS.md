# GitOps: применить, проверить и откатить конфигурацию

Источник состояния — частный `k8s-config`. В нём лежат Helm-чарт,
профиль модели и привязки площадки. Argo CD применяет выбранный commit
в целевой кластер; отдельного Helm release здесь нет.

![GitLab хранит конфигурацию; Argo CD применяет её в GPU-кластер](../assets/12-gitops.svg)

**Не используйте `helm upgrade`, `kubectl scale` или ручную правку Deployment
поверх Argo.** Runtime меняется только через Git.
Общий порядок подготовки начинается в [SETUP](SETUP.md): сначала доступы
и ветка, затем модели и выключенные сервисы.

<a id="bootstrap"></a>
## 1. Подготовить ветку

Команды выполняются из **частного `k8s-config`**.
Рядом расположен клон `hardfest-gpu-workshop`. Контексты, каталоги и
`WORKSHOP_BRANCH` заданы в [SETUP](SETUP.md#1-проверить-доступ-и-ресурсы)
или [подготовке RTX](SETUP.md#rtx). Доступ GitLab настраивается в Argo,
не токеном внутри `repoURL`.

Ветка выбирается **до первого импорта моделей**. Для нового стенда она
создаётся от текущего согласованного commit `k8s-config`; для существующего
используется локальная либо удалённая ветка площадки. Сначала сохраните
незавершённые изменения: блок ниже требует чистого рабочего дерева.

```bash
: "${WORKSHOP_BRANCH:?Задайте ветку площадки из SETUP}"
git status --short
test -z "$(git status --porcelain)" || exit 1
git fetch origin || exit 1
if git show-ref --verify --quiet "refs/heads/$WORKSHOP_BRANCH"; then
  git switch "$WORKSHOP_BRANCH" || exit 1
elif git show-ref --verify --quiet "refs/remotes/origin/$WORKSHOP_BRANCH"; then
  git switch --track -c "$WORKSHOP_BRANCH" "origin/$WORKSHOP_BRANCH" || exit 1
else
  git switch -c "$WORKSHOP_BRANCH" || exit 1
fi
git push --set-upstream origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
git branch -vv
```

Ожидается upstream `origin/$WORKSHOP_BRANCH`. Если push отклонён из-за
расхождения истории, сначала разберите diff; force-push здесь не используется.
В каждом Application `targetRevision` должен совпадать с этой веткой.
После этого вернитесь к доставке весов в SETUP.

## 2. Подготовить файлы площадки

Только для **нового** каталога:

```bash
test ! -e "$DEMO_DIR/charts/vllm-runtime" || exit 1
test ! -e "$DEMO_DIR/site" || exit 1
mkdir -p "$DEMO_DIR/charts" "$DEMO_DIR/values" "$DEMO_DIR/site" "$DEMO_DIR/argo-app"
cp -R ../hardfest-gpu-workshop/charts/vllm-runtime "$DEMO_DIR/charts/"
cp ../hardfest-gpu-workshop/values/*.yaml "$DEMO_DIR/values/"
cp ../hardfest-gpu-workshop/examples/site-gemma-catalog.yaml "$DEMO_DIR/site/gemma.yaml"
cp ../hardfest-gpu-workshop/examples/site-gemma-assistant-catalog.yaml "$DEMO_DIR/site/gemma-assistant.yaml"
cp ../hardfest-gpu-workshop/argocd/gemma-a.yaml "$DEMO_DIR/argo-app/"
cp ../hardfest-gpu-workshop/argocd/gemma-b.yaml "$DEMO_DIR/argo-app/"
```

На существующем стенде переносится diff; собственный `site/` не перезаписывается.

| Файл | Его задача и необходимые значения |
| --- | --- |
| `values/gemma-a.yaml`, `gemma-b.yaml` | Ручной профиль: готовые параметры запуска vLLM, память, shm и число реплик |
| `site/gemma.yaml` | Нода, созданный контроллером DeviceClass, Model и путь весов |
| `site/gemma-assistant.yaml` | Те же привязки и оба `modelRefs` |
| `argo-app/*.yaml` | Git-адрес, ветка, проект, путь чарта, destination |

`destination.name` — зарегистрированное имя кластера Argo,
не обязательно kubectl-контекст. Проверьте tolerations и сетевой доступ
от шлюза и мониторинга.

Application использует **один профиль, затем один site-файл**:

```yaml
helm:
  releaseName: hf-gemma-b
  valueFiles:
    - ../../values/gemma-b.yaml
    - ../../site/gemma.yaml
```

Site перекрывает профиль: из параметров vLLM здесь меняются только пути
`model` и `speculative-config.model`, не настройки производительности.
Списки Helm заменяет целиком. При `modelRefs` веса доставляет ai-models,
ручной загрузчик и `modelVolumes` не нужны.

Пока `replicaCount: 0`; autosync и общий prune выключены.
[Каталог моделей](../catalog/README.md) — отдельный Application.

## 3. Проверить и отправить изменение

Для A заданы значения ниже. Для B Cache: `SLOT=b SITE=gemma APP=hardfest-gemma-b`;
для B Tune: `SLOT=b SITE=gemma-assistant APP=hardfest-gemma-b`.

**Каждый следующий этап выполняется только после успеха предыдущего.**
До commit просматривается полный staged diff: без токенов и открытых Secret.

```bash
export SLOT=a SITE=gemma APP=hardfest-gemma-a
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
helm lint "$DEMO_DIR/charts/vllm-runtime" --strict \
  -f "$DEMO_DIR/values/gemma-$SLOT.yaml" -f "$DEMO_DIR/site/$SITE.yaml" || exit 1
helm template "hf-gemma-$SLOT" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-$SLOT.yaml" -f "$DEMO_DIR/site/$SITE.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Update HardFest configuration" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
```

Dry-run проверяет схему, не веса и генерацию.

<a id="цикл-изменения"></a>
## 4. Зарегистрировать и синхронизировать

Если Applications управляются родительским Application, сначала выполните его Sync.
Иначе при создании или изменении описаний примените их **в управляющем кластере**:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server \
  -f "$DEMO_DIR/argo-app/gemma-a.yaml" -f "$DEMO_DIR/argo-app/gemma-b.yaml" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/gemma-a.yaml" -f "$DEMO_DIR/argo-app/gemma-b.yaml"
```

Затем Sync приложения `APP`, выбранного в шаге 3:

```bash
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application "$APP" \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,OPERATION:.status.operationState.phase,REVISION:.status.sync.revision'
```

Ожидаются `Synced`, `Healthy`, `Succeeded` и тот же `REVISION`.
Новая Sync-операция не запускается до завершения предыдущей.
Для первого Sync B задайте `APP=hardfest-gemma-b` и повторите только блок Sync
с тем же commit. Оба профиля с нулём реплик GPU не занимают.

<a id="запуск-a"></a>
## 5. Запустить A

В `values/gemma-a.yaml`: `replicaCount: 1`; B остаётся выключенной.
Повторите шаги 3–4 с новым commit, затем:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=40m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-a --tail=80
```

Нужны загруженные веса и [реальный ответ API/WebUI](SETUP.md#base-check),
а не старый Ready Pod от предыдущего commit.

## 6. Переключить B

**До замены завершаются запросы и через Git/Argo останавливается текущая B.**
На узле 128 GiB также сначала останавливается A; продолжение — после удаления Pod,
не сразу после изменения файла.

Профиль заменяется **целиком**, Service и Application сохраняются:

```bash
cp "$DEMO_DIR/values/gemma-b-spec.yaml" "$DEMO_DIR/values/gemma-b.yaml"
```

В новом `gemma-b.yaml` задаётся `replicaCount: 1`, а в Application B второй
`valueFiles` меняется на `../../site/gemma-assistant.yaml`.
Он подключает основную модель и assistant. Затем шаги 3–4 с `SITE=gemma-assistant`.

Чарт обновляет ConfigMap/checksum и перезапускает Pod через `Recreate`.
При изменении DRA появляется новый ResourceClaimTemplate:
активные заявки вручную не удаляются, старый шаблон очищается только после проверки ссылок.

## 7. Подготовить платформенные заказы

Платформенный заказ **InferenceService** описывает модель и нужный сервис;
AI Inference выбирает рецепт и создаёт runtime. Чарт `inference-service`
создаёт только этот заказ; Model и классы должны существовать.

Для нового каталога H100:

```bash
mkdir -p "$DEMO_DIR/platform"
test ! -e "$DEMO_DIR/charts/inference-service" || exit 1
cp -R ../hardfest-gpu-workshop/charts/inference-service "$DEMO_DIR/charts/"
cp ../hardfest-gpu-workshop/platform/gemma.yaml "$DEMO_DIR/platform/"
cp ../hardfest-gpu-workshop/platform/qwen.yaml "$DEMO_DIR/platform/"
cp ../hardfest-gpu-workshop/argocd/gemma-platform.yaml "$DEMO_DIR/argo-app/"
cp ../hardfest-gpu-workshop/argocd/qwen-platform.yaml "$DEMO_DIR/argo-app/"
```

Для нового каталога A30:

```bash
mkdir -p "$A30_DIR/charts" "$A30_DIR/platform" "$A30_DIR/argo-app"
test ! -e "$A30_DIR/charts/inference-service" || exit 1
cp -R ../hardfest-gpu-workshop/charts/inference-service "$A30_DIR/charts/"
for SERVICE in embedding reranker whisper; do
  cp "../hardfest-gpu-workshop/platform/$SERVICE.yaml" "$A30_DIR/platform/"
  cp "../hardfest-gpu-workshop/argocd/$SERVICE-platform.yaml" "$A30_DIR/argo-app/"
done
```

В Applications заполняются Git-адрес, ветка, проект и destination;
`source.path` заканчивается на `charts/inference-service`,
`valueFiles` содержит только `../../platform/ИМЯ.yaml`.
В values — существующие Model, InferenceServiceClass и DeviceClass.

Заказы пока `order.enabled: false`. Выключенный заказ рендерит
пустой документ; для проверки полей **без запуска** включите его только в
локальном рендере:

```bash
helm lint "$DEMO_DIR/charts/inference-service" --strict -f "$DEMO_DIR/platform/gemma.yaml" \
  --set order.enabled=true || exit 1
helm template hf-platform-gemma "$DEMO_DIR/charts/inference-service" \
  -n hardfest-demo -f "$DEMO_DIR/platform/gemma.yaml" --set order.enabled=true |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
```

Далее commit/push файлов чарта, `platform/` и соответствующих
`argo-app/*-platform.yaml`. По шагу 4 зарегистрируйте именно эти новые
Applications и синхронизируйте каждое с конкретным SHA:
`hardfest-gemma-platform`, `hardfest-qwen-platform` или отдельного сервиса A30.
Для A30 в командах `DEMO_DIR` заменяется на `A30_DIR`, `GPU_CONTEXT` —
на `MIG_CONTEXT`, `gemma` — на нужный сервис; Argo-контекст сохраняется.
Рецепт выбирает платформа; поля `recipeName` в заказ не добавляются.

## Откат

```bash
git log --oneline -5 -- "$DEMO_DIR"
git revert -S -s --no-edit YOUR_PROFILE_COMMIT || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
```

Синхронизируйте новый commit, не используйте `helm rollback`.
Модели и PVC сохраняются.

## Остановка

1. Отключите маршрут и завершите запросы.
2. Ручной профиль: `replicaCount: 0`, commit/push и Sync.
3. Платформенный заказ: `order.enabled: false`, commit/push.
   При `prune: false` обычный Sync **не удалит** InferenceService.
4. В отдельном Application обновите diff до этого commit:
   на удаление должен быть только нужный InferenceService.
5. Выполните **выборочный Sync этого ресурса с Prune**; дождитесь удаления
   заказа и его Pod, освобождения ResourceClaim и GPU.

**Без Force, Replace и общего prune.** Если diff содержит Model, PVC
или чужие ресурсы, остановитесь и проверьте ownership.
Удаление Application без finalizer не заменяет остановку нагрузки.
Namespace, диски, модели и GPUClass/GPUPool сохраняются.

## Секреты

Публичный Git хранит шаблоны, частный — привязки `site/`.
Токены, пароли и kubeconfig доставляются менеджером секретов:
External Secrets, SOPS или Sealed Secrets. Base64 не является шифрованием.

Вывод `kubectl get secret -o yaml` и скриншоты с токенами не попадают в Git
или результаты замеров. [Secret адаптера WebUI](../integrations/webui-access/README.md).
