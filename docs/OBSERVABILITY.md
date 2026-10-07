# Подключить мониторинг инференса

[AI Inference / Service performance](../observability/dashboard.yaml) разделяет ряды по
`namespace / service`. Поэтому две Gemma с одинаковыми весами не
сливаются в одну линию. Ручные Deployment и сервисы AI Inference можно
сравнивать вместе, когда Prometheus собирает их `/metrics`.

Ручной чарт маркирует Pod как `app.kubernetes.io/component: llm-runtime`;
`app.kubernetes.io/name` совпадает с именем Service. Дашборд использует эти
метки для списка сервисов, RAM/CPU и перезапусков ещё до готовности `/metrics`.
Это не объект InferenceService: метки владения контроллера ему не присваиваются.
Проверьте, что kube-state-metrics экспортирует обе метки в `kube_pod_labels`.
При недоступном сборе сервис остаётся виден как `DOWN` или `NO SCRAPE`, а не
исчезает. При `replicaCount: 0` Pod отсутствует: остановленный ручной запуск
не считается работающим сервисом.

В каждом кластере используется один и тот же дашборд, без отдельных версий
под карту или модель. Выбирайте namespace, сервис и модель фильтрами.
Источник Prometheus относится к выбранному кластеру: общая схема чата
не объединяет метрики разных кластеров. Учёт ai-mcp-gateway смотрите
в кластере шлюза.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; переменные заданы по [GitOps](GITOPS.md).
- [ ] Установлены Prometheus, CRD `ServiceMonitor` и `ClusterObservabilityDashboard`.
- [ ] Argo AppProject разрешает нужные namespace и кластерный дашборд.
- [ ] Известны namespace и labels Prometheus.
- [ ] Для каждого endpoint выбран один сборщик: двойного scrape нет.

Проверка стандартной установки Deckhouse:

```bash
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get prometheus main -o yaml
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get pods --show-labels
kubectl --context "$GPU_CONTEXT" get crd clusterobservabilitydashboards.observability.deckhouse.io
```

Если сборщик называется иначе, используйте его реальные имя и namespace.

## 1. Подготовить манифесты

```bash
cp -R ../hardfest-gpu-workshop/observability "$DEMO_DIR/observability"
cp ../hardfest-gpu-workshop/argocd/observability.yaml \
  "$DEMO_DIR/argo-app/observability.yaml"
```

В редакторе проверьте следующие привязки:

| Файл/объект | Что проверить |
| --- | --- |
| ServiceMonitor в `observability/monitoring.yaml` | Namespace, selector и соответствие селекторам Prometheus |
| NetworkPolicy в том же файле | Namespace и labels Pod Prometheus |
| `argo-app/observability.yaml` | Git URL, ветка, путь, AppProject, целевой кластер |
| AppProject | Доступ к namespace и `ClusterObservabilityDashboard`, без ненужных wildcard |

Имена файлов относительны `$DEMO_DIR`.
У ручного чарта порт Service называется `http`; на него рассчитан
`hardfest-vllm`. AI Inference создаёт собственный ServiceMonitor с портом
`service-port` — его не переименовывайте. NetworkPolicy в примере открывает
TCP/8000 только сборщику ручных сервисов и не меняет доступ ai-mcp-gateway.

> [!IMPORTANT]
> Для сервисов AI Inference сохраняйте сбор, уже созданный модулем.
> Второй ServiceMonitor одного endpoint даст двойной счёт.

Prometheus должен также выбирать namespace, в котором находится
ServiceMonitor. В стандартной конфигурации Deckhouse с
`serviceMonitorNamespaceSelector` по метке
`prometheus.deckhouse.io/monitor-watcher-enabled` добавьте в существующий
манифест namespace приложения:

```yaml
metadata:
  labels:
    prometheus.deckhouse.io/monitor-watcher-enabled: "true"
```

Остальные поля и labels namespace сохраните. Проверьте фактический selector
в `Prometheus/main`: один лишь ServiceMonitor не гарантирует обнаружение цели.

## 2. Проверить и отправить конфигурацию

```bash
kubectl --context "$GPU_CONTEXT" apply --server-side --dry-run=server \
  -f "$DEMO_DIR/observability"
```

После успешной проверки:

```bash
git add -- "$DEMO_DIR/observability" "$DEMO_DIR/argo-app/observability.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Add inference monitoring and live dashboard"
git push
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/observability.yaml"
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-observability \
  --type merge \
  --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
```

Блок регистрации рассчитан на отдельный Application.
Для App-of-Apps обновите родителя, а не создавайте второго владельца.

