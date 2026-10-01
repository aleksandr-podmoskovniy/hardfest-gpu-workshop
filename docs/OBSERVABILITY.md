# Метрики всех сервисов инференса

[Дашборд](../observability/dashboard.yaml) показывает отдельные ряды для каждой пары `namespace / service`. Поэтому две реплики с одинаковыми весами не превращаются в одну линию. Он работает с метриками vLLM напрямую: ручные Deployment и сервисы AI Inference можно выбирать вместе, если Prometheus уже собирает их `/metrics`.

В Deckhouse Console откройте **Система → Управление → Мониторинг → Дашборды → AI Platform → AI Inference / Live performance**. Это кластерный дашборд, не страница проекта. Прямой путь в своей Console:

```text
/system/management/monitoring/dashboard/cluster-observability-dashboard/ai-inference-live
```

Выберите Namespace `hardfest-demo`, оба Service `hf-gemma-a` и `hf-gemma-b`, интервал последних 30 минут. Для обзора всех сервисов оставьте All. Если используется другой Prometheus, выберите его в первом поле.

## Доставка через GitOps

Нужны CRD `ServiceMonitor` и `ClusterObservabilityDashboard`, Prometheus и права Argo на кластерный дашборд и namespace мониторинга. В стандартной конфигурации Deckhouse сборщик называется `main` и находится в `d8-monitoring`. Для другой установки адаптируйте selector, namespace и NetworkPolicy по фактическим labels:

```bash
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get prometheus main -o yaml
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get pods --show-labels
kubectl --context "$GPU_CONTEXT" get crd clusterobservabilitydashboards.observability.deckhouse.io
```

Из корня GitOps-репозитория, с переменными из [GitOps](GITOPS.md):

```bash
cp -R ../hardfest-gpu-workshop/observability "$DEMO_DIR/observability"
kubectl kustomize "$DEMO_DIR/observability"
kubectl --context "$GPU_CONTEXT" apply --server-side --dry-run=server \
  -k "$DEMO_DIR/observability"
git add "$DEMO_DIR/observability"
git diff --cached --check
git diff --cached
git commit -S -s -m 'Add inference monitoring and live dashboard'
git push
```

Подготовьте [Application](../argocd/observability.yaml): укажите свою репу, ветку, путь, Argo project и целевой кластер. Сохраните его как `$DEMO_DIR/argo-app/observability.yaml`, проверьте diff и также закоммитьте. Для общего AppProject понадобится разрешение на оба namespace и `ClusterObservabilityDashboard`; не расширяйте чужой проект до wildcard без необходимости.

```bash
kubectl --context "$ARGO_CONTEXT" -n argocd apply \
  -f "$DEMO_DIR/argo-app/observability.yaml"
revision=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n argocd patch application hardfest-observability \
  --type=merge -p "$(jq -nc --arg rev "$revision" \
  '{operation:{sync:{revision:$rev,prune:false}}}')"
kubectl --context "$ARGO_CONTEXT" -n argocd get application hardfest-observability
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get servicemonitor hardfest-vllm
kubectl --context "$GPU_CONTEXT" get clusterobservabilitydashboard ai-inference-live
```

ServiceMonitor выбирает только сервисы воркшопа. Порт Service должен называться `http`; в публичных манифестах это уже задано. NetworkPolicy открывает TCP/8000 только Prometheus в namespace мониторинга и не меняет доступ Bifrost. Для сервисов AI Inference сохраняйте сбор, уже созданный модулем: второй scrape одного endpoint даст двойной счёт.

Дашборд имеет Grafana-совместимое поле `spec.definition`. Если Console/observability нет, извлеките JSON для импорта в свой Grafana; это отдельная доставка, создание CR само по себе не добавляет дашборд в Grafana:

```bash
yq -r '.spec.definition' observability/dashboard.yaml | jq .
```

## Что смотреть

| Панель | Что измеряет |
| --- | --- |
| Output tokens/s | Суммарная скорость генерации сервиса по всем сессиям |
| Running / waiting | Текущие запросы в работе и очереди |
| Queue p95 | Ожидание в scheduler, отдельно от вычисления входа |
| Среднее время этапов | Queue, prefill и decode: `rate(sum) / rate(count)`, не p95 |
| TTFT p95 | Время до первого токена внутри vLLM, включая очередь |
| Prefill p95 | Обработка входа по измерению движка |
| TPOT | Время на выходной токен после первого токена, распределение по запросам |
| Темп decode | Обратное среднее ITL; не суммарный throughput сервиса |
| KV-cache / external hit | Заполнение GPU KV и повторное использование префикса |
| KV-offload в RAM | Доля CPU KV, занятая активными передачами, и фактические скорости записи/чтения для поддерживаемого backend |
| Bifrost | Отдельные показатели шлюза со своими фильтрами namespace/model |

Для эмбеддера и реранкера важны requests/s и E2E; отсутствие выходных токенов не означает отказ. Статистика времени появляется только после запросов. Без наблюдений p95 не определён, поэтому No data/NaN не заменяются красивым нулём.

Не вычитайте p95 очереди из p95 TTFT: это разные распределения. Первый генерируемый токен также не обязательно совпадает с первым видимым словом ответа в reasoning-модели. Задержка браузера, сети, RAG и шлюза не входит целиком в метрики движка.

GPU-панели показывают все DCGM-устройства выбранных узлов. На общей ноде они включают другие нагрузки, а не приписывают всю карту выбранному сервису. Изолированное потребление MIG/MPS требует соответствующих метрик драйвера; фильтр Service его не создаёт.

Панели RAM-кэша остаются пустыми, пока offload выключен или backend не экспортирует эти метрики. В vLLM 0.30 `kv_offload_cpu_cache_usage_perc` показывает память, закреплённую **активными передачами**, не общий объём сохранённых KV: ноль не означает пустой кэш. `cpu_offload_gb` в таблице конфигурации — выгрузка **весов**, не KV. Увидеть выделенный RAM-кэш и реальный возврат байтов — две разные проверки.

Строки Bifrost не связываются с runtime по одному `model_name`: A и B используют одни веса. Если шлюз экспортирует выбранный provider key, он виден в легенде; иначе эта часть не может различить A/B. Для сравнения используйте графики vLLM по Service. Общий серверный VK также не является персональным учётом участников.

## Если графики пустые

1. В inventory должны быть обе строки Service с `Scrape UP = 1`. Это проверка scrape, не ответа модели.
2. Проверьте ServiceMonitor selector, имя порта и разрешение NetworkPolicy. `Synced` в Argo не доказывает, что endpoint доступен Prometheus.
3. Выполните короткий запрос к A и B. Через два интервала scrape проверьте токены и задержки.
4. Если пропал конкретный ряд, сравните имена метрик и labels с `/metrics` закреплённого образа. Не суммируйте дублирующие ServiceMonitor.
5. В Console проверьте кластерный раздел и права на дашборды, а не только namespace приложения.
