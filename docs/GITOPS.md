# Два кластера и GitOps

Рабочий путь:

```text
GitLab: k8s-config
        ↓ commit / push
Argo CD в управляющем кластере
        ↓ Application.spec.destination
GPU-кластер: DRA → Pod vLLM → Service
        ↑
Open WebUI → Bifrost
```

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

В `resources.yaml` задайте ноду, DeviceClass, PVC и путь весов вместо `REPLACE_...`.
В `argo-app/*.yaml` задайте свой repoURL, ветку, path и destination.
Для H100 A/B используются **один и тот же класс физических GPU** и одна нода;
две DRA-заявки выделяют разные карты. Не указывайте PCI-адрес вручную.

Просмотрите изменения до отправки:

```bash
git diff -- "$DEMO_DIR"
kubectl kustomize "$DEMO_DIR/gemma-a"
kubectl kustomize "$DEMO_DIR/gemma-b"
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -k "$DEMO_DIR/gemma-a"
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -k "$DEMO_DIR/gemma-b"
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

Используется `yq` Mike Farah v4. Изменение replicas хранится в Kustomization,
а не выполняется командой scale мимо Git:

```bash
yq -i '.replicas = [{"name": "hf-gemma-a", "count": 1}]' "$DEMO_DIR/gemma-a/kustomization.yaml"
yq -i '.replicas = [{"name": "hf-gemma-b", "count": 1}]' "$DEMO_DIR/gemma-b/kustomization.yaml"
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/gemma-a/kustomization.yaml" "$DEMO_DIR/gemma-b/kustomization.yaml"
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

Правьте `profile.yaml`, проверяйте `kubectl kustomize`, commit/push и sync того же
Application. Kustomize генерирует новое имя ConfigMap; ссылка в Deployment меняется,
поэтому Pod перезапускается с новым профилем. Стратегия Recreate освобождает его GPU
перед запуском замены. Само редактирование Git ещё не означает успешный старт.

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
