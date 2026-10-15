# Конфигурация стенда и управление запуском

На подготовленном стенде администратор меняет `spec.replicas` ручного Deployment
в Deckhouse: `1` запускает модель, `0` останавливает её. Argo autosync выключен,
а `/spec/replicas` исключён из сравнения. Параметры vLLM, образы и привязки
устройств по-прежнему хранятся в частном `k8s-config`.
Связь репозитория и кластеров показана на [схеме GitOps](../assets/12-gitops.svg).

| Изменение | Где выполнять |
| --- | --- |
| Запустить или остановить готовую ручную модель | Deployment в Deckhouse |
| Изменить профиль, образ, память или привязки | Git → проверка diff → ручной Sync Argo |
| Заказать модель через AI Inference | Создать InferenceService в Deckhouse по подготовленному примеру |
| Остановить созданный вручную InferenceService | Удалить именно этот заказ и проверить освобождение ресурсов |

При Sync конфигурации проверяйте, какое число реплик будет применено:
`ignoreDifferences` само по себе не гарантирует сохранение значения при Sync.
Нужен также `RespectIgnoreDifferences=true`; остальные правки Deployment
следующий ручной Sync заменит значениями из Git. Для нового или пересозданного
Deployment используется число реплик из Git, а не прежнее значение в Console.
Завершите текущие запросы и дождитесь освобождения GPU перед переключением.
Ниже — подготовка администратором; перед каждым запросом она не требуется.

<a id="bootstrap"></a>
## 1. Подготовить ветку

