# Найти место отказа

Проверяйте путь по порядку: **Git/Argo → Pod → веса → API модели → шлюз → чат**.
Рабочий следующий слой не исправляет предыдущий: например, `/health` не доказывает
успешную генерацию.

Команды используют [переменные площадки](GITOPS.md). Ниже — namespace H100
`hardfest-demo`; для RTX замените его на `hardfest-rtx` и укажите реальные имена.

## Argo не применяет коммит

```bash
git rev-parse HEAD
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b -o yaml
```

Сверьте `repoURL`, `targetRevision`, путь и кластер. `status.sync.revision`
должен совпасть с отправленным commit, а `status.operationState.phase` — завершиться
успешно. Push не запускает sync автоматически. Дождитесь текущей операции;
не применяйте prune, force или удаление Application к неясному diff.

## Pod остаётся Pending

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims,pvc -o wide
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get events --sort-by=.lastTimestamp
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe pod REPLACE_POD_NAME
```

| Причина | Где видна | Безопасное действие |
| --- | --- | --- |
| Нет свободного устройства | ResourceClaim, GPUClass/GPUPool | Дождаться освобождения предыдущего этапа |
| Мало CPU/RAM | Events и Allocated resources ноды | Пересчитать requests и доступный бюджет |
| Нода не подходит | Ready, selector, taints | Сверить привязки площадки |
| Том привязан к другой ноде | PVC → PV → node affinity | Согласовать размещение с готовым кешем |

Не обходите cordon и не удаляйте чужие claims. Для одновременных A и B нужны
две разные физические GPU и достаточная RAM.

## Модель не готова в ai-models

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model REPLACE_MODEL_NAME
```

Сверьте источник, revision, условия готовности, доступ к закрытой модели
и свободное место на диске. Квота хранилища не равна свободному месту.
Не меняйте путь runtime до завершения доставки. `Model Ready` подтверждает
артефакт, но не работу CUDA и генерацию.

## Загрузка идёт долго или срабатывает timeout

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=200
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe deployment hf-gemma-b
```

По логам найдите этап: образ → чтение весов → компиляция → захват CUDA graphs.
Если процесс продвигается, согласуйте startup probe и progress deadline
с холодным запуском. Если повторяет ошибку, увеличение таймера её не исправит.

## OOM или ошибка shared memory

| Где кончилась память | Что входит в бюджет |
| --- | --- |
| GPU | Веса, KV-пул, временные буферы и graphs |
| RAM контейнера | Процессы, CPU KV, page cache и лимит cgroup |
| `/dev/shm` | tmpfs и память коннектора |
| Нода | Allocatable и requests всех Pod |

Сохраните логи и фактические лимиты, затем пересчитайте
[бюджет памяти](MEMORY_BUDGET.md). Свободная RAM ноды не отменяет лимит контейнера;
уменьшение `max-num-seqs` не гарантирует вместимость одной длинной истории.

## AI Inference Ready, но параметры не совпали

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get inferenceservices
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get statefulsets,deployments \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name'
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
```

Сверьте заказ, план и runtime. Для Qwen нужны `acceleratorCount=2`, две разные
GPU в claim и TP2 в движке. Assistant/MTP и RAM offload тоже должны быть видны
в конфигурации и логах, а не только в названии профиля.

Исправляйте рецепт и штатную поставку модуля. Ручной патч дочернего
Deployment/StatefulSet будет перезаписан контроллером.

## API работает, но модель недоступна в Open WebUI

| Проверка по порядку | Если не прошла |
| --- | --- |
| Прямой запрос к Service | Вернуться к runtime |
| Шлюз видит Service | Проверить сеть и backend credential |
| В Bifrost выбран точный маршрут | Разделить A/B по Service и provider key |
| Личный VK разрешает модель | Проверить одобрение, профиль и квоты |
| Модель видна в WebUI | Проверить ACL и сохранённые настройки подключения |

`personal access is not ready` — повод читать логи адаптера, а не выдавать общий
ключ. `request capability not allowed` в браузере — проверить Function Calling
и встроенные инструменты по [инструкции](../integrations/webui-access/README.md#подключение-webui).
Сохранённые настройки WebUI могут иметь приоритет над env/Helm values.

## Метрики пустые или цифры не сходятся

Проверьте [сбор и фильтры](OBSERVABILITY.md), затем [одинаковую нагрузку](MEASUREMENTS.md).
Частые причины: двойной scrape, разные окна усреднения, отсутствующая метрика
другого offload backend или отсутствие запросов в выбранном интервале.

Быстрый повтор не доказывает чтение KV из RAM. Exit code 0 не заменяет проверку
failed requests. TTFT движка, очередь и время видимого ответа — разные измерения.

## Появились новые uncorrectable ECC/Xid

> [!WARNING]
> При новой неисправимой ошибке памяти остановите нагрузку на затронутой карте.
> Сохраните время, GPU UUID, логи и счётчики. Перезапуск Pod не ремонтирует GPU.

Xid — код драйвера NVIDIA, не обязательно ECC. Сопоставляйте события по UUID,
а не индексу карты: после перезапуска VM индексы могут измениться.
Сохраните volatile- и aggregate-счётчики; обнуление первого не устраняет причину.
Stop/Start VM не доказывает аппаратный сброс GPU.

Не продолжайте нагрузочный прогон и не меняйте ECC, VFIO или драйвер в рамках
этой инструкции. Возврат — после согласованной проверки аппаратуры и
**законченного ответа модели**, а не только Ready или `/health` 200.

<a id="a30-claims"></a>
## A30: связать Pod, MIG и MPS

MIG — аппаратные части карты; MPS — совместная работа процессов внутри устройства.
Сначала сопоставьте заявку эмбеддера с устройством; повторите для реранкера и Whisper:

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods -o wide
export A30_POD=REPLACE_EMBEDDING_POD
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pod "$A30_POD" \
  -o jsonpath='{.spec.resourceClaims}{"\n"}{.status.resourceClaimStatuses}{"\n"}'
export A30_CLAIM=REPLACE_ALLOCATED_CLAIM
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaim "$A30_CLAIM" \
  -o jsonpath='{.status.allocation.devices}{"\n"}'
kubectl --context "$MIG_CONTEXT" -n hardfest-demo exec "$A30_POD" \
  -c REPLACE_RUNTIME_CONTAINER -- nvidia-smi -L
```

Устройство в claim должно соответствовать MIG UUID в данных драйвера и
`nvidia-smi`. Название DeviceClass само по себе этого не подтверждает.

В **существующем контейнере MPS-сервера**, с его `CUDA_MPS_PIPE_DIRECTORY`,
доступны read-only команды [интерфейса MPS](https://docs.nvidia.com/deploy/mps/mpsv2-interface.html):

```bash
printf 'get_server_list\n' | nvidia-cuda-mps-control
export MPS_SERVER_PID=REPLACE_SERVER_PID
printf 'get_client_list %s\n' "$MPS_SERVER_PID" | nvidia-cuda-mps-control
```

PID берётся из первого ответа, не из runtime-контейнера. Сверьте клиентов
с процессами эмбеддера и реранкера по данным драйвера. Без списка клиентов
подтверждён выделенный раздел, но не фактическая работа MPS.
Новый MPS-сервер ради проверки не запускается.
