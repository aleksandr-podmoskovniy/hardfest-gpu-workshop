# Найти причину отказа

Сначала нужно выяснить, работает ли сама модель. Если Pod запущен и веса
доступны, проверьте ответ её API через Service. Если тот же запрос проходит
напрямую, проверьте маршрут Bifrost, личные разрешения и настройки WebUI.
Ответ на короткий вопрос ещё не исключает ошибку движка при длинном входе
или под нагрузкой.

После изменения конфигурации сверьте Git SHA и результат Sync Argo.
Статусы `Synced`, Model Ready и ответ `/health` подтверждают отдельные этапы
подготовки, но не успешную генерацию текста.

## Pod не запускается или падает

Укажите namespace и Pod своей площадки:

```bash
export NS=hardfest-demo POD=REPLACE_RUNTIME_POD
kubectl --context "$GPU_CONTEXT" -n "$NS" get pods,resourceclaims,pvc -o wide
kubectl --context "$GPU_CONTEXT" -n "$NS" describe pod "$POD"
kubectl --context "$GPU_CONTEXT" -n "$NS" get events --sort-by=.lastTimestamp
kubectl --context "$GPU_CONTEXT" -n "$NS" logs "$POD" --all-containers --tail=200
```

| Симптом | Проверка и действие |
| --- | --- |
| Pending, нет устройства | Проверить ResourceClaim и занятость GPU; дождаться остановки предыдущего этапа |
| Pending, CPU/RAM | Сравнить requests с Allocatable и другими Pod |
| Нода или том не подходят | Проверить selector, taints и PV node affinity; чужой cordon не обходить |
| Нет файлов модели | Проверить Model revision, Ready, доставку NodeCache и mount |
| Долго загружается | По логам отделить образ, чтение весов, компиляцию и CUDA graphs |
| OOMKilled или CUDA OOM | Разделить RAM, `/dev/shm` и VRAM; сверить [бюджет](MEMORY_BUDGET.md) |

Увеличивать timeout имеет смысл, если загрузка продолжается. Повторяющуюся
ошибку это не исправит. При нехватке памяти проверьте limit контейнера,
даже если на узле ещё свободна RAM. Уменьшение `max-num-seqs` сокращает
число активных запросов, но один длинный запрос всё равно должен помещаться.

## InferenceService создаёт неожиданный runtime

Сверьте Model, класс, выбранный план и принятые параметры процесса.
Для TP2 нужны две GPU в claim и TP2 у движка; assistant/MTP и offload
должны быть видны в конфигурации и логах. Исправляйте заказ или рецепт:
контроллер может перезаписать ручной патч дочернего Deployment/StatefulSet.

У живого `v1alpha1` нет `replicas` и `pause`. Для остановки удаляется конкретный
заказ с сохранением Model, PVC и классов. [Порядок остановки](GITOPS.md#остановка).

## API отвечает, чат — нет

| Проверка | Где искать причину |
| --- | --- |
| Прямой запрос к Service | Готовность runtime, порт, `/v1/models` и upstream-имя |
| Запрос через Bifrost | Service/DNS, сеть, backend credential и точный маршрут |
| Запрос с личным VK | Одобрение пользователя, разрешённая модель, квоты |
| Браузерный чат | ACL, сохранённое подключение, фоновые запросы WebUI |

Имя нового InferenceService должно соответствовать подготовленному маршруту.
`personal access is not ready` требует проверки адаптера; общий ключ проблему
не решает. При `request capability not allowed` проверьте
[Function Calling и инструменты](../integrations/webui-access/README.md#подключение-webui).
Сохранённые настройки WebUI могут перекрывать Helm values.

## Метрики отсутствуют или расходятся

Проверьте [сбор и фильтры](OBSERVABILITY.md), затем [условия нагрузки](MEASUREMENTS.md).
Частые причины — двойной scrape, разное окно усреднения, другой backend offload
или отсутствие завершённых запросов. Быстрый повтор сам по себе не доказывает
возврат KV из RAM; успешный exit code клиента не заменяет `failed=0`.

## Ошибки памяти GPU и сообщения драйвера

При новой неисправимой ошибке памяти остановите нагрузку на карте. Сохраните
время, GPU UUID, логи и volatile/aggregate-счётчики; перезапуск Pod не ремонтирует GPU.
Xid — код драйвера, не обязательно ECC. После перезапуска индексы карт могут
измениться, поэтому сопоставляйте события по UUID. Stop/Start VM не доказывает
аппаратный сброс. Возвращайтесь к нагрузке после согласованной проверки аппаратуры.

<a id="a30-claims"></a>
## Проверить выделение GPU небольшим моделям на A30

MIG делит карту на аппаратные части; MPS позволяет процессам делить устройство.
Для эмбеддера, реранкера и Whisper сопоставьте Pod с выделенным claim и MIG UUID:

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

Имя DeviceClass само по себе не подтверждает геометрию.
В существующем контейнере MPS-сервера с его `CUDA_MPS_PIPE_DIRECTORY`
выполните читающие команды [MPS](https://docs.nvidia.com/deploy/mps/mpsv2-interface.html):

```bash
printf 'get_server_list\n' | nvidia-cuda-mps-control
export MPS_SERVER_PID=REPLACE_SERVER_PID
printf 'get_client_list %s\n' "$MPS_SERVER_PID" | nvidia-cuda-mps-control
```

PID берётся из первого ответа. Сверьте клиентов с процессами эмбеддера и реранкера.
Без этого подтверждён выделенный раздел, но не работа MPS; новый сервер ради
диагностики не запускается.
