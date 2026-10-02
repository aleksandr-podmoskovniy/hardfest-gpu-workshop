# Диагностика по месту отказа

Начинайте с первого неработающего участка: Git/Argo → размещение Pod →
загрузка модели → API → шлюз → чат. Следующий слой не исправляет предыдущий.

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
| Нет свободной GPU/DRA-ёмкости | ResourceClaim и статус GPUClass/GPUPool |
| Не хватает CPU/RAM | `kubectl describe node`, раздел Allocated resources |
| Не подходит нода | Ready, nodeSelector, taint/toleration |
| PVC доступен на другой ноде | PVC/PV и node affinity |
| Карта ещё занята предыдущим этапом | Pod и ResourceClaim владельца |

Не обходите чужой cordon и не удаляйте активные claims.
Для двух одновременных Gemma нужны две разные физические GPU.

## Модель не готова в ai-models

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe model REPLACE_MODEL_NAME
```

Проверьте source/revision, условия готовности, доступ к источнику,
Secret для закрытой модели и реальную свободную ёмкость хранилища.
Квота бакета не равна свободному месту на диске.
Не переключайте runtime на новый путь до завершения доставки.

`Model Ready` означает готовый артефакт, а не работоспособный инференс.

## Загрузка идёт долго или срабатывает timeout

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=200
kubectl --context "$GPU_CONTEXT" -n hardfest-demo describe deployment hf-gemma-b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get events --sort-by=.lastTimestamp
```

Разделяйте скачивание образа, чтение весов, компиляцию и захват CUDA graphs.
Сверьте startup probe и progress deadline с реальным холодным запуском.
Увеличение таймера помогает только когда процесс действительно продвигается,
а не повторяет ошибку.

## OOM или ошибка shared memory

| Область | Что проверить |
| --- | --- |
| GPU | Веса, KV-пул, окно контекста и архитектура модели |
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

Для Qwen должны совпасть три вещи: `acceleratorCount=2` в плане,
две разные GPU в claim и TP2 в движке. MTP, assistant и CPU KV также
подтверждаются реальной конфигурацией и логами.

Если рецепт не попал в установленный модуль, исправьте штатную поставку.
Не патчите дочерний Deployment/StatefulSet: это расходится с заказом
и будет перезаписано контроллером.

## API работает, но модель недоступна в Open WebUI

Проверяйте по порядку:

1. Прямой запрос к Service vLLM.
2. Сеть от ai-mcp-gateway до этого Service.
3. Точный provider/model route шлюза.
4. Личный Virtual Key, его разрешения и лимиты.
5. Подключение и видимость модели в Open WebUI.

У A и B одинаковый upstream model ID, но разные Service и provider keys.
Общий балансируемый маршрут смешивает A/B.

Локальный пользователь не имеет OIDC-токена. Ему нужна серверная
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

> [!WARNING]
> Остановите нагрузку на затронутой карте и сохраните время ошибки,
> GPU UUID, логи движка и доступные диагностические счётчики.
> Перезапуск Pod не является ремонтом GPU.

Не продолжайте нагрузочную сетку и не меняйте ECC, VFIO или драйвер
в рамках этого упражнения. Возврат к тесту — после отдельной проверки
аппаратуры и согласованного восстановления.
