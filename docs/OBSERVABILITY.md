# Метрики и дашборд

В Deckhouse Console откройте **Система → Управление → Мониторинг → Дашборды →
AI Platform → AI Inference / Service performance**. Выберите кластер, namespace,
Service и время нагрузки. Для GPU отдельно задайте ноду: `All` включает соседей.
Шаблон дашборда — [dashboard.yaml](../observability/dashboard.yaml).

## Что читать

| Панель | Что она показывает |
| --- | --- |
| Running / waiting | Запросы в работе / очереди |
| Output tokens/s | Общая генерация сервиса, не скорость одного чата |
| TTFT | Время до первого токена vLLM, включая очередь |
| Queue / prefill / decode | Где запрос ждёт и тратит время |
| TPOT / ITL | Время выходного токена / интервалы между токенами |
| KV-cache / prefix hits | Занятость выделенного пула / повторное использование начала запроса |
| Pod RAM/CPU, restarts | Расход и перезапуски runtime |
| GPU | Устройство и другие процессы на нём |

p95 — значение, в которое укладываются 95% наблюдений. Вычитать p95 очереди
из p95 TTFT нельзя: это разные распределения. `No data` при отсутствии запросов
не означает нулевую задержку. Короткий benchmark сравнивайте с тем же интервалом
графика: окно `rate` может захватывать простой.

Первый токен рассуждений может ещё не быть видимым ответом. Клиентский TTFT
включает сеть и шлюз; видимый TTFT измеряется до первого текста ответа.
Для эмбеддера и реранкера используйте requests/s и полную задержку.
Расход пользователя смотрите в Bifrost по личному VK.

## Как читать offload и GPU

OffloadingConnector экспортирует метрики с префиксом `vllm:`:

| Метрика | Смысл |
| --- | --- |
| `kv_offload_total_bytes_total{transfer_type="GPU_to_CPU"}` | KV записан в RAM |
| `kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"}` | KV возвращён на GPU |
| `kv_offload_cpu_cache_usage_perc` | Блоки, закреплённые активными передачами |

Нулевая доля закреплённых блоков не означает пустой RAM-кеш. Возврат из RAM
доказывается чтением при [повторе после вытеснения](MEASUREMENTS.md#offload-replay).
У SimpleCPUOffload другой набор счётчиков; сверяйте backend с `/metrics`.
Принятые draft-токены подтверждают работу assistant/MTP, но для ускорения нужен A/B.

GPU-панели относятся к устройству, а не конкретному чату. В MIG-режиме используйте
доступные метрики разделов с `GPU_I_ID` и `GPU_I_PROFILE`; отсутствие `GPU_UTIL`
не заменяйте нулём. Не складывайте повторённые значения физической GPU.

## Подключить сбор

Подготовка выполняется администратором из частного `k8s-config` после
[выбора ветки](SETUP.md). Нужны Prometheus, ServiceMonitor,
ClusterObservabilityDashboard и разрешения AppProject.

```bash
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get prometheus main -o yaml
kubectl --context "$GPU_CONTEXT" -n d8-monitoring get pods --show-labels
kubectl --context "$GPU_CONTEXT" get crd clusterobservabilitydashboards.observability.deckhouse.io
```

Задайте `OBS_ROOT` каталогом площадки, `OBS_NAMESPACE` — её namespace.
Для H100: `OBS_ROOT="$DEMO_DIR" OBS_NAMESPACE=hardfest-demo`;
для RTX 5060: `OBS_ROOT="$RTX_DIR" OBS_NAMESPACE=hardfest-rtx`.
Следующий блок предназначен только для нового каталога:

```bash
test ! -e "$OBS_ROOT/observability" || exit 1
test ! -e "$OBS_ROOT/argo-app/observability.yaml" || exit 1
mkdir -p "$OBS_ROOT/observability" "$OBS_ROOT/argo-app"
cp ../hardfest-gpu-workshop/observability/dashboard.yaml "$OBS_ROOT/observability/dashboard.yaml"
sed "s/hardfest-demo/$OBS_NAMESPACE/g" ../hardfest-gpu-workshop/observability/monitoring.yaml \
  > "$OBS_ROOT/observability/monitoring.yaml"
cp ../hardfest-gpu-workshop/argocd/observability.yaml "$OBS_ROOT/argo-app/observability.yaml"
```

Заполните Git URL, ветку, путь и destination Application. Сверьте selectors
Prometheus, метки namespace и NetworkPolicy. При необходимости добавьте namespace
метку `prometheus.deckhouse.io/monitor-watcher-enabled: "true"`.
Политика примера открывает TCP/8000 от Prometheus; доступ шлюза настраивается отдельно.

Ручной `hardfest-vllm` собирает порт `http`, платформенный ServiceMonitor —
`service-port`. Один endpoint должен собираться один раз. У общих объектов
дашборда и сборщика сохраняйте одного владельца, имя и JSON `uid`.

```bash
test "$(git branch --show-current)" = "$WORKSHOP_BRANCH" || exit 1
kubectl --context "$GPU_CONTEXT" apply --server-side --dry-run=server \
  -f "$OBS_ROOT/observability" || exit 1
git add -- "$OBS_ROOT/observability" "$OBS_ROOT/argo-app/observability.yaml" || exit 1
git diff --cached --check || exit 1
git diff --cached
git commit -S -s -m "Add inference monitoring and live dashboard" || exit 1
git push origin "HEAD:refs/heads/$WORKSHOP_BRANCH" || exit 1
```

Зарегистрируйте и вручную синхронизируйте Application по [GitOps](GITOPS.md#цикл-изменения).
Если им управляет App-of-Apps, используйте родителя.
Отдельная Grafana требует импорта того же JSON или GrafanaDashboardDefinition;
Console-объект сам её не настраивает.

## Проверить сбор

| Статус | Проверка |
| --- | --- |
| `UP` | Отправить запрос и дождаться двух интервалов сбора |
| `DOWN` | Проверить endpoint, сеть и готовность runtime |
| `NO SCRAPE` | Проверить selectors и имя порта |
| Сервис исчез после остановки | При нуле реплик Pod отсутствует — это ожидаемо |

Ручной чарт добавляет Pod метки `app.kubernetes.io/component: llm-runtime`
и `app.kubernetes.io/name`; они нужны в `kube_pod_labels`.
Убедитесь, что A/B видны раздельно, счётчики растут после запросов и источник
Prometheus относится к нужному кластеру. `UP` подтверждает сбор, а ответ модели
проверяется отдельно.
