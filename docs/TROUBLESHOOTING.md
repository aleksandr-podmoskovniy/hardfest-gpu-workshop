# Диагностика по месту отказа

Начинайте с первого неработающего участка: Git/Argo → размещение Pod →
загрузка модели → API → шлюз → чат. Следующий слой не исправляет предыдущий.

## С чего начать

| Симптом | Первая проверка |
| --- | --- |
| Новый commit не применён | [Argo и версия Git](#argo-не-применяет-коммит) |
| Pod не назначен на ноду | [Ресурсы и заявки](#pod-остаётся-pending) |
| Нет весов или доставка не завершена | [Готовность ai-models](#модель-не-готова-в-ai-models) |
| Запуск остановился или слишком долгий | [Этап загрузки](#загрузка-идёт-долго-или-срабатывает-timeout) |
| Процесс завершён по памяти | [Бюджеты GPU, RAM и shared memory](#oom-или-ошибка-shared-memory) |
| Прямой запрос успешен, чат не работает | [Маршрут и персональные права](#api-работает-но-модель-недоступна-в-open-webui) |

## Argo не применяет коммит

Из корня `k8s-config`:

```bash
git rev-parse HEAD
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b -o yaml
```

Проверьте:

- `repoURL`, `targetRevision`, `source.path`, `destination`;
- `status.sync.revision` совпадает с отправленным коммитом;
- `status.operationState.phase` завершён успешно.

Push не равен sync; autosync учебных Applications выключен.
Если предыдущая операция ещё выполняется, не запускайте вторую.
Не используйте prune, force или удаление Application для неясного diff.

## Pod остаётся Pending

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims,pvc -o wide
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get events --sort-by=.lastTimestamp
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe pod REPLACE_POD_NAME
```

| Возможная причина | Где проверить |
| --- | --- |
| Нет свободной GPU/DRA-ёмкости | ResourceClaim и статус GPUClass/GPUPool; DRA (Dynamic Resource Allocation) — выделение устройств по заявкам Kubernetes |
| Не хватает CPU/RAM | `kubectl describe node`, раздел Allocated resources |
| Не подходит нода | Ready, nodeSelector, taint/toleration |
| PVC доступен на другой ноде | PersistentVolumeClaim — заявка на хранилище; проверьте связанный PersistentVolume и node affinity |
| Карта ещё занята предыдущим этапом | Pod и ResourceClaim владельца |

Не обходите чужой cordon и не удаляйте активные claims.
Для двух одновременных Gemma нужны две разные физические GPU.

## Модель не готова в ai-models

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model REPLACE_MODEL_NAME
```

Проверьте:

- `source/revision` и условия готовности;
- доступ к источнику и Secret для закрытой модели;
- реальную свободную ёмкость хранилища.

Квота бакета не равна свободному месту на диске.
Не переключайте runtime на новый путь до завершения доставки.

`Model Ready` означает готовый артефакт, а не работоспособный инференс.

## Загрузка идёт долго или срабатывает timeout

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=200
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe deployment hf-gemma-b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get events --sort-by=.lastTimestamp
```

Разделите холодный запуск на этапы:

1. Скачивание контейнерного образа.
2. Чтение весов модели.
3. Компиляция.
4. Захват CUDA graphs — запись последовательностей GPU-операций для повторного запуска.

Сверьте startup probe и progress deadline с длительностью реального холодного запуска.
Увеличение таймера помогает только когда процесс действительно продвигается,
а не повторяет ошибку.

## OOM или ошибка shared memory

**OOM (Out of Memory)** — отказ из-за нехватки памяти.
Свободная память в одном месте не компенсирует лимит в другом.

| Область | Что проверить |
| --- | --- |
| GPU | Веса, KV-пул (Key–Value cache — сохранённое состояние attention), окно контекста и архитектура модели |
| RAM контейнера | cgroup limit, процессы движка, CPU KV, page cache |
| `/dev/shm` | Размер tmpfs и бюджет коннектора |
| Нода | Allocatable и суммарные requests других Pod |

Свободная RAM хоста не отменяет лимит контейнера.
Уменьшение `max-num-seqs` не гарантирует вместимость одной длинной истории.
Перед сменой профиля сохраните логи и фактические лимиты.

## AI Inference Ready, но параметры не совпали

Проверьте заказ, результат планирования и созданный workload:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get inferenceservices
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get statefulsets,deployments \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name'
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
```

Для Qwen должны совпасть три вещи:

1. `acceleratorCount=2` в плане.
2. Две разные GPU в claim.
3. **TP2 (Tensor Parallelism на двух GPU)** в движке — распределение вычислений
   одной модели между двумя картами.

**MTP (Multi-Token Prediction)**, предложение нескольких токенов за шаг,
assistant — отдельную черновую модель — и CPU KV тоже проверьте
по реальной конфигурации и логам.

Если рецепт не попал в установленный модуль, исправьте штатную поставку.
Не патчите дочерний Deployment/StatefulSet: это расходится с заказом
и будет перезаписано контроллером.

## API работает, но модель недоступна в Open WebUI

Проверяйте по порядку:

1. Прямой запрос к Service vLLM.
2. Сеть от ai-mcp-gateway до этого Service.
3. Точный provider/model route шлюза.
4. Личный VK (Virtual Key), ключ шлюза с разрешениями и лимитами пользователя.
5. Подключение и видимость модели в Open WebUI.

У A и B одинаковый upstream model ID, но разные Service и provider keys.
Общий балансируемый маршрут смешивает A/B.

Локальный пользователь не имеет **OIDC (OpenID Connect)** токена внешней системы входа.
Ему нужна серверная
авторизация inference без передачи сервисного ключа браузеру.
Сохранённая конфигурация WebUI может иметь приоритет над env/Helm values.

Управляющий ключ с ограниченной областью доступа может не видеть созданные
объекты. Проверяйте через штатную администраторскую сессию, не обходя контроль доступа.

## Метрики пустые или цифры не сходятся

Начните с [проверки мониторинга](OBSERVABILITY.md), затем сверьте
[методику измерений](MEASUREMENTS.md).

- Нулевой exit code benchmark не доказывает отсутствие failed requests.
- Быстрый повтор не доказывает возврат KV из RAM.
- TTFT, очередь, prefill, reasoning и видимый текст — разные интервалы.
- Prefix cache движка и кэш готовых ответов шлюза — разные механизмы.
- Дублирующий scrape увеличивает суммарные счётчики.

## Появились новые uncorrectable ECC/Xid

**ECC (Error-Correcting Code)** — механизм контроля ошибок памяти.
Uncorrectable означает, что исправить ошибку не удалось.
**Xid** — диагностический код драйвера NVIDIA, не синоним ECC.

> [!WARNING]
> Остановите нагрузку на затронутой карте и сохраните время ошибки,
> GPU UUID, логи движка и доступные диагностические счётчики.
> Перезапуск Pod не является ремонтом GPU.

Сопоставляйте ошибки по **UUID (Universally Unique Identifier)** GPU,
постоянному идентификатору карты: после перезапуска VM индексы могут поменяться.

- Полный Stop/Start VM не доказывает аппаратный сброс GPU на хосте.
- Обнуление volatile-счётчика не означает устранения причины.
- Сохраните также aggregate-счётчик до и после восстановления.

После восстановления проверяйте законченный ответ модели, а не только
Ready или `/health` 200: процесс API может продолжать отвечать на проверки
здоровья после ошибки CUDA в worker-процессе.

Не продолжайте нагрузочную сетку и не меняйте ECC, VFIO или драйвер
в рамках этого упражнения. Возврат к тесту — после отдельной проверки
аппаратуры и согласованного восстановления.

<a id="a30-claims"></a>
## Как связать модели с MIG и проверить MPS

Список частей ещё не связывает их с моделями. Сначала выбирается Pod
эмбеддера, затем та же проверка повторяется для реранкера и Whisper:

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

Заявка показывает выделенное устройство и конфигурацию драйвера.
Идентификатор устройства сопоставляется с MIG UUID — постоянным идентификатором
части — в данных драйвера и `nvidia-smi`. Имя DeviceClass само по себе не доказательство.

Фактических MPS-клиентов показывает управляющий процесс MPS на A30.
В его контейнере, с его `CUDA_MPS_PIPE_DIRECTORY`, доступны read-only команды
[интерфейса MPS v2](https://docs.nvidia.com/deploy/mps/mpsv2-interface.html):

```bash
printf 'get_server_list\n' | nvidia-cuda-mps-control
# PID берётся из предыдущего ответа, не PID процесса внутри runtime-контейнера.
export MPS_SERVER_PID=REPLACE_SERVER_PID
printf 'get_client_list %s\n' "$MPS_SERVER_PID" | nvidia-cuda-mps-control
```

Подключённые процессы сверяются с эмбеддером и реранкером в логах драйвера.
Если список клиентов недоступен, подтверждена геометрия и заявленный режим,
но не фактическое использование MPS. Новый MPS-сервер ради проверки не запускается.
