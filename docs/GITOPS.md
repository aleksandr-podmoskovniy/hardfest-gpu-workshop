# Helm через GitLab и Argo CD

![Helm-чарт и values в GitLab; Argo CD управляющего кластера применяет ресурсы в GPU-кластер](../assets/12-gitops.svg)

В публичном репозитории — чарт и восемь профилей. В вашей репе k8s-config —
их закреплённая копия, параметры площадки и Application. Argo CD использует
Helm для рендеринга, а жизненным циклом ресурсов управляет сам.
Поверх этих ресурсов не выполняем `helm install/upgrade` или прямой `kubectl scale`.
[Как Argo CD работает с Helm](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/).

## 1. Подготовить контексты

Нужны Helm 3+, Git, kubectl, jq и yq Mike Farah v4. Команды выполняются из k8s-config;
публичный репозиторий расположен рядом. Подставьте свои значения:

```bash
export ARGO_CONTEXT=management
export GPU_CONTEXT=gpu-cluster
export ARGO_NAMESPACE=argocd
export DEMO_DIR=argo-projects/gpu-cluster/hardfest-demo
set -o pipefail

kubectl config get-contexts
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications
kubectl --context "$GPU_CONTEXT" get nodes,deviceclasses
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pvc
```

Namespace, PVC, целевой кластер и GitLab уже подготовлены и подключены к Argo.
Не добавляйте токены в repoURL. Project и destination должны разрешать выбранный кластер.

## 2. Скопировать чарт и профили

Для новой установки:

```bash
git switch -c hardfest-demo
mkdir -p "$DEMO_DIR/charts" "$DEMO_DIR/values" "$DEMO_DIR/site" "$DEMO_DIR/argo-app"
cp -R ../hardfest-gpu-workshop/charts/vllm-runtime "$DEMO_DIR/charts/"
cp ../hardfest-gpu-workshop/values/*.yaml "$DEMO_DIR/values/"
cp ../hardfest-gpu-workshop/examples/site-gemma.yaml "$DEMO_DIR/site/gemma.yaml"
cp ../hardfest-gpu-workshop/argocd/gemma-a.yaml "$DEMO_DIR/argo-app/"
cp ../hardfest-gpu-workshop/argocd/gemma-b.yaml "$DEMO_DIR/argo-app/"
```

