# Подключить мониторинг инференса

[Дашборд](../observability/dashboard.yaml) разделяет ряды по
`namespace / service`. Поэтому две Gemma с одинаковыми весами не
сливаются в одну линию. Ручные Deployment и сервисы AI Inference можно
сравнивать вместе, когда Prometheus собирает их `/metrics`.

Если H100 и A30 находятся в разных кластерах, откройте дашборд в каждой
Console отдельно. Источник Prometheus относится к выбранному кластеру:
общая схема чата не объединяет их метрики. Вкладка H100 нужна для Gemma/Qwen,
вкладка A30 — для эмбеддера, реранкера и Whisper. Учёт ai-mcp-gateway смотрите
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
AI Platform → AI Inference / Live performance**.

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
| Период | Последние 30 минут |
| Prometheus | Сборщик вашей площадки |

Inventory использует также сведения Kubernetes: сервис остаётся виден,
даже если движок ещё не запустился и не отдаёт `/metrics`.
`UP` означает успешный сбор, `DOWN` — ошибку имеющейся цели,
`NO SCRAPE` — отсутствие цели. Последнее нормально для намеренно выключенных
Gemma при запуске Qwen; сверяйте число реплик и состояние InferenceService.
Ни один из этих статусов не заменяет успешный запрос к API модели.

Выполните короткие запросы к выбранным сервисам и подождите два интервала scrape.
Статистика задержек появляется только после запросов.

Если вместо Deckhouse Console используется Grafana, импортируйте JSON
из `spec.definition`. После создания CR его можно извлечь без дополнительных утилит:

```bash
mkdir -p results/hardfest
kubectl --context "$GPU_CONTEXT" get clusterobservabilitydashboard ai-inference-live \
  -o jsonpath='{.spec.definition}' > results/hardfest/inference-dashboard.json
```

Без установленной CRD откройте `dashboard.yaml` редактором и скопируйте
JSON блока `spec.definition` в импорт Grafana.
Само создание CR не добавляет дашборд в отдельную Grafana.

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
| KV-offload | Экспортируемые backend метрики CPU KV и передачи байтов |
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

GPU-панели показывают все DCGM-устройства выбранных узлов, включая чужую
нагрузку. Фильтр Service не создаёт изолированный учёт MIG/MPS — для него
нужны соответствующие метрики драйвера.

Выбирайте узел явно в фильтре **GPU node**. На A30 в MIG-режиме экспортёр
может не отдавать обычные `GPU_UTIL` и `FB_USED`; доступны отдельные
счётчики активности MIG. Не заменяйте отсутствующую память нулём и не
суммируйте повторённые значения физической карты как память разных разделов.
Панели GPU не зависят от готовности vLLM: ошибка карты должна оставаться
видимой и после падения движка.

Ненулевой Last Xid — повод сопоставить время с журналом драйвера и
`nvidia-smi -q`. Сброс значения после перезапуска не доказывает устранение
неисправимой ECC-ошибки. Панель не заменяет сравнение volatile/aggregate
счётчиков и проверку повторения под нагрузкой.

В vLLM 0.30 `kv_offload_cpu_cache_usage_perc` описывает память,
закреплённую активными передачами, а не все сохранённые KV-блоки.
Ноль не означает пустой RAM-кэш. `cpu_offload_gb` относится к выгрузке
**весов**, не KV. Выделение кэша и фактический возврат байтов проверяются отдельно.

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

1. Проверьте ServiceMonitor selector, имя порта и NetworkPolicy.
2. Сверьте `/metrics` закреплённого runtime: имена и labels могли отличаться.
3. Убедитесь, что в выбранном интервале были запросы.
4. Проверьте права на кластерные дашборды, а не только namespace приложения.
5. Не путайте `Synced` в Argo со здоровым scrape и успешным ответом модели.
