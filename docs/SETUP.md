# Подготовка стенда

Основной маршрут начинается с уже работающей `Gemma A — Base`.
Выполните эту подготовку заранее: инфраструктура, модели, рецепты, доступы,
мониторинг и первый запуск A. Участники начинают с чата, а не с установки стенда.
Развёртывание чартов описано в [GitOps](GITOPS.md).
Для RTX используйте [отдельную подготовку](../labs/rtx5060.md#setup).

## Перед началом

- На рабочей машине есть Git, Helm 3+, kubectl и curl. YAML редактируется в обычном редакторе.
- Публичная `hardfest-gpu-workshop` клонирована рядом с частной репой `k8s-config`.
- В целевом кластере создан namespace `hardfest-demo`.
- Argo CD имеет доступ к GitLab и зарегистрированному GPU-кластеру.
- Установлены рабочий драйвер, DRA и контроллер GPUClass/GPUPool.

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
затем обе карты для Qwen TP2. Для отдельного упражнения MIG/MPS нужна A30.
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
- нет новых неисправимых ECC; выполнены проверки из [диагностики](TROUBLESHOOTING.md);
- хватает `Allocatable` RAM и места под веса, временные файлы и кэши.

> [!WARNING]
> Не начинайте запуск при ошибках GPU. Не меняйте драйвер, VFIO, MIG mode и чужие
> блокировки ноды в рамках упражнения.

| Профиль | RAM request / limit | CPU KV | Как размещать |
| --- | --- | --- | --- |
| Gemma A | 24 / 48 GiB | Нет | Одна H100 |
| Gemma B, обе итерации | 56 / 80 GiB | 32 GiB | Одна H100 |
| Qwen TP2 | 80 / 104 GiB | 16 GiB | Обе H100, Gemma остановлены |

На ноде с 128 GiB RAM выполняйте A и B **последовательно**: их лимиты в сумме
равны всей памяти, без запаса системе. Перед платформенной Gemma также остановите
ручную B. Для двух одновременно работающих настроенных Gemma планируйте минимум
192 GiB и проверьте остальных потребителей. Это бюджет, а не измеренное постоянное
потребление; расчёт приведён в [MEMORY_BUDGET.md](MEMORY_BUDGET.md).

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
   При отдельном PVC используйте альтернативный site-пример, не смешивая источники.
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

Базовый Gemma 64K и полный перенос ручной B — разные конфигурации.
Основной маршрут требует **полного переноса B**, включая её длинное окно,
CPU KV и assistant. Рецепт подготовьте, соберите и проверьте до начала работы.
Базовый 64K-профиль остаётся справочным вариантом; он не заменяет парное сравнение.
Сравните параметры из пакета, план и процесс после запуска. Throughput —
название стратегии, не доказательство выбора нужного рецепта.

Проверьте выбранный InferenceServiceClass через `kubectl get ... -o yaml`.
Не используйте `default-llm` вслепую: его штатный вариант допускает
масштабирование до двух реплик, но ограничивает каждую одной GPU.
Точные поля и условия — в [Gemma](../labs/04-deckhouse.md) и
[Qwen](../labs/06-tp2.md).

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

Проверьте выбранный GPU, mount ai-models и логи. Затем выполните короткий
запрос API из [первой лабораторной](../labs/01-ab.md#2-выполнить-серию)
и тот же вопрос в WebUI. `helm template`, server dry-run и `Model Ready`
не являются проверкой CUDA или генерации.

## 7. Оставить готовое стартовое состояние

- A работает на одной H100 и отвечает в `Gemma A — Base`; вторая H100 свободна.
- B выключена, обе её конфигурации и assistant подготовлены. Платформенные
  Gemma и Qwen выключены, их классы и рецепты проверены.
- Все нужные веса доставлены. Runtime-образы доступны и закреплены по digest;
  runtime ручных и платформенных запусков — совместимый vLLM 0.31.
- После регистрации и одобрения тестовый пользователь получил личный VK
  и ответ A; общий ключ вместо персонального не используется.
- Дашборд видит ручную A: после запроса выросли счётчики, единицы и фильтры проверены.
- Для A30 подготовлены модели, заказы и начальное состояние геометрии по
  [лабораторной](../labs/05-mig-mps.md). Не останавливайте чужие сервисы ради опыта.

После этой проверки оставьте A включённой. [Основное руководство](../README.md#ab)
начинается с её использования. Полные A/B-серии выполняются отдельно от
свободных вопросов пользователей; условия прогрева фиксируются в результатах.