Не копируйте поверх действующей установки без проверки diff:
[переход с прежних YAML](#миграция-с-прежних-yaml) описан отдельно.

В `site/gemma.yaml` замените ноду, созданный контроллером DeviceClass, PVC и subPath.
Добавьте точные tolerations и разрешения NetworkPolicy для Bifrost/мониторинга.
Не меняйте одинаковые для A/B веса, runtime и класс физических H100.

В `argo-app/*.yaml` задайте repoURL, ветку, project и destination.
`source.path` указывает на `$DEMO_DIR/charts/vllm-runtime`.
Пути valueFiles считаются от каталога чарта:

```yaml
helm:
  releaseName: hf-gemma-b
  valueFiles:
    - ../../values/gemma-b.yaml
    - ../../site/gemma.yaml
```

Порядок важен: **один полный профиль, затем привязки площадки**.
В site-файле не переопределяйте replicaCount, vllm, resources и shmSize — иначе
он перекроет переключение учебного профиля. Не наслаивайте B RAM на B assistant:
Helm объединяет словари и может сохранить ненужные флаги. Списки, включая
modelVolumes, заменяются целиком; для assistant в site-файле нужны обе модели.

Проверка до отправки:

```bash
for SLOT in a b; do
  helm lint "$DEMO_DIR/charts/vllm-runtime" --strict \
    -f "$DEMO_DIR/values/gemma-$SLOT.yaml" -f "$DEMO_DIR/site/gemma.yaml" || exit 1
  helm template "hf-gemma-$SLOT" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
    -f "$DEMO_DIR/values/gemma-$SLOT.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
    kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
done
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR"
git diff --cached --check
git diff --cached --stat
git commit -S -s -m "Add HardFest Helm chart and profiles"
git push -u origin hardfest-demo
```

`-S -s` использует ваш настроенный ключ и DCO. Проверяйте staged diff:
секреты в него не входят. Чарт создаёт пять ресурсов с нулём реплик,
не создаёт namespace, PVC, GPUClass или DeviceClass.

## 3. Зарегистрировать Application и выполнить sync

Application находится в управляющем кластере. В примерах нет autosync,
автоматического prune и каскадного finalizer:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server -f "$DEMO_DIR/argo-app/"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$DEMO_DIR/argo-app/"

REVISION=$(git rev-parse HEAD)
for APP in hardfest-gemma-a hardfest-gemma-b; do
  kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
    --type merge -p "$(jq -nc --arg rev "$REVISION" '{operation:{sync:{revision:$rev,prune:false}}}')"
done
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications hardfest-gemma-a hardfest-gemma-b
```

Первый sync не занимает GPU. Дождитесь завершения операции именно на отправленной
ревизии и посмотрите diff. Если Application управляется родительским GitOps-приложением,
меняйте его source через родителя, не создавайте второго владельца.

## 4. Включить реплики

```bash
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-a.yaml"
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-b.yaml"
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git commit -S -s -m "Start both Gemma replicas"
git push
```

Перед commit повторите lint и server dry-run из шага 2, затем sync из шага 3
с новым REVISION. Чарт откажется включать Pod с незаполненными REPLACE-параметрами.

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=80
```

Прежний Ready Pod не подтверждает новый sync. Сверьте ревизию, логи и ответ API.

## 5. Изменение профиля и откат

Настройки движка находятся в `vllm` файла values.
Чарт собирает ConfigMap и checksum из одних и тех же данных.
При изменении конфигурации Recreate останавливает прежний Pod перед запуском нового.

Один параметр:

```bash
yq -i '.vllm.max-model-len = 131072' "$DEMO_DIR/values/gemma-b.yaml"
```

Переход на полный профиль с KV в RAM, включая бюджеты памяти:

```bash
cp "$DEMO_DIR/values/gemma-b-ram.yaml" "$DEMO_DIR/values/gemma-b.yaml"
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-b.yaml"
helm template hf-gemma-b "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-b.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git diff -- "$DEMO_DIR/values/gemma-b.yaml"
git add -- "$DEMO_DIR/values/gemma-b.yaml"
git diff --cached --check
git commit -S -s -m "Enable Gemma B CPU KV cache"
git push
```

Синхронизируйте новый коммит. Application, Deployment и Service B остаются прежними.
Перед опытом с RAM на VM 128 GiB остановите A.
Для assistant отдельно подготовьте site-values с обоими PVC/mount; его профиль не содержит CPU KV.

При изменении спецификации DRA чарт сам меняет имя ResourceClaimTemplate и ссылку Pod.
Старая активная заявка не изменяется и не удаляется вручную. При prune=false старый
шаблон может остаться: удаляйте только подтверждённо неиспользуемый шаблон отдельным
согласованным действием, не включайте общий prune.

Откат:

```bash
git log --oneline -5 -- "$DEMO_DIR"
git revert --no-edit YOUR_PROFILE_COMMIT
git push
```

Синхронизируйте получившуюся ревизию. Helm rollback здесь не используется:
источник состояния — Git и Argo CD.

## Миграция с прежних YAML

1. Сохраните SHA прежней версии и параметры нод, PVC, DeviceClass, tolerations и NetworkPolicy.
2. Перенесите их в site-values. Сравните helm template с действующими ресурсами.
   Имена Deployment, Service, labels и selectors сохранены; Bifrost остаётся на прежних адресах.
3. Согласованно остановите A/B через старые YAML: replicas=0, commit/push/sync.
   Дождитесь освобождения Pod и DRA-заявок.
4. В том же Application замените directory-source на source.path чарта и helm.valueFiles.
   Поле directory удалите полностью; сохраните имя Application, project и destination.
5. Первый Helm-sync выполните при replicaCount=0, без prune и force.
   Чарт использует новый алгоритм имени DRA-шаблона; старые шаблоны не удалятся автоматически.
6. После проверки diff и ресурсов включайте A и B по одному через values.
   Старые исходные YAML уберите из активного пути GitOps отдельным коммитом;
   они остаются восстановимыми из истории Git.

Если эти workloads управляются Argo CD, не создавайте поверх них отдельный Helm release.

## 6. Секреты и публичная копия

В GitHub входят `charts/`, `values/`, обезличенные `argocd/`, документация, схемы и
обезличенные измерения. В GitLab — привязки стенда и те же профили.

Ключи Bifrost, пароли, cookies, kubeconfig, SSH-ключи, токены Hugging Face
не хранятся ни в одном из этих каталогов. Используйте принятый на площадке
External Secrets / SOPS / Sealed Secrets, либо создайте Secret из защищённого
локального файла. Base64 в обычном Secret YAML **не является шифрованием**.
Не выгружайте `kubectl get secret -o yaml` в репозиторий или результаты замеров.

Например, ограниченный Virtual Key сначала создаётся **в Bifrost**: случайная строка
из openssl не заменяет зарегистрированный ключ шлюза. Выгрузите только этот ключ
из менеджера секретов в защищённый файл вне репозитория. Импорт в namespace WebUI:

```bash
export WEBUI_CONTEXT=chat-cluster
export WEBUI_NAMESPACE=chat
export KEY_FILE=/secure/path/bifrost-workshop-key
test -s "$KEY_FILE"
chmod 600 "$KEY_FILE"
kubectl --context "$WEBUI_CONTEXT" -n "$WEBUI_NAMESPACE" \
  create secret generic hardfest-gemma-access \
  --from-file=api-key="$KEY_FILE" --dry-run=client -o yaml | \
kubectl --context "$WEBUI_CONTEXT" -n "$WEBUI_NAMESPACE" apply --server-side -f -
```

Здесь ключ не попадает в аргументы процесса, историю команд или YAML-файл в Git.
Не выполняйте блок с `set -x`, не добавляйте `tee` и не показывайте содержимое файла.
Сам Secret ещё не настраивает WebUI: подключение к Bifrost задаётся отдельно по
[схеме доступа](CHAT_AND_ACCESS.md). Для постоянной эксплуатации предпочтительнее
принятый на площадке контроллер секретов. Не создавайте два владельца одного Secret.

При переносе проверяйте конкретный diff; не выполняйте `git add .` в большой рабочей
репе. Для общедоступного репозитория предусмотрена отдельная проверка публичных файлов
в CI. Скриншоты также проверьте: адресная строка может содержать токен.
