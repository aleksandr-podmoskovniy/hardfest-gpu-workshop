# Мониторинг: подключение и чтение графиков

Один дашборд **AI Inference / Service performance** сравнивает ручные и
платформенные запуски по `namespace / service`. Одинаковые веса A и B не
смешиваются в одну линию. Шаблон — [dashboard.yaml](../observability/dashboard.yaml).

| Данные | Источник | Где смотреть |
| --- | --- | --- |
| Очередь, токены, время ответа, KV | `/metrics` vLLM → Prometheus | Дашборд инференса |
| Pod, RAM/CPU, перезапуски | Kubernetes → Prometheus | Тот же дашборд |
| GPU и аппаратные разделы | DCGM-экспортёр → Prometheus | GPU-панели выбранной ноды |
| Пользователь, маршрут, расход | Bifrost | Журнал запросов и бюджеты шлюза |

Prometheus выбирается **для конкретного кластера**. Общий чат не объединяет
метрики GPU-кластера и соседнего A30 автоматически.

## 1. Подключить сбор через GitOps

Команды выполняются из `k8s-config` после выбора ветки по [SETUP](SETUP.md).
Нужны Prometheus, типы `ServiceMonitor` и `ClusterObservabilityDashboard`,
а также права AppProject на соответствующие namespace и кластерный дашборд.

```bash
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get prometheus main -o yaml
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get pods --show-labels
kubectl --context "$GPU_CONTEXT" get crd clusterobservabilitydashboards.observability.deckhouse.io
```

Если Prometheus установлен не как `d8-monitoring/main`, используйте его имя.
Выберите **одну** площадку в текущем GPU-кластере:

| Площадка | Переменные |
| --- | --- |
| H100 | `export OBS_ROOT="$DEMO_DIR" OBS_NAMESPACE=hardfest-demo` |
| RTX | `export OBS_ROOT="$RTX_DIR" OBS_NAMESPACE=hardfest-rtx` |

Для нового каталога выполните блок ниже. Он копирует два файла напрямую,
поэтому повторный запуск остановится до изменения существующего каталога.
На действующей площадке перенесите проверенный diff, сохранив имена объектов,
настройки Prometheus и доступы; заново этот блок не выполняйте.

```bash
test ! -e "$OBS_ROOT/observability" || exit 1
test ! -e "$OBS_ROOT/argo-app/observability.yaml" || exit 1
mkdir -p "$OBS_ROOT/observability" "$OBS_ROOT/argo-app"
cp ../hardfest-gpu-workshop/observability/dashboard.yaml "$OBS_ROOT/observability/dashboard.yaml"
sed "s/hardfest-demo/$OBS_NAMESPACE/g" ../hardfest-gpu-workshop/observability/monitoring.yaml \
  > "$OBS_ROOT/observability/monitoring.yaml"
cp ../hardfest-gpu-workshop/argocd/observability.yaml "$OBS_ROOT/argo-app/observability.yaml"
```

ServiceMonitor выбирает только namespace площадки; NetworkPolicy создаётся
в нём же. Установка H100 не требует `hardfest-rtx`, установка RTX — `hardfest-demo`.
В скопированных файлах измените:

| Файл | Привязки площадки |
| --- | --- |
| `observability/monitoring.yaml` | Namespace и labels Prometheus; selectors ручных Service; NetworkPolicy |
| `argo-app/observability.yaml` | Git URL; `targetRevision: WORKSHOP_BRANCH`; путь `$OBS_ROOT/observability`; AppProject, кластер и `destination.namespace: OBS_NAMESPACE` |
| Существующий манифест namespace | Метка выбора namespace, если её требует Prometheus |

При selector Deckhouse по метке `prometheus.deckhouse.io/monitor-watcher-enabled`
добавьте её со значением `"true"`, сохранив остальные поля namespace.
Имена переменных в таблице заменяются их значениями: Argo не раскрывает shell-переменные.
Если обе площадки работают в одном кластере, дополните существующий
Application обоими namespace и отдельной политикой для каждого.
У общих `hardfest-vllm` и `ai-inference-live` должен остаться один владелец.

> [!IMPORTANT]
> Ручной ServiceMonitor `hardfest-vllm` собирает порт `http`.
> AI Inference создаёт свой ServiceMonitor с портом `service-port`.
> Не добавляйте второй сборщик того же endpoint: это удвоит счётчики.

NetworkPolicy примера открывает TCP/8000 от Prometheus к ручным сервисам,
но не настраивает доступ шлюза. После проверки selectors и прав AppProject:

Блок ниже регистрирует отдельный Application. Если им управляет App-of-Apps,
обновите и синхронизируйте родителя вместо прямого `apply`: у Application
должен остаться один владелец. У существующего дашборда сохраните
`metadata.name` и JSON `uid`, чтобы не сломать ссылки.

```bash
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
kubectl --context "$GPU_CONTEXT" apply --server-side --dry-run=server \
  -f "$OBS_ROOT/observability" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply --dry-run=server \
  -f "$OBS_ROOT/argo-app/observability.yaml" || exit 1
git add -- "$OBS_ROOT/observability" "$OBS_ROOT/argo-app/observability.yaml" || exit 1
git diff --cached --check || exit 1
git diff --cached || exit 1
git commit -S -s -m "Add inference monitoring and live dashboard" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$OBS_ROOT/argo-app/observability.yaml" || exit 1
REVISION=$(git rev-parse HEAD) || exit 1
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-observability \
  --type merge \
  --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
```

## 2. Найти обе модели

В Console: **Система → Управление → Мониторинг → Дашборды → AI Platform →
AI Inference / Service performance**. Объект примера — `ai-inference-live`.

