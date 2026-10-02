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

При обновлении существующей установки сначала сравните diff и рендер:
имена Service, selectors и ссылки на PVC должны сохраняться.

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

## 4. Начать с базовой реплики

```bash
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-a.yaml"
yq -i '.replicaCount = 0' "$DEMO_DIR/values/gemma-b.yaml"
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git commit -S -s -m "Start Gemma baseline"
git push
```

Перед commit повторите lint и server dry-run из шага 2, затем sync из шага 3
с новым REVISION. Чарт откажется включать Pod с незаполненными REPLACE-параметрами.

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-a --tail=80
```

Прежний Ready Pod не подтверждает новый sync. Сверьте ревизию, логи и ответ API.
Дальше включайте B по [первой итерации](../README.md#ram), а затем переключайте
её на [вторую](../labs/03-speculation.md). На 128 GiB сначала выключайте A:
лимиты A + B с offload складываются в 128 GiB без запаса системе.

## 5. Изменение профиля и откат

Настройки движка находятся в `vllm` файла values.
Чарт собирает ConfigMap и checksum из одних и тех же данных.
При изменении конфигурации Recreate останавливает прежний Pod перед запуском нового.

Один параметр:

```bash
yq -i '.vllm.max-model-len = 131072' "$DEMO_DIR/values/gemma-b.yaml"
```

Переход на контрольный профиль 128K с KV в RAM, включая бюджеты памяти
(в основном `gemma-b.yaml` offload уже включён):

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
Для assistant отдельно подготовьте site-values с обоими PVC/mount.
Его профиль сохраняет CPU KV первой итерации; [переключение site-файла](../labs/03-speculation.md).

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

## 6. Секреты и публичная копия

В GitHub входят `charts/`, `values/`, обезличенные `argocd/`, документация, схемы и
обезличенные измерения. В GitLab — привязки стенда и те же профили.

Ключи Bifrost, пароли, cookies, kubeconfig, SSH-ключи, токены Hugging Face
не хранятся ни в одном из этих каталогов. Используйте принятый на площадке
External Secrets / SOPS / Sealed Secrets, либо создайте Secret из защищённого
локального файла. Base64 в обычном Secret YAML **не является шифрованием**.
Не выгружайте `kubectl get secret -o yaml` в репозиторий или результаты замеров.

Для адаптера доступа используйте отдельный Secret с четырьмя полями.
Выгрузите значения из менеджера секретов в защищённый каталог вне репозитория.
Management-ключ выпускается в Bifrost, ключ служебной учётки — в Open WebUI;
транспортный и JWT-секреты генерируются отдельно. Личные VK затем создаёт адаптер.

```bash
export WEBUI_CONTEXT=chat-cluster
export WEBUI_NAMESPACE=chat
export SECRET_DIR=/secure/path/webui-access
for FIELD in WEBUI_ADMIN_API_KEY BIFROST_MANAGEMENT_KEY WEBUI_TRANSPORT_KEY FORWARD_USER_INFO_HEADER_JWT_SECRET; do
  test -s "$SECRET_DIR/$FIELD" || exit 1
  chmod 600 "$SECRET_DIR/$FIELD"
done
set -o pipefail
kubectl --context "$WEBUI_CONTEXT" -n "$WEBUI_NAMESPACE" \
  create secret generic webui-access \
  --from-file=WEBUI_ADMIN_API_KEY="$SECRET_DIR/WEBUI_ADMIN_API_KEY" \
  --from-file=BIFROST_MANAGEMENT_KEY="$SECRET_DIR/BIFROST_MANAGEMENT_KEY" \
  --from-file=WEBUI_TRANSPORT_KEY="$SECRET_DIR/WEBUI_TRANSPORT_KEY" \
  --from-file=FORWARD_USER_INFO_HEADER_JWT_SECRET="$SECRET_DIR/FORWARD_USER_INFO_HEADER_JWT_SECRET" \
  --dry-run=client -o yaml |
kubectl --context "$WEBUI_CONTEXT" -n "$WEBUI_NAMESPACE" apply --server-side -f -
```

Ключи не попадают в аргументы процесса или YAML-файл в Git.
Не выполняйте блок с `set -x`, не добавляйте `tee` и не показывайте содержимое файлов.
Подключение WebUI и значения полей описаны в [интеграции](../integrations/webui-access/README.md).
Если Secret управляется контроллером секретов, настраивайте его через этот контроллер,
а не создавайте второго владельца командой выше.

При переносе проверяйте конкретный diff; не выполняйте `git add .` в большой рабочей
репе. Для общедоступного репозитория предусмотрена отдельная проверка публичных файлов
в CI. Скриншоты также проверьте: адресная строка может содержать токен.
