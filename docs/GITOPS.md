# Два кластера и GitOps

![GitLab, Argo CD управляющего кластера и обычные YAML в GPU-кластере](../assets/12-gitops.svg)

Application хранится в управляющем кластере, модель работает в целевом.
`kubectl apply -f argocd/` регистрирует приложения, а не разворачивает vLLM локальным скриптом.
Argo использует собственные учётные данные целевого кластера; kubeconfig пользователя
не попадает в Application или Git.

## 1. Подготовить контексты

Примерные имена замените своими. Настройки относятся к текущему терминалу:

```bash
export ARGO_CONTEXT=management
export GPU_CONTEXT=gpu-cluster
export ARGO_NAMESPACE=argocd
export DEMO_DIR=argo-projects/gpu-cluster/hardfest-demo

kubectl config get-contexts
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications
kubectl --context "$GPU_CONTEXT" get nodes
kubectl --context "$GPU_CONTEXT" get deviceclasses
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pvc
```

Целевой кластер и GitLab-репозиторий уже должны быть подключены к Argo.
Используйте существующие credentials, не добавляйте токены в repoURL.
В Application разрешённый project и destination должны соответствовать вашей площадке.

## 2. Перенести исходники в k8s-config

Если здесь уже лежит прежняя версия примеров, не смешивайте форматы:
в каталоге сервиса должны остаться пять файлов из новой структуры вместо
`resources.yaml`, отдельного `profile.yaml` и файла сборки. Сначала проверьте
diff и сохраните привязки ноды, PVC, DeviceClass и NetworkPolicy.
В Application задайте `source.directory.recurse: false`, как в новых примерах.
Проверяйте старые ConfigMap с хешем в имени отдельно: при `prune: false` Argo
их не удалит. Не включайте общий prune ради этой миграции.

Сначала получите публичные исходники и свою GitOps-репу.
Команды копирования выполняются **из k8s-config**, предполагая, что оба репозитория
находятся рядом. Не копируйте поверх существующего каталога без просмотра diff.

```bash
git switch -c hardfest-demo
mkdir -p "$DEMO_DIR/argo-app"
cp -R ../hardfest-gpu-workshop/deploy/gemma-a "$DEMO_DIR/"
cp -R ../hardfest-gpu-workshop/deploy/gemma-b "$DEMO_DIR/"
cp ../hardfest-gpu-workshop/argocd/gemma-a.yaml "$DEMO_DIR/argo-app/"
cp ../hardfest-gpu-workshop/argocd/gemma-b.yaml "$DEMO_DIR/argo-app/"
```

В `deployment.yaml` задайте ноду, PVC и путь весов вместо `REPLACE_...`;
в `resourceclaimtemplate.yaml` — DeviceClass.
В `argo-app/*.yaml` задайте свой repoURL, ветку, path и destination.
Для H100 A/B используются **один и тот же класс физических GPU** и одна нода;
две DRA-заявки выделяют разные карты. Не указывайте PCI-адрес вручную.

Просмотрите изменения до отправки:

```bash
git diff -- "$DEMO_DIR"
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f "$DEMO_DIR/gemma-a"
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f "$DEMO_DIR/gemma-b"
git add -- "$DEMO_DIR"
git diff --cached --check
git diff --cached --stat
git commit -S -s -m "Add HardFest Gemma A/B manifests"
git push -u origin hardfest-demo
```

`-S -s` использует **ваш** настроенный ключ и DCO. При отсутствии GPG сначала
настройте подпись по правилам своего репозитория; чужие ключи не копируйте.

## 3. Зарегистрировать Application и выполнить sync

В этих приложениях нет automated sync, prune и каскадного finalizer.
Namespace/PVC/GPUClass не входят в приложение и не удаляются вместе с ним.

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

Это штатная операция Argo CD через Kubernetes API; можно нажать Sync в UI.
Проверьте в UI **ревизию коммита**, целевой кластер и состав diff.
Первый sync создаёт ресурсы с нулём реплик и не занимает GPU.

## 4. Включить A и B через Git

Используется `yq` Mike Farah v4. Число реплик меняется в самом Deployment:

```bash
yq -i '.spec.replicas = 1' "$DEMO_DIR/gemma-a/deployment.yaml"
yq -i '.spec.replicas = 1' "$DEMO_DIR/gemma-b/deployment.yaml"
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/gemma-a/deployment.yaml" "$DEMO_DIR/gemma-b/deployment.yaml"
git commit -S -s -m "Start both Gemma replicas"
git push
```

Повторите sync из шага 3 с новым `REVISION=$(git rev-parse HEAD)`.
Проверка выполняется уже **в GPU-кластере**:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=80
```

Timeout — повод посмотреть Pod events и логи, не удалять PVC и не применять force.

## 5. Изменение профиля и откат

Параметры находятся в `configmap.yaml`, внутри `data.profile.yaml`.
У ConfigMap постоянное имя; изменение данных само по себе не перезапускает vLLM.
В том же коммите обновляйте checksum в шаблоне Pod:

```bash
export CONFIG_SHA=$(yq -o=json '.' "$DEMO_DIR/gemma-b/configmap.yaml" | jq -j '.data["profile.yaml"]' | shasum -a 256 | awk '{print $1}')
yq -i '.spec.template.metadata.annotations."checksum/vllm-config" = strenv(CONFIG_SHA)' \
  "$DEMO_DIR/gemma-b/deployment.yaml"

kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f "$DEMO_DIR/gemma-b"
git diff -- "$DEMO_DIR/gemma-b"
git add -- "$DEMO_DIR/gemma-b/configmap.yaml" "$DEMO_DIR/gemma-b/deployment.yaml"
git diff --cached --check
git commit -S -s -m "Update Gemma B configuration"
git push
```

После sync нового коммита Recreate останавливает прежний Pod и освобождает GPU
перед запуском замены. Сначала проверьте завершение операции Argo на нужной
ревизии, затем rollout и API. Прежний Ready Pod не подтверждает новый rollout.

Каталоги B 128K, B RAM и B speculative — альтернативы **того же** сервиса.
Переносите только нужные параметры и RAM/shm-настройки в существующий каталог B,
сохраняя привязки площадки. Не создавайте конкурирующие Application.


Откат — новый коммит, отменяющий **ваш конкретный** коммит конфигурации:

```bash
git log --oneline -5 -- "$DEMO_DIR"
# Подставьте SHA своего изменения:
git revert --no-edit YOUR_PROFILE_COMMIT
git push
```

Выполните sync получившейся ревизии и проверьте API. Не используйте hard reset или
принудительную отправку истории общей GitOps-репы.

## 6. Секреты и публичная копия

В GitHub входят только `deploy/`, обезличенные `argocd/`, документация, схемы и
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