```bash
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get servicemonitor hardfest-vllm
kubectl --context "$GPU_CONTEXT" get clusterobservabilitydashboard ai-inference-live
```

Выберите namespace, Service A/B и интервал нагрузки. Для GPU укажите ноду
в **GPU node (whole node)**; `All` включает другие нагрузки на всех нодах.
Фильтр **Model** ограничивает метрики движка, не список Pod и ресурсы Kubernetes.

Ручной чарт уже добавляет Pod метки `app.kubernetes.io/component: llm-runtime`
и `app.kubernetes.io/name` с именем Service. Обе должны экспортироваться
в `kube_pod_labels`. Метки владения InferenceService ручному Pod не нужны.

| Статус | Что это значит | Что проверить |
| --- | --- | --- |
| `UP` | `/metrics` собирается | Отправить запрос модели |
| `DOWN` | Цель найдена, опрос неуспешен | Endpoint, сеть, готовность runtime |
| `NO SCRAPE` | Pod есть, цели нет | Selectors Prometheus и ServiceMonitor, имя порта |
| Сервис исчез после остановки | При `replicaCount: 0` нет Pod | Нормально; остановленный запуск не считается работающим |

После запроса подождите два интервала сбора. `UP` подтверждает метрики,
но не успешную генерацию. Задержки без завершённых запросов могут быть пустыми.

### Если нужна отдельная Grafana

Console-объект не регистрирует дашборд в Grafana. При установленном типе
`GrafanaDashboardDefinition` добавьте в тот же GitOps-каталог:

```yaml
apiVersion: deckhouse.io/v1
kind: GrafanaDashboardDefinition
metadata:
  name: ai-inference-live
spec:
  folder: AI Platform
  definition: |
    # Заменить полным JSON из spec.definition файла dashboard.yaml.
```

Разрешите этот тип в AppProject и синхронизируйте Application. Без такого типа
импортируйте JSON через интерфейс Grafana. В обоих случаях сохраняйте `uid`,
не добавляйте верхнеуровневое поле `id` и используйте тот же JSON, не вторую версию.

## 3. Читать результаты нагрузки

Начните с очереди и скорости. Остальные панели объясняют найденный симптом.

| Панель | Смысл |
| --- | --- |
| Running / waiting | Запросы в работе и ожидании |
| Output tokens/s | Суммарная генерация всех сессий сервиса |
| Queue p95 | Ожидание свободного места в работе движка |
| TTFT p95 | Время до первого токена внутри vLLM, включая очередь |
| Среднее время этапов | Queue, prefill, decode: `rate(sum) / rate(count)` |
| TPOT / темп decode | Время на выходной токен / обратное среднее интервала между токенами; не общий throughput |
| KV-cache / external hit | Занятость KV-пула / повторное использование префиксов |
| Speculative decoding | Скорость предложенных draft-токенов и доля принятых |

**p95** — значение, в которое укладываются 95% наблюдений. Из p95 TTFT нельзя
вычесть p95 очереди: это разные распределения. При отсутствии наблюдений p95
не определён; `No data` не заменяется нулём.

Первый токен рассуждений не обязательно виден как текст ответа. Браузер,
сеть, поиск документов и шлюз не входят целиком во время движка. У эмбеддера
и реранкера сравнивайте requests/s и полную задержку запроса, не output tokens/s.

Короткий benchmark и график могут давать разные числа: `rate` усредняет
счётчик по окну, которое захватывает простой. Для сравнения выделите одинаковый
интервал нагрузки; не меняйте формулу ради совпадения.

### KV-offload: запись — ещё не повторное использование

Панели **OffloadingConnector** используют метрики с префиксом `vllm:`:

| Метрика | Значение |
| --- | --- |
| `kv_offload_total_bytes_total{transfer_type="GPU_to_CPU"}` | Записано в RAM |
| `kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"}` | Возвращено на GPU |
| `kv_offload_cpu_cache_usage_perc` | Доля блоков, закреплённых активными передачами |

Дашборд отдельно показывает скорость и объём за окно. Нулевая доля закреплённых
блоков **не означает пустой RAM-кеш**. Только записи GPU → RAM не доказывают
повторного использования: нужны чтения RAM → GPU при повторном запросе.

У `SimpleCPUOffload` другой набор метрик. Сверяйте backend и `/metrics`
закреплённого runtime; отсутствующие счётчики другого backend не означают отказ.
Высокая доля принятия draft-токенов тоже не доказывает ускорения — нужен A/B-замер.

### GPU: показатели устройства, не отдельного чата

GPU-панели включают чужую нагрузку на выбранной ноде. MIG делит карту на
аппаратные части; MPS позволяет процессам совместно работать внутри устройства.
Фильтр Service сам по себе не выделяет долю процесса в MIG/MPS.

В MIG-режиме обычные `GPU_UTIL` и `FB_USED` могут отсутствовать; используйте
доступные метрики разделов с `GPU_I_ID` и `GPU_I_PROFILE`. Не подставляйте нули
и не суммируйте повторённые значения физической карты как память разных частей.
Ошибка GPU проверяется отдельно по журналу драйвера и `nvidia-smi -q`.

## Проверка готовности

- A и B видны раздельно; их endpoint собирается один раз.
- После запросов появляются скорость и задержки.
- Выбраны нужные кластер, namespace, Service, нода и время.
- Метрики выключенной оптимизации не выданы за нулевое потребление.

Если данных нет, проверьте selectors, порт и NetworkPolicy, затем фактический
`/metrics` и наличие запросов в интервале. `Synced` в Argo не доказывает ни сбор,
ни ответ модели. Для пользовательского расхода нужен Bifrost, а не общий
`model_name` или серверный ключ провайдера.