Команды выполняются из частного `k8s-config`; рядом лежит публичный репозиторий.
Переменные площадки заданы в [SETUP](SETUP.md#1-проверить-доступ-и-ресурсы).
Bootstrap нужен до первого импорта моделей и требует чистого рабочего дерева:

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

Upstream — `origin/$WORKSHOP_BRANCH`; `targetRevision` Applications — та же ветка.
При расхождении истории сначала разберите diff, без force-push.

## 2. Подготовить файлы площадки

Для нового каталога H100:

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
cp ../hardfest-gpu-workshop/argocd/gemma-cache.yaml "$DEMO_DIR/argo-app/"
```

На существующей площадке переносите diff, сохраняя её `site/`.
В Application задаются Git URL без токена, ветка, AppProject и целевой кластер.
Имя кластера Argo может отличаться от kubectl-контекста.

Подключайте один профиль, затем один site-файл:

```yaml
helm:
  releaseName: hf-gemma-b
  valueFiles:
    - ../../values/gemma-b-spec.yaml
    - ../../site/gemma-assistant.yaml
```

Site задаёт ноду, существующий DeviceClass, `modelRefs` и пути весов.
У Tune нужны основная модель и assistant. Списки Helm заменяет целиком.
Все профили поставляются с `replicaCount: 0`; autosync выключен.

| H100 Application | Deployment | Профиль |
| --- | --- | --- |
| `hardfest-gemma-a` | `hf-gemma-a` | `gemma-a.yaml` |
| `hardfest-gemma-cache` | `hf-gemma-cache` | `gemma-b.yaml`, имя переопределено Helm parameter |
| `hardfest-gemma-b` | `hf-gemma-b` | `gemma-b-spec.yaml` + assistant |

У всех трёх сохранены `ignoreDifferences` для `/spec/replicas`
и [RespectIgnoreDifferences=true](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-differences-configs).
Это сохраняет реплики существующего Deployment при Sync; новый создаётся с `0`.
Cache и Tune используют одну свободную карту
по очереди; их Pod не должны существовать одновременно.

**Обновление старой площадки:** если `hardfest-gemma-b` ещё запускает Cache,
сначала завершите запросы и остановите его. Затем перенесите новый diff,
проверьте три уникальных имени Deployment/Service/ResourceClaimTemplate и Sync.
Это подготовительная миграция, не действие в середине сравнения.

<a id="pro6000-bootstrap"></a>
### Отдельная площадка RTX PRO 6000

Не копируйте для неё values H100. После [bootstrap ветки](#bootstrap),
из частного `k8s-config` создайте новый каталог:

```bash
export PRO_DIR=argo-projects/gpu-cluster/hardfest-pro6000
test ! -e "$PRO_DIR" || exit 1
mkdir -p "$PRO_DIR/charts" "$PRO_DIR/values" "$PRO_DIR/site" "$PRO_DIR/argo-app" "$PRO_DIR/platform"
cp -R ../hardfest-gpu-workshop/charts/vllm-runtime "$PRO_DIR/charts/" || exit 1
cp ../hardfest-gpu-workshop/values/pro6000/*.yaml "$PRO_DIR/values/" || exit 1
cp ../hardfest-gpu-workshop/argocd/pro6000/*.yaml "$PRO_DIR/argo-app/" || exit 1
cp ../hardfest-gpu-workshop/platform/pro6000/*.yaml "$PRO_DIR/platform/" || exit 1
cp ../hardfest-gpu-workshop/examples/site-pro6000.yaml "$PRO_DIR/site/pro6000.yaml" || exit 1
```

В `site/pro6000.yaml` замените образ на доступный проверенный digest,
имя ноды и существующий DeviceClass. При необходимости задайте tolerations
и ingress шлюза/CPU-клиента. `modelRefs` и `vllm` остаются в каждом профиле:
общий site не должен удалить assistant у B или добавить её в A.

Во всех трёх Application замените Git URL, `targetRevision` на
`$WORKSHOP_BRANCH`, AppProject и имя целевого кластера. Пути уже соответствуют
этому каталогу. Сохраняются **replicaCount: 0**, autosync OFF,
`ignoreDifferences` для `/spec/replicas` и `RespectIgnoreDifferences=true`.

```bash
set -o pipefail
for PROFILE in gemma-a gemma-cache gemma-b; do
  helm lint "$PRO_DIR/charts/vllm-runtime" --strict \
    -f "$PRO_DIR/values/$PROFILE.yaml" -f "$PRO_DIR/site/pro6000.yaml" || exit 1
  helm template "selectel-$PROFILE" "$PRO_DIR/charts/vllm-runtime" -n hardfest-demo \
    -f "$PRO_DIR/values/$PROFILE.yaml" -f "$PRO_DIR/site/pro6000.yaml" |
    kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
done
```

После просмотра diff закоммитьте и отправьте **этот каталог**:

```bash
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
git add -- "$PRO_DIR"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Add PRO6000 workshop profiles" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server \
  -f "$PRO_DIR/argo-app/" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$PRO_DIR/argo-app/" || exit 1
```

В Argo вручную синхронизируются `hardfest-pro6000-gemma-a`,
`hardfest-pro6000-gemma-cache` и `hardfest-pro6000-gemma-b` на опубликованный SHA.
Если Applications принадлежат родительскому приложению, регистрируйте их через
него, не вторым владельцем. Во время показа меняются только реплики в Deckhouse.

Файлы `platform/` — **сырые YAML заказов для Console**, не Helm values;
эта процедура не применяет их. Создание заказа — отдельный этап после
освобождения GPU. [Нагрузочный клиент PRO 6000](../examples/pro6000-load-client/README.md).

## 3. Проверить и отправить изменение

Пример для A. Для Cache задайте `PROFILE=gemma-b SERVICE=hf-gemma-cache SITE=gemma APP=hardfest-gemma-cache`,
для Tune — `PROFILE=gemma-b-spec SERVICE=hf-gemma-b SITE=gemma-assistant APP=hardfest-gemma-b`.
Перед commit просмотрите staged diff; открытые Secret и токены в Git не попадают.

```bash
export PROFILE=gemma-a SERVICE=hf-gemma-a SITE=gemma APP=hardfest-gemma-a
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
helm lint "$DEMO_DIR/charts/vllm-runtime" --strict \
  -f "$DEMO_DIR/values/$PROFILE.yaml" -f "$DEMO_DIR/site/$SITE.yaml" \
  --set-string fullnameOverride="$SERVICE" || exit 1
helm template "$SERVICE" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/$PROFILE.yaml" -f "$DEMO_DIR/site/$SITE.yaml" \
  --set-string fullnameOverride="$SERVICE" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git add -- "$DEMO_DIR"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Update HardFest configuration" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
```

Dry-run проверяет схему, но не запуск CUDA или ответ модели.

<a id="цикл-изменения"></a>
## 4. Синхронизировать

Applications регистрируются в управляющем кластере. Если ими управляет
родительский Application, синхронизируйте его; иначе:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server \
  -f "$DEMO_DIR/argo-app/gemma-a.yaml" -f "$DEMO_DIR/argo-app/gemma-cache.yaml" -f "$DEMO_DIR/argo-app/gemma-b.yaml" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/gemma-a.yaml" -f "$DEMO_DIR/argo-app/gemma-cache.yaml" -f "$DEMO_DIR/argo-app/gemma-b.yaml" || exit 1
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application "$APP" \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,OPERATION:.status.operationState.phase,REVISION:.status.sync.revision'
```

Дождитесь окончания операции и сверьте SHA. Не запускайте следующую Sync-операцию
поверх текущей. Для Cache/Tune повторите Sync с `APP=hardfest-gemma-cache`
и `APP=hardfest-gemma-b`. На этом этапе все три Deployment имеют 0 реплик.

<a id="запуск-a"></a>
## 5. Запустить A

В подготовленной Console откройте Deployment A и задайте `spec.replicas: 1`.
B остаётся выключенной. Исходный `replicaCount: 0` в Git не меняется.
Старую площадку без исключения `/spec/replicas` сначала обновите по шагам 2–4.

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=40m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-a --tail=80
```

После загрузки проверьте [ответ API и чат](SETUP.md#base-check).

## 6. Переключить B

Cache и Tune подготовлены отдельными Deployment на всех трёх площадках.
Остановите текущий и включите следующий в Console.
Для H100 Tune используется `gemma-b-spec.yaml` и `site/gemma-assistant.yaml`.
На узле 128 GiB A останавливается до B; продолжайте после удаления её Pod.

## 7. Подготовить платформенные заказы

Участник создаёт InferenceService в Deckhouse с заранее заданным именем,
Model и классом. Примеры полей находятся в [platform](../platform/).
Имя заказа должно соответствовать подготовленному Service и маршруту;
после создания проверьте всю цепочку по [инструкции доступа](CHAT_AND_ACCESS.md).

Чарт `inference-service` остаётся способом проверить или поставлять заказ через
GitOps. Для локального рендера выключенного заказа, без изменения values:

```bash
helm lint "$DEMO_DIR/charts/inference-service" --strict -f "$DEMO_DIR/platform/gemma.yaml" \
  --set order.enabled=true || exit 1
helm template hf-platform-gemma "$DEMO_DIR/charts/inference-service" \
  -n hardfest-demo -f "$DEMO_DIR/platform/gemma.yaml" --set order.enabled=true |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
```

До выполнения скопируйте чарт и нужный пример в частный каталог, заполните
привязки площадки. Не создавайте один заказ одновременно вручную и через Argo.
Рецепт выбирает контроллер; поля `recipeName` в заказе нет.

**Одинаковое имя сохраняет маршрут чата, но не передаёт управление ресурсом.**
Для нового заказа из Console используйте согласованные имя и namespace;
не переносите из старого YAML метки и аннотации отслеживания Argo.
Если прежний заказ ещё числится в ресурсах Application, не включайте **Prune**
при Sync: при `order.enabled: false` Argo может удалить его. Выключенный
autosync запрещает автоматическое применение, но не ручное удаление через Prune.

## Откат

Для конфигурации: `git revert -S -s --no-edit YOUR_PROFILE_COMMIT`, push
и ручной Sync нового commit. Для числа реплик подготовленного ручного Deployment —
верните прежнее значение в Console. Отдельного Helm release и `helm rollback` здесь нет.

## Остановка

1. Закройте маршрут для новых запросов и дождитесь завершения текущих.
2. У ручного Deployment задайте `spec.replicas: 0` в Console.
3. У созданного вручную InferenceService удалите только нужный заказ.
   В живом `v1alpha1` нет `replicas` или `pause`; scale дочернего workload
   до нуля контроллер может отменить.
4. Дождитесь удаления Pod и освобождения ResourceClaim/GPU.

Если заказ принадлежит Argo, сначала выключите его в Git (`order.enabled: false`),
затем проверьте diff и выполните выборочный Prune только этого InferenceService.
Обычный Sync с `prune: false` заказ не удаляет.
Model, PVC, Namespace, GPUClass/GPUPool и инфраструктура сохраняются.

Секреты доставляются менеджером секретов. Ни открытый Secret, ни base64,
ни kubeconfig не публикуются в Git или результатах.
