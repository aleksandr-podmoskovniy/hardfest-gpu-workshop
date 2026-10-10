# Подготовка стенда

Результат подготовки — работающая **Gemma A — Base**, три сервиса A30,
личный доступ в чат и метрики. B, платформенная Gemma и Qwen подготовлены,
но выключены. Далее начинается [основной маршрут](../README.md#ab).

![Стенд: запросы, запуск и наблюдение](../assets/01-topology.svg)

| Компонент | За что отвечает |
| --- | --- |
| Open WebUI | Чат, документы и голосовой ввод |
| webui-access | Связывает одобренного пользователя с личным ключом шлюза |
| Bifrost | Выбирает маршрут модели, применяет доступы и учитывает запросы |
| vLLM | Выполняет модель; ручной профиль или AI Inference задаёт запуск |
| ai-models | Импортирует веса и доставляет их на ноду |
| GitLab → Argo CD | Хранит и применяет конфигурацию |
| Prometheus → Grafana | Собирает метрики и показывает состояние сервисов |

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

Проверяются Model Ready, ненулевой размер и digest, затем доставка на целевую
ноду и пути внутри Pod. Для ручного запуска используется `modelRefs`;
вариант с assistant содержит обе модели. На A30 импортируется `catalog/a30.yaml`.

Runtime ручных и платформенных запусков — совместимый **vLLM 0.31**,
образ закреплён по digest и доступен ноде.
NodeCache хранит веса, а контейнерный образ имеет отдельный кеш.
Для замеров заранее подготовлены CPU-нода, токенизатор и
[клиент Gemma](../examples/gemma-benchmark-job.yaml) /
[клиент Qwen](../examples/qwen-benchmark-job.yaml).

<a id="inference-readiness"></a>
## 3. Проверить рецепты AI Inference

Клиентские примеры не устанавливают модуль. До освобождения работающих GPU
проверьте рецепт, класс сервиса и доступность следующей модели.

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

| Подготовить | Проверить |
| --- | --- |
| [GitOps](GITOPS.md) | Applications зарегистрированы, B и заказы выключены |
| [Маршруты и доступ](CHAT_AND_ACCESS.md) | Отдельные A/B, без fallback и кеша готовых ответов |
| Личный ключ | После регистрации и одобрения запрос имеет владельца |
| [Мониторинг](OBSERVABILITY.md) | Видны ручные и платформенные сервисы, фильтры и единицы корректны |

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

Общие проверки выше сохраняются. Нужны две RTX 5060 Ti по 16 GiB одной ноды,
NodeCache и RAM для двух Gemma с лимитом **18 GiB каждая**, включая shm,
плюс система. Диски, DRA и модули настраиваются заранее.

```bash
export GPU_CONTEXT=gpu-cluster ARGO_CONTEXT=management ARGO_NAMESPACE=argocd
export MIG_CONTEXT=a30-cluster
export RTX_DIR=argo-projects/gpu-cluster/hardfest-rtx
export NS=hardfest-rtx
set -o pipefail
```

В **новый** `$RTX_DIR` копируются:

| Источник в публичной копии | Каталог назначения |
| --- | --- |
| `charts/vllm-runtime`, `inference-service`, `model-catalog` | `charts/` |
| `values/rtx/gemma-base.yaml`, `gemma-cache.yaml`, `gemma-spec.yaml` | `values/` |
| `catalog/rtx.yaml` | `catalog/` |
| `platform/rtx-gemma.yaml`, `rtx-qwen.yaml` | `platform/` |
| `argocd/rtx/*.yaml` | `argo-app/` |

Существующие привязки не перезаписываются. Заменяются `REPLACE_*`, Git-адрес,
ветка, destination, нода, DeviceClass и registry; токен Hugging Face доставляется
менеджером секретов.

1. Sync `rtx-models`: три Model Ready и готовая доставка на RTX.
2. Проверка рецептов: Gemma — 128K, assistant, FP8 KV, RAM KV 4 GiB;
   Qwen — 128K, TP2, MTP, FP8 KV, RAM KV 16 GiB, `max-num-seqs=8`.
   Класс разрешает соответственно одну/две GPU и одну реплику.
3. Подключение маршрутов и метрик.
4. В `gemma-base.yaml` — `replicaCount: 1`; Cache/Tune — 0,
   заказы — `order.enabled: false`. Проверка:

```bash
helm template rtx-gemma-base "$RTX_DIR/charts/vllm-runtime" -n "$NS" \
  -f "$RTX_DIR/values/gemma-base.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

Далее подписанный commit/push и Sync этого SHA в `rtx-gemma-base`.
После Ready нужны ответ API и ответ обычному пользователю WebUI.

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
