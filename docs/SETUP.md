# Подготовка стенда

Результат подготовки — работающая **Gemma A — Base**, три сервиса A30,
личный доступ в чат и метрики. B, платформенная Gemma и Qwen подготовлены,
но выключены. Далее начинается [основной маршрут](../README.md#ab).
Для двух RTX используется [вариант подготовки ниже](#rtx); H100-разделы
не нужно выполнять перед ним.

![Стенд: запросы, запуск и наблюдение](../assets/01-topology.svg)

| Компонент | За что отвечает |
| --- | --- |
| Open WebUI | Чат, документы и голосовой ввод |
| webui-access | Связывает одобренного пользователя с личным ключом шлюза |
| Bifrost | Выбирает маршрут модели, применяет доступы и учитывает запросы |
| vLLM | Сервер, который загружает модель и обрабатывает запросы |
| ai-models | Импортирует веса — файлы с параметрами модели — и доставляет их на ноду |
| GitLab → Argo CD | Хранит и применяет конфигурацию |
| Prometheus → Console | Собирает метрики и показывает состояние сервисов |

## 1. Проверить доступ и ресурсы

Нужны Git, Helm 3+, kubectl и curl; соседние клоны публичного
`hardfest-gpu-workshop` и частного `k8s-config`.
В кластерах заранее созданы namespace, установлен драйвер и DRA для выделения GPU с контроллером
GPUClass/GPUPool. Argo имеет доступ к GitLab и целевым кластерам.

Из корня **частного `k8s-config`**, с именами своих контекстов:

```bash
export ARGO_CONTEXT=management
export GPU_CONTEXT=gpu-cluster
export MIG_CONTEXT=a30-cluster
export A30_DIR=argo-projects/a30-cluster/hardfest-demo
export ARGO_NAMESPACE=argocd
export DEMO_DIR=argo-projects/gpu-cluster/hardfest-demo
export WORKSHOP_BRANCH=hardfest-demo
set -o pipefail

kubectl config get-contexts
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications
kubectl --context "$GPU_CONTEXT" get nodes,deviceclasses
kubectl --context "$MIG_CONTEXT" get nodes,deviceclasses
```

`ARGO_CONTEXT` управляет Argo, `GPU_CONTEXT` — моделями H100,
`MIG_CONTEXT` — A30. Имена контекстов не обязаны совпадать с именами
кластеров в Argo. Все проверки должны завершиться без ошибок доступа.

Две H100 должны находиться на одной ноде: сначала Gemma, затем Qwen TP2.
Замените `YOUR_GPU_NODE`:

```bash
kubectl --context "$GPU_CONTEXT" describe node YOUR_GPU_NODE
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,pvc,resourceclaims
kubectl --context "$GPU_CONTEXT" get deviceclasses
```

До запуска нужны Ready-нода без cordon, свободные целевые GPU,
сгенерированный DeviceClass, видимость обеих карт в гостевой ОС,
проверенный обмен и отсутствие новых неисправимых ECC.
[Проверка GPU](TROUBLESHOOTING.md).

**При ошибках GPU запуск не начинается. Драйвер, VFIO, MIG mode
и чужие блокировки в рамках руководства не меняются.**

До любых commit/push выполните [bootstrap ветки GitOps](GITOPS.md#bootstrap)
и вернитесь сюда. Он выбирает существующую ветку либо создаёт новую,
публикует её и задаёт upstream. Так первый импорт моделей уже получает
существующую `targetRevision`.

### Память определяет порядок запуска

| Профиль | RAM request / limit | RAM для KV |
| --- | --- | --- |
| A | 24 / 48 GiB | Нет |
| B Cache и Tune | 56 / 80 GiB | 32 GiB |
| Qwen TP2 | 80 / 104 GiB | 16 GiB |

На **128 GiB** A и B запускаются последовательно; перед платформенной Gemma
останавливается также ручная B. От **192 GiB** возможны две настроенные Gemma,
если хватает Allocatable после остальных потребителей.
Qwen занимает обе карты только после остановки Gemma.
[Расчёт RAM](MEMORY_BUDGET.md).

Для A30 заранее включён MIG mode. Нужны две `2g.12gb`: эмбеддер и реранкер
делят одну через MPS, Whisper занимает другую.
Создание частей требует свободных ресурсов и поддержки драйвера;
**чужие разделы не удаляются, DeviceClass вручную не создаётся**.

## 2. Доставить веса и runtime

[Импорт ai-models](../catalog/README.md) использует закреплённые ревизии.
Веса Gemma, assistant и Qwen занимают около **183 GiB**; дополнительно нужны
место под артефакты, временные файлы и доставку. Квота не заменяет свободный диск.

`Model` — объект с источником и ревизией весов. После импорта нужны `Ready`,
ненулевой размер и digest. При размещении потребителя ai-models доставляет
файлы в **NodeCache — дисковый кеш весов на ноде** и подключает их к Pod.
Доставка и пути проверяются при первом запуске; `Model Ready` их не гарантирует.

Ручной профиль **values** задаёт параметры vLLM и ресурсы; отдельный site-файл
привязывает их к ноде, GPU и моделям. В его `modelRefs` перечислены модели
для доставки; вариант с assistant содержит обе. На A30 импортируется
`catalog/a30.yaml` по инструкции каталога.

Runtime ручных и платформенных запусков — совместимый **vLLM 0.31**,
образ закреплён по digest и доступен ноде.
У контейнерного образа свой кеш, отдельный от весов.
Для замеров заранее подготовлены CPU-нода, токенизатор и
[клиент Gemma](../examples/gemma-benchmark-job.yaml) /
[клиент Qwen](../examples/qwen-benchmark-job.yaml).

<a id="inference-readiness"></a>
## 3. Проверить рецепты AI Inference

AI Inference получает **InferenceService — заказ на запуск модели**:
какая модель нужна и какие ограничения у сервиса. Контроллер выбирает рецепт —
параметры запуска — и создаёт runtime. InferenceServiceClass задаёт допустимые
ресурсы и правила сервиса. Клиентские примеры не устанавливают сам модуль.

Подготовьте выключенные ручные A/B по [шагам 2–4 GitOps](GITOPS.md#2-подготовить-файлы-площадки)
и заказы H100/A30 по [шагу 7](GITOPS.md#7-подготовить-платформенные-заказы).
До освобождения работающих GPU
проверьте рецепт, класс сервиса и доступность модели:

| Заказ | Необходимая конфигурация |
| --- | --- |
| Gemma Tune | 1 H100, 128K, FP8 KV, prefix cache, prefill 2048, CUDA graphs, assistant, CPU KV 32 GiB |
| Qwen | 2 H100, 256K, MTP, native Simple CPU KV 16 GiB, runtime 0.31 |
| A30 | Qwen3 Embedding/Reranker 4B W4A16 и Whisper large-v3 |

В классе Qwen `acceleratorPolicy.maxAcceleratorCount ≥ 2`,
`scalingPolicy.minReplicas = maxReplicas = 1`.
Для H100 — `exposurePolicy.authentication: Token`; ключ хранится в менеджере секретов.
Не полагайтесь на имя `default-llm`: проверьте фактические ограничения класса.

Стратегия Throughput сама по себе не гарантирует нужный рецепт.
После запуска параметры процесса сверяются с планом;
меньшее окно или отсутствие assistant не заменяет Tune.

## 4. Подключить чат и наблюдение

Сначала настройте [маршруты и доступ](CHAT_AND_ACCESS.md): отдельные A/B,
без fallback и кеша готовых ответов. Адаптер личных ключей использует свою
ветку `k8s-config`. Завершив его настройку, вернитесь в ветку стенда:

```bash
git switch "$WORKSHOP_BRANCH" || exit 1
```

Теперь подключите [мониторинг](OBSERVABILITY.md), затем запустите A.
После запуска проверяются личный ключ запроса и метрики ручного сервиса;
платформенные сервисы появятся на том же дашборде при включении.

<a id="base-check"></a>
## 5. Запустить A и проверить ответ

Примените `replicaCount: 1` только у A по [процедуре GitOps](GITOPS.md#запуск-a).
После rollout проверьте GPU-заявку, mount ai-models и логи.
В отдельном терминале:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/hf-gemma-a 18001:8000
```

В основном терминале:

```bash
curl --fail http://127.0.0.1:18001/v1/models
curl --fail-with-body --max-time 180 http://127.0.0.1:18001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-31b","max_tokens":512,"temperature":0,"messages":[{"role":"user","content":"Объясни KV-кеш двумя предложениями."}]}'
```

Затем тот же запрос проверяется в WebUI одобренным аккаунтом;
port-forward закрывается. Dry-run и Model Ready не заменяют генерацию.

### Стенд готов, когда

- A отвечает в **Gemma A — Base**, вторая H100 свободна.
- B и платформенные Gemma/Qwen выключены; веса, assistant и рецепты готовы.
- Запрос пользователя учтён по личному ключу; метрики A увеличились.
- На A30 отвечают эмбеддер, реранкер и Whisper:
  [подготовка заказов](../README.md#a30-orders).
  Новая геометрия подтверждается снимками до/после подготовки,
  не повторным Sync уже готовых сервисов.

<a id="rtx"></a>
## RTX 5060 Ti: отличия подготовки

Начните здесь из частного `k8s-config`. Нужны Git, Helm 3+, kubectl, curl
и соседний клон `hardfest-gpu-workshop`. Две RTX 5060 Ti по 16 GiB находятся
на одной Ready-ноде. Нужны дисковый кеш весов NodeCache и RAM для двух Gemma
с лимитом **18 GiB каждая**, включая shm, плюс система. Namespace `hardfest-rtx`,
диски, драйвер, DRA и ai-models/AI Inference подготовлены заранее;
Argo имеет доступ к GitLab и целевому кластеру.

```bash
export GPU_CONTEXT=gpu-cluster ARGO_CONTEXT=management ARGO_NAMESPACE=argocd
export MIG_CONTEXT=a30-cluster
export A30_DIR=argo-projects/a30-cluster/hardfest-demo
export RTX_DIR=argo-projects/gpu-cluster/hardfest-rtx
export NS=hardfest-rtx
export WORKSHOP_BRANCH=hardfest-rtx
set -o pipefail

kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications
kubectl --context "$GPU_CONTEXT" get nodes,deviceclasses
kubectl --context "$GPU_CONTEXT" -n "$NS" get pods,pvc,resourceclaims
```

Выполните [bootstrap ветки](GITOPS.md#bootstrap) с этими переменными
и вернитесь сюда. Проверьте обе карты по [диагностике GPU](TROUBLESHOOTING.md).
При ошибках GPU запуск останавливается; драйверы, режимы карт и чужие
workloads в рамках подготовки не меняются.

Для **нового** `$RTX_DIR`:

```bash
test ! -e "$RTX_DIR" || exit 1
mkdir -p "$RTX_DIR/charts" "$RTX_DIR/values" "$RTX_DIR/catalog" "$RTX_DIR/platform" "$RTX_DIR/argo-app"
for CHART in vllm-runtime inference-service model-catalog; do
  cp -R "../hardfest-gpu-workshop/charts/$CHART" "$RTX_DIR/charts/" || exit 1
done
cp ../hardfest-gpu-workshop/values/rtx/gemma-*.yaml "$RTX_DIR/values/"
cp ../hardfest-gpu-workshop/catalog/rtx.yaml "$RTX_DIR/catalog/"
cp ../hardfest-gpu-workshop/platform/rtx-*.yaml "$RTX_DIR/platform/"
cp ../hardfest-gpu-workshop/argocd/rtx/*.yaml "$RTX_DIR/argo-app/"
```

Существующие привязки не перезаписываются. Заменяются `REPLACE_*`, Git-адрес,
ветка, destination, нода, DeviceClass и registry; токен Hugging Face доставляется
менеджером секретов.

Сначала все ручные профили имеют `replicaCount: 0`,
заказы — `order.enabled: false`. Проверьте каталог до импорта:

```bash
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
helm lint "$RTX_DIR/charts/model-catalog" --strict -f "$RTX_DIR/catalog/rtx.yaml" || exit 1
helm template rtx-models "$RTX_DIR/charts/model-catalog" -n "$NS" \
  -f "$RTX_DIR/catalog/rtx.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git add -- "$RTX_DIR"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Prepare RTX models and disabled services" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
```

Зарегистрируйте все RTX Applications в управляющем кластере. Если ими управляет
родительский Application, сначала синхронизируйте его вместо прямого `apply`:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server \
  -f "$RTX_DIR/argo-app/" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$RTX_DIR/argo-app/" || exit 1
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application rtx-models \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}" || exit 1
kubectl --context "$GPU_CONTEXT" -n "$NS" get models.ai.deckhouse.io -w
```

После Ready завершите наблюдение `Ctrl+C`. У всех трёх Model — объектов
с источником весов — проверьте ненулевой размер и digest по [каталогу](../catalog/README.md#3-проверить-и-запустить-импорт),
заменив namespace на `$NS`. Доставка файлов в NodeCache и mount в Pod
проверяются при запуске потребителя.

1. Проверка рецептов AI Inference по [объяснению выше](#inference-readiness): Gemma — 128K, assistant, FP8 KV, RAM KV 4 GiB;
   Qwen — 128K, TP2, MTP, FP8 KV, RAM KV 16 GiB, `max-num-seqs=8`.
   Класс разрешает соответственно одну/две GPU и одну реплику.
2. Подключение [маршрутов](CHAT_AND_ACCESS.md). Если инструкция адаптера
   переключила Git на его отдельную ветку, вернитесь командой
   `git switch "$WORKSHOP_BRANCH"` перед [мониторингом RTX](OBSERVABILITY.md)
   и дальнейшими изменениями профилей.
3. В ручном профиле `gemma-base.yaml` параметры vLLM заданы явно.
   Установите `replicaCount: 1`; Cache/Tune — 0,
   заказы — `order.enabled: false`. Проверка:

```bash
helm template rtx-gemma-base "$RTX_DIR/charts/vllm-runtime" -n "$NS" \
  -f "$RTX_DIR/values/gemma-base.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git add -- "$RTX_DIR/values/gemma-base.yaml"
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Start RTX Gemma Base" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application rtx-gemma-base \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}" || exit 1
kubectl --context "$GPU_CONTEXT" -n "$NS" rollout status deployment/rtx-gemma-base --timeout=40m
```

После Ready проверьте логи, mount весов, ответ API и ответ обычному пользователю
WebUI по [проверке A](#base-check), заменив namespace на `$NS`, Service на
`rtx-gemma-base`, а имя модели в запросе на `rtx-gemma-base`.

| В WebUI | Маршрут | Service:порт | Upstream |
| --- | --- | --- | --- |
| Gemma A — Base | `vllm/rtx-gemma-base` | `rtx-gemma-base:8000` | `rtx-gemma-base` |
| Gemma B — Cache | `vllm/rtx-gemma-cache` | `rtx-gemma-cache:8000` | `rtx-gemma-cache` |
| Gemma B — Tune | `vllm/rtx-gemma-tune` | `rtx-gemma-tune:8000` | `rtx-gemma-tune` |
| Gemma A — DP | `vllm/rtx-gemma-platform` | `rtx-gemma-platform:80` | `rtx-gemma-e2b` |
| Qwen 9B — TP2 | `vllm/rtx-qwen35-tp2` | `rtx-qwen35-tp2:80` | `rtx-qwen35-9b` |

Все Service находятся в `hardfest-rtx`; сначала доступна только Base.
Разрешения и тарифы учитывают upstream-имена; при переключении ключи и расход
сохраняются. Fallback и кеш ответов выключены, `enable_thinking` одинаков для A/B.

Для длинного чата оставляется до **8192 токенов ответа**:
вход с шаблоном плюс выход не превышает окно. `finish_reason: length` —
обрыв по лимиту, не законченный ответ. Base с окном 4K длинные настройки не получает.

A30 остаётся в соседнем кластере. После проверки A, свободной второй RTX
и метрик — [сценарий RTX](../RTX5060.md).
