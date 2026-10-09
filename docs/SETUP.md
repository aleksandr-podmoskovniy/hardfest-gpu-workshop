# Подготовка стенда

Основной маршрут начинается с уже работающей `Gemma A — Base`.
Подготовьте заранее инфраструктуру, модели, рецепты, доступы и мониторинг.
Участники начинают с чата, а не с установки стенда.

| Площадка | Где продолжить |
| --- | --- |
| Две H100 | Шаги 1–7 на этой странице |
| Две RTX 5060 Ti | [Параметры и стартовые файлы](#rtx) |
| Доставка чартов в кластер | [GitLab и Argo CD](GITOPS.md) |

## Перед началом

- На рабочей машине есть Git, Helm 3+, kubectl и curl. YAML редактируется в обычном редакторе.
- Публичная `hardfest-gpu-workshop` клонирована рядом с частной репой `k8s-config`.
- В целевом кластере создан namespace `hardfest-demo`.
- Argo CD имеет доступ к GitLab и зарегистрированному GPU-кластеру.
- Установлены рабочий драйвер, DRA (Dynamic Resource Allocation), механизм заявок на устройства Kubernetes, и контроллер GPUClass/GPUPool.

Сборка образов и тесты исходников не входят в подготовку участника.

## 1. Проверить доступ к кластерам

Команды выполняются из корня вашей **частной репы `k8s-config`**.
Замените имена контекстов своими:

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

`ARGO_CONTEXT` — кластер с Argo CD. `GPU_CONTEXT` — кластер, в котором будут
работать модели. Контекст указан в каждой команде, менять текущий не нужно.

**Проверка:** все команды возвращают ресурсы без ошибок доступа.
Namespace и доступы не создаются runtime-чартом.

## 2. Проверить GPU-ноды и память

Для Gemma нужны две H100 одной ноды: сначала одна карта для каждой конфигурации,
затем обе карты для Qwen **TP2 (Tensor Parallelism на двух GPU)** — разделения
вычислений одной модели между картами.

Для отдельного опыта нужна A30:

- **MIG (Multi-Instance GPU)** разделяет её на аппаратные устройства с собственной памятью.
- **MPS (Multi-Process Service)** позволяет нескольким CUDA-процессам совместно использовать доступное устройство.

Замените `YOUR_GPU_NODE` именем подготовленной ноды:

```bash
kubectl --context "$GPU_CONTEXT" describe node YOUR_GPU_NODE
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,pvc,resourceclaims
kubectl --context "$GPU_CONTEXT" get deviceclasses
```

**Проверка перед запуском:**

- нода `Ready`, не cordoned, нет чужих заявок на нужные GPU;
- DeviceClass создан GPUClass/GPUPool-контроллером и выбирает физические H100;
- обе карты видны гостевой ОС, межкарточный обмен проверен;
- нет новых неисправимых ECC (Error-Correcting Code), ошибок памяти; выполнены проверки из [диагностики](TROUBLESHOOTING.md);
- хватает `Allocatable` RAM и места под веса, временные файлы и кэши.

> [!WARNING]
> Не начинайте запуск при ошибках GPU. Не меняйте драйвер, VFIO, MIG mode и чужие
> блокировки ноды в рамках упражнения.

| Профиль | RAM request / limit | CPU KV | Как размещать |
| --- | --- | --- | --- |
| Gemma A | 24 / 48 GiB | Нет | Одна H100 |
| Gemma B, обе итерации | 56 / 80 GiB | 32 GiB | Одна H100 |
| Qwen TP2 | 80 / 104 GiB | 16 GiB | Обе H100, Gemma остановлены |

### Порядок запуска зависит от RAM

| RAM узла | Порядок |
| --- | --- |
| 128 GiB | A и B последовательно; перед платформенной Gemma остановить также ручную B |
| От 192 GiB | Можно планировать две настроенные Gemma одновременно, проверив остальных потребителей |

При 128 GiB сумма лимитов A и B занимает всю RAM, без запаса системе.
Это бюджет, а не измеренное постоянное потребление;
расчёт приведён в [MEMORY_BUDGET.md](MEMORY_BUDGET.md).

### Подготовка A30

MIG mode на A30 включается заранее. Для опыта создания разделов нужны свободные
ресурсы и драйвер с соответствующей политикой подготовки по DRA-заявкам.
Существующие чужие разделы не удаляются. DeviceClass вручную не создаётся;
H100 этот этап не затрагивает.

## 3. Подготовить веса через ai-models

Нужны Gemma, её assistant и Qwen NVFP4. Ревизии закреплены в
[models.lock.json](../models.lock.json); веса трёх моделей занимают около 183 GiB.
Проверьте лицензии и доступ к репозиториям до импорта.

1. Подготовьте каталог по [инструкции ai-models](../catalog/README.md).
2. Дождитесь `Ready` у каждого `Model` и проверьте digest артефакта.
3. Для ручного runtime заполните `modelRefs` и пути в `site-gemma-catalog.yaml`.
   Чарт ставит аннотацию `ai.deckhouse.io/model` на Deployment; ai-models доставляет
   и монтирует артефакты. Во второй итерации укажите оба Model, включая assistant.
4. В режиме NodeCache проверьте готовность артефакта на целевой ноде.
5. В кластере A30 импортируйте три модели из `catalog/a30.yaml`.

Не оценивайте запас только по квоте бакета: проверьте реальное свободное место
хранилища. Доставка, временная загрузка и другие модели расходуют его отдельно.
Не загружайте веса заново при каждом старте Pod.

**Проверка:** объекты каталога `Ready`; путь в Pod указывает на полные веса
закреплённой ревизии, а не на пустой каталог.

Для замеров подготовьте отдельную CPU-ноду, образ клиента и токенизатор заранее.
[Пример Job для Qwen](../examples/qwen-benchmark-job.yaml) не занимает H100.

<a id="inference-readiness"></a>

## 4. Проверить поставку AI Inference

Этот репозиторий содержит клиентские примеры, не сборку модуля AI Inference.
До остановки ручных моделей проверьте содержимое установленного пакета:

| Этап | Что обязательно должно быть в поставке |
| --- | --- |
| Gemma 64K | FP8 KV, prefix cache, chunked prefill 4096, CUDA graphs; одна H100 |
| Полный перенос второй B | Отдельный согласованный рецепт с assistant, CPU KV 32 GiB и доставкой двух checkpoint |
| Qwen TP2 | `acceleratorCount: 2`, MTP, native Simple CPU KV-offload 16 GiB; runtime 0.31 |
| A30 | Рецепты Qwen3 Embedding/Reranker 4B W4A16 и Whisper large-v3 |
| Класс сервиса | Одна реплика; для Qwen разрешены две целые выделенные GPU; аутентификация Token |

### Что считать переносом B

Основной маршрут требует **полного переноса B**:

- то же длинное окно;
- CPU KV — хранение блоков Key–Value cache в RAM;
- assistant — черновая модель, предлагающая токены основной модели.

Рецепт подготовьте, соберите и проверьте до начала работы.
Базовый Gemma 64K остаётся справочным вариантом, не заменой парного сравнения.

Сравните параметры из пакета, план и процесс после запуска.
Throughput — название стратегии, не доказательство выбора нужного рецепта.

### Ограничения класса сервиса

Проверьте выбранный InferenceServiceClass через `kubectl get ... -o yaml`.
Не используйте `default-llm` вслепую: его штатный вариант допускает
масштабирование до двух реплик, но ограничивает каждую одной GPU.
В классе Qwen проверьте `acceleratorPolicy.maxAcceleratorCount` не меньше `2`,
`scalingPolicy.minReplicas` и `scalingPolicy.maxReplicas` — оба `1`.
Для H100 используйте `exposurePolicy.authentication: Token`;
ключ API хранится в менеджере секретов. Не освобождайте работающие GPU
до успешной проверки модели, рецепта и класса следующего запуска.

## 5. Подготовить подключения

| Что подготовить | Где продолжить | Что должно получиться |
| --- | --- | --- |
| Чарты, values и параметры площадки | [GitOps](GITOPS.md) | Ручные Applications с `replicaCount: 0`, платформенные заказы выключены |
| Отдельные маршруты A и B | [Чат и доступ](CHAT_AND_ACCESS.md) | Выбор `Gemma A — Base` / `Gemma B — Tune` без fallback между ними |
| Личные ключи участников | [Чат и доступ](CHAT_AND_ACCESS.md#2-настроить-регистрацию-и-личные-ключи) | Доступ только после одобрения, отдельный учёт расхода |
| Графики | [Мониторинг](OBSERVABILITY.md) | Метрики нужного namespace и отдельных сервисов |

## 6. Заранее запустить A

В частной копии `$DEMO_DIR/values/gemma-a.yaml` установите `replicaCount: 1`.
Привязки площадки заполнены в `site/gemma.yaml`. Values не передавайте
в kubectl как Kubernetes-манифест — сначала нужен Helm render:

```bash
helm lint "$DEMO_DIR/charts/vllm-runtime" --strict \
  -f "$DEMO_DIR/values/gemma-a.yaml" -f "$DEMO_DIR/site/gemma.yaml"
helm template hf-gemma-a "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-a.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

После успешных проверок отправьте только профиль A:

```bash
git add -- "$DEMO_DIR/values/gemma-a.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Prepare running Gemma baseline"
git push
```

В Application `hardfest-gemma-a` проверьте diff и примените выбранный SHA.
Эквивалент адресного sync:

```bash
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-a \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=30m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
```

Проверьте выбранный GPU, mount ai-models и логи. В отдельном терминале:

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

Проверьте тот же вопрос в WebUI. `helm template`, server dry-run и `Model Ready`
не являются проверкой CUDA или генерации. Закройте port-forward после проверки.

## 7. Оставить готовое стартовое состояние

- A работает на одной H100 и отвечает в `Gemma A — Base`; вторая H100 свободна.
- B выключена, обе её конфигурации и assistant подготовлены. Платформенные
  Gemma и Qwen выключены, их классы и рецепты проверены.
- Все нужные веса доставлены. Runtime-образы доступны и закреплены по digest;
  runtime ручных и платформенных запусков — совместимый vLLM 0.31.
- После регистрации и одобрения тестовый пользователь получил личный VK
  и ответ A; общий ключ вместо персонального не используется.
- Дашборд видит ручную A: после запроса выросли счётчики, единицы и фильтры проверены.
- Для A30 подготовлены модели и три заказа. MIG mode уже включён;
  начальная свободная геометрия позволяет создать две `2g.12gb`.
  Не останавливайте чужие сервисы ради опыта. Для повторного показа существующие
  MIG можно выделить заново, но не называть это созданием геометрии.

После этой проверки оставьте A включённой. [Основное руководство](../README.md#ab)
начинается с её использования. Полные A/B-серии выполняются отдельно от
свободных вопросов пользователей; условия прогрева фиксируются в результатах.

<a id="rtx"></a>
## RTX 5060 Ti: подготовить тот же старт

Нужны две RTX 5060 Ti по 16 GiB на одной ноде, доступная RAM для двух
Gemma и системы, ai-models NodeCache на этой ноде и vLLM 0.31.
Профили ручных Gemma ограничены 18 GiB RAM каждый; shm входит в этот лимит.
Настройку дисков, DRA и модулей выполняем до занятия.

Рабочая директория — частный `k8s-config`. Задайте реальные контексты площадки:

```bash
export GPU_CONTEXT=gpu-cluster ARGO_CONTEXT=management ARGO_NAMESPACE=argocd
export MIG_CONTEXT=a30-cluster
export RTX_DIR=argo-projects/gpu-cluster/hardfest-rtx
export NS=hardfest-rtx
set -o pipefail
```

В новый каталог перенесите из публичной копии:

| Что | Куда в `$RTX_DIR` |
| --- | --- |
| `charts/vllm-runtime`, `charts/inference-service`, `charts/model-catalog` | `charts/` |
| `values/rtx/gemma-base.yaml`, `gemma-cache.yaml`, `gemma-spec.yaml` | `values/` |
| `catalog/rtx.yaml` | `catalog/rtx.yaml` |
| `platform/rtx-gemma.yaml`, `platform/rtx-qwen.yaml` | `platform/` |
| `argocd/rtx/*.yaml` | `argo-app/` |

В существующем стенде переносите diff, не перезаписывайте привязки.

Проверьте перед импортом:

- `REPLACE_*`, repoURL, ветку, destination, DeviceClass, ноду и registry;
- доступность ноде закреплённого runtime-образа;
- классы сервисов: одна GPU для Gemma, две для Qwen, одна реплика,
  namespace опыта и нужный DeviceClass;
- Secret с токеном Hugging Face для закрытых моделей: доставка менеджером
  секретов, не открытым YAML в Git.

1. Через `rtx-models` импортируйте Gemma, assistant и Qwen.
   Дождитесь Model Ready и готовой доставки на RTX; проверьте три записи в каталоге.
2. Заранее проверьте выбор рецептов: Gemma 128K с assistant, FP8 KV и RAM KV 4 GiB;
   Qwen 128K, TP2, MTP, FP8 KV, RAM KV 16 GiB, `max-num-seqs=8`.
   Не собирайте и не обновляйте модуль посреди показа.
3. Подготовьте маршруты ниже и мониторинг ручных и платформенных сервисов.
4. В `values/gemma-base.yaml` поставьте `replicaCount: 1`; Cache/Tune оставьте 0,
   заказы — `order.enabled: false`. Проверьте render, commit/push и Sync только A.

```bash
helm template rtx-gemma-base "$RTX_DIR/charts/vllm-runtime" -n "$NS" \
  -f "$RTX_DIR/values/gemma-base.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

После успешной проверки отправьте профиль подписанным commit и примените
этот SHA в Application `rtx-gemma-base`. Дождитесь Ready и выполните
запрос через API и обычный одобренный аккаунт WebUI.

| Название в WebUI | Маршрут | Service:порт | Имя upstream |
| --- | --- | --- | --- |
| Gemma A — Base | `vllm/rtx-gemma-base` | `rtx-gemma-base:8000` | `rtx-gemma-base` |
| Gemma B — Cache | `vllm/rtx-gemma-cache` | `rtx-gemma-cache:8000` | `rtx-gemma-cache` |
| Gemma B — Tune | `vllm/rtx-gemma-tune` | `rtx-gemma-tune:8000` | `rtx-gemma-tune` |
| Gemma A — DP | `vllm/rtx-gemma-platform` | `rtx-gemma-platform:80` | `rtx-gemma-e2b` |
| Qwen 9B — TP2 | `vllm/rtx-qwen35-tp2` | `rtx-qwen35-tp2:80` | `rtx-qwen35-9b` |

Все эти Service — в `hardfest-rtx`. До занятия показываем только Base.

- Разрешения **VK (Virtual Key)**, личного ключа шлюза, и тарифы учитывают
  реальные upstream-имена; ключи и расход при переключении сохраняются.
- Fallback — автоматическая подмена недоступной модели другой — и кэш готовых
  ответов шлюза выключены.
- Для одинаковых A/B-запросов `chat_template_kwargs.enable_thinking` одинаков.

### Длинный контекст включает ответ

В длинном чате оставьте до 8192 токенов на ответ; для большего выхода
соответственно уменьшайте вход. Вход с шаблоном плюс лимит выхода не должен
превышать окно. `finish_reason: length` у обычного вопроса — обрыв по лимиту,
не доказательство законченного ответа. Контрольному A с окном 4K длинные
настройки не назначайте.

A30 готовится так же, как в основном варианте, и остаётся в соседнем кластере.
На старте A отвечает, вторая RTX свободна, метрики видны. Дальше —
[единый сценарий RTX](../RTX5060.md), без дополнительных упражнений.