## 3. Открыть дашборд и проверить сбор

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-observability
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get servicemonitor hardfest-vllm
kubectl --context "$GPU_CONTEXT" get clusterobservabilitydashboard ai-inference-live
```

В Console: **Система → Управление → Мониторинг → Дашборды →
AI Platform → AI Inference / Service performance**.

В примере объект называется `ai-inference-live`. Если на площадке уже есть
этот дашборд под другим именем, сохраните его `metadata.name` и JSON `uid`,
обновите существующий Application. Не создавайте второй объект только ради
переименования: ранее сохранённая ссылка должна продолжить работать.

Прямой путь в своей Console:

```text
/system/management/monitoring/dashboard/cluster-observability-dashboard/ai-inference-live
```

Установите фильтры:

| Поле | Значение |
| --- | --- |
| Namespace | `hardfest-demo` |
| Service | `hf-gemma-a`, `hf-gemma-b` или активный сервис платформы |
| Model | Нужная модель или All |
| GPU node (whole node) | Нода сервиса; All показывает все GPU кластера |
| Период | Последние 30 минут |
| Prometheus | Сборщик вашей площадки |

Inventory использует labels Pod AI Inference и ручных `llm-runtime`: сервис
виден, пока его Pod существует, даже до готовности `/metrics`.
Без Pod после остановки до нуля запись исчезает; это не ошибка сборщика.
Model ограничивает метрики движка. Inventory и Kubernetes-ресурсы сохраняют
сведения о Pod без метрик модели; для них используйте Service и Runtime pod,
а для ресурсов также GPU node.
`UP` означает успешный сбор, `DOWN` — ошибку имеющейся цели,
`NO SCRAPE` — Pod есть, но цели сбора нет. Сверяйте настройку ServiceMonitor,
число реплик и состояние workload.
Ни один из этих статусов не заменяет успешный запрос к API модели.

Выполните короткие запросы к выбранным сервисам и подождите два интервала scrape.
Статистика задержек появляется только после запросов.

### Регистрация в Grafana

`ClusterObservabilityDashboard` публикует дашборд в Console, но не
в отдельной Grafana. Для Grafana Deckhouse проверьте наличие её CRD:

```bash
kubectl --context "$GPU_CONTEXT" get crd grafanadashboarddefinitions.deckhouse.io
```

Добавьте в тот же GitOps-каталог `grafana-dashboard.yaml`:

```yaml
apiVersion: deckhouse.io/v1
kind: GrafanaDashboardDefinition
metadata:
  name: ai-inference-live
spec:
  folder: AI Platform
  definition: |
    # Вставьте сюда тот же JSON из dashboard.yaml.
```

Комментарий замените полным JSON; поле верхнего уровня `id` не добавляйте,
`uid` сохраните. AppProject должен разрешать также
`deckhouse.io/GrafanaDashboardDefinition`. После commit/push и синхронизации
того же Application дашборд появится в папке **AI Platform** в Grafana.
Используйте одинаковое содержимое для обоих интерфейсов, не отдельные версии.

В Grafana без этой CRD импортируйте JSON вручную. Из существующего
Console-дашборда его можно извлечь без дополнительных утилит:

```bash
mkdir -p results/hardfest
kubectl --context "$GPU_CONTEXT" get clusterobservabilitydashboard ai-inference-live \
  -o jsonpath='{.spec.definition}' > results/hardfest/inference-dashboard.json
```

Без установленной CRD откройте `dashboard.yaml` редактором и скопируйте
JSON блока `spec.definition` в импорт Grafana.
Само создание Console CR не добавляет дашборд в отдельную Grafana.

## Как читать панели

| Панель | Что показывает |
| --- | --- |
| Output tokens/s | Суммарная генерация сервиса по всем сессиям |
| Running / waiting | Запросы в работе и очереди |
| Queue p95 | Ожидание scheduler |
| Среднее время этапов | Queue, prefill, decode: `rate(sum) / rate(count)` |
| TTFT p95 | Время до первого токена внутри vLLM, включая очередь |
| Prefill p95 | Обработка входа по измерению движка |
| TPOT | Время на выходной токен после первого, распределение по запросам |
| Темп decode | Обратное среднее ITL, не общий throughput |
| KV-cache / external hit | Заполнение GPU KV и повторное использование префиксов |
| KV-offload | Закреплённые блоки CPU KV, очередь сохранения и возврат блоков на GPU |
| Speculative decoding | Draft tokens/s и доля принятых токенов; не коэффициент ускорения |
| Шлюз | Отдельные метрики ai-mcp-gateway с собственными фильтрами |
| MIG engine / tensor activity | Активность разделов по `GPU_I_ID` и `GPU_I_PROFILE`, не доля отдельного MPS-процесса |
| Last Xid | Последний код ошибки драйвера для устройства, не количество ECC-ошибок |

Для эмбеддера и реранкера важны requests/s и E2E.
Отсутствие выходных токенов не означает отказ.

### Границы измерения

- Без наблюдений p95 не определён: `No data`/NaN не заменяются нулём.
- Из p95 TTFT нельзя вычитать p95 очереди: это разные распределения.
- Первый токен reasoning-модели не обязательно первый видимый текст ответа.
- Браузер, сеть, RAG и шлюз не входят целиком в метрики движка.

### GPU и RAM

Короткий benchmark считает токены за длительность своей серии. График
`rate(...[$__rate_interval])` усредняет счётчик по временному окну, куда может
попасть и простой. Поэтому пик короткой серии в CLI не обязан совпадать с
высотой графика. Для сопоставления выделите интервал нагрузки и проверьте
шаг scrape; не меняйте единицы или формулу ради совпадения чисел.

GPU-панели показывают все DCGM-устройства выбранных узлов, включая чужую
нагрузку. Фильтр Service не создаёт изолированный учёт MIG/MPS — для него
нужны соответствующие метрики драйвера.

Выбирайте узел явно в фильтре **GPU node (whole node)**. В MIG-режиме экспортёр
может не отдавать обычные `GPU_UTIL` и `FB_USED`; доступны отдельные
счётчики активности MIG. Не заменяйте отсутствующую память нулём и не
суммируйте повторённые значения физической карты как память разных разделов.
Панели GPU не зависят от готовности vLLM: ошибка карты должна оставаться
видимой и после падения движка.

Ненулевой Last Xid — повод сопоставить время с журналом драйвера и
`nvidia-smi -q`. Сброс значения после перезапуска не доказывает устранение
неисправимой ECC-ошибки. Панель не заменяет сравнение volatile/aggregate
счётчиков и проверку повторения под нагрузкой.

### KV-offload и speculative decoding

Ряд **KV-offload: переносы OffloadingConnector** предназначен для native
connector, в том числе профиля `VLLM_USE_SIMPLE_KV_OFFLOAD=0` на RTX.
Он не требует включения Simple CPU backend.

| Метрика native connector | Что показывает |
| --- | --- |
| `kv_offload_total_bytes_total{transfer_type="GPU_to_CPU"}` | Переданные в RAM байты |
| `kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"}` | Возвращённые на GPU байты |
| `kv_offload_cpu_cache_usage_perc` | Доля кеша, закреплённая активными передачами |

Все имена имеют префикс `vllm:`. Дашборд показывает bytes/s и объём за выбранное
окно раздельно по направлению. Ноль pinned usage не означает пустой RAM-кеш;
счётчик записей без чтений не доказывает повторное использование CPU KV.

Для ручных RTX-сервисов добавьте `hardfest-rtx` в `namespaceSelector.matchNames`
существующего ServiceMonitor и разрешите ему доступ к runtime в этом namespace
через NetworkPolicy. Для платформенных сервисов AI Inference создаёт собственный
ServiceMonitor: проверьте его наличие и `up=1` в Prometheus.
Проверьте selector наблюдения за namespace и labels Service. Второй ServiceMonitor
для тех же endpoints создавать не нужно: это может удвоить scrape.

Панели **KV-offload: SimpleCPUOffload (только этот backend)** используют метрики
Simple CPU backend vLLM 0.31. Они подходят любой модели с этим backend,
а не только Qwen. Если backend не включён или runtime не экспортирует
эти метрики, панели остаются пустыми.

| Метрика | Значение |
| --- | --- |
| `simple_kv_offload_load_blocks_total` | Завершённые возвраты блоков на GPU |
| `simple_kv_offload_used_blocks` | Закреплённые блоки активных передач или cache hits |
| `simple_kv_offload_save_outcomes_total{outcome=...}` | Решения о сохранении, с причинами отказов |
| `simple_kv_offload_info` | Факты конфигурации backend, включая ёмкость |
| `simple_kv_offload_pending_store_blocks` | Блоки в очереди сохранения |

`used_blocks` не включает вытесняемые сохранённые блоки.
Не называйте этот график «вся занятая RAM». Счётчики решений о сохранении
также не являются завершённой передачей байтов.
[Семантика метрик v0.31.0](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/simple_kv_offload/metrics.py).

Speculative decoding показывает скорость draft-токенов и долю принятых
токенов. Высокая доля принятия сама по себе не доказывает ускорения:
сравните время и throughput на одинаковой нагрузке. При выключенном
speculative decoding эти панели также могут не иметь данных.

### Учёт на шлюзе

Один `model_name` не различает A и B: веса одинаковы.
Если шлюз экспортирует provider key, используйте его в легенде;
иначе сравнивайте runtime по Service.
Общий серверный VK не является персональным учётом участников.
Отсутствие успешных запросов не должно скрывать шлюз: его фильтры учитывают
попытки обращения к провайдерам и ошибки. Без запросов генерации график
выходных токенов остаётся пустым.

## Проверка

- [ ] В inventory выбранные сервисы имеют `Scrape UP = 1`.
- [ ] После запросов появляются токены и задержки.
- [ ] A и B отображаются отдельно.
- [ ] Нет двух ServiceMonitor для одного endpoint.
- [ ] Дашборд доступен в кластерном разделе Console.

## Если графики пустые

1. Проверьте namespace selector Prometheus, ServiceMonitor selector, имя порта и NetworkPolicy.
2. Сверьте `/metrics` закреплённого runtime: имена и labels могли отличаться.
3. Убедитесь, что в выбранном интервале были запросы.
4. Проверьте права на кластерные дашборды, а не только namespace приложения.
5. Не путайте `Synced` в Argo со здоровым scrape и успешным ответом модели.
