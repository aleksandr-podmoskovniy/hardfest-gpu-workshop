# Запустить Gemma через AI Inference

Ручной Helm-чарт описывает процесс vLLM. Платформенный Helm-чарт описывает
заказ: какую Model обслуживать, каким классом и с какой стратегией.
Дочерние ресурсы создаёт AI Inference.

## Перед началом

- [ ] Результаты ручных опытов сохранены.
- [ ] Model Gemma готова в ai-models, включая NodeCache целевой ноды.
- [ ] Выполнена [проверка поставки](../docs/SETUP.md#inference-readiness).
- [ ] Чарт и Application `hardfest-gemma-platform` подготовлены по [GitOps](../docs/GITOPS.md).
- [ ] В `platform/gemma.yaml` заполнены реальные Model, InferenceServiceClass и DeviceClass.

Для основного маршрута нужен рецепт полного переноса второй B,
включая assistant и CPU KV. Ниже стандартный **Gemma 64K** показан как точка
сверки: название Throughput само по себе не делает его копией ручной B.
До успешной проверки поставки не останавливайте рабочую Gemma.

## 1. Проверить API, класс и рецепт

```bash
kubectl --context "$GPU_CONTEXT" api-resources --api-group=ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" explain inferenceservice.spec --api-version=ai.deckhouse.io/v1alpha1
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io
kubectl --context "$GPU_CONTEXT" get inferenceserviceclasses,deviceclasses
export INFERENCE_CLASS=REPLACE_INFERENCE_SERVICE_CLASS
kubectl --context "$GPU_CONTEXT" get inferenceserviceclass "$INFERENCE_CLASS" -o yaml
```

Проверьте `scalingPolicy.minReplicas/maxReplicas: 1`, разрешённые
`Dedicated` и `WholeDevice`, `exposurePolicy.authentication: Token`.
Не меняйте общий класс других пользователей.

| Настройка стандартного рецепта | Ожидается |
| --- | --- |
| GPU / окно | Одна H100 / 65 536 |
| KV / prefix cache | FP8 / включён |
| Chunked prefill / бюджет | Включён / 4096 |
| CUDA graphs | Включены |
| CPU KV / assistant | Не входят в этот базовый рецепт |

Если нужен парный performance-тест ручного и платформенного запуска,
сначала выровняйте **все** эффективные параметры, включая окно и бюджет памяти.
Иначе это проверка способа развёртывания, не измерение накладных расходов платформы.

До остановки A проверьте, что штатно собранный модуль поставляет рецепт,
совпадающий с последней измеренной B: ревизии Gemma и assistant, окно,
FP8 KV, 32 GiB CPU KV, chunked prefill 2048, graphs и параметры генерации.
Если B уже расширена до 128K, такой же предел нужен платформенному запуску.
Модель assistant должна доставляться из ai-models вместе с основной моделью.
При отсутствии такого рецепта оставайтесь на работающем ручном запуске и
готовьте рецепт через ветку модуля, не патчите дочерний workload.
После старта сравнение повторяется по реальному плану и конфигурации Pod:
проверка наличия рецепта не доказывает, что планировщик выбрал именно его.

## 2. Освободить GPU и RAM

Сначала скройте маршрут A в WebUI и дождитесь завершения её запросов.
В полном маршруте выключите ручную A: `replicaCount: 0` в
`$DEMO_DIR/values/gemma-a.yaml`. B остаётся только при достаточном бюджете RAM.
На узле 128 GiB скройте маршрут B, дождитесь завершения её запросов
и остановите B по [бюджету памяти](../docs/MEMORY_BUDGET.md).

Выполните commit/push/sync только соответствующих ручных Applications по
[GitOps, шагам 4–5](../docs/GITOPS.md). Затем:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
```

Дождитесь освобождения заявок. Не удаляйте claims вручную.
Если B занимает вторую H100, заказ получает свободную карту.
Когда свободны обе, проверяйте фактический UUID — имя класса не фиксирует номер GPU.

## 3. Включить Helm-заказ

В `$DEMO_DIR/platform/gemma.yaml` установите `order.enabled: true`.
Это **values**, не Kubernetes-манифест: не передавайте файл напрямую в kubectl apply.

```bash
set -o pipefail
helm lint "$DEMO_DIR/charts/inference-service" --strict -f "$DEMO_DIR/platform/gemma.yaml"
helm template hf-platform-gemma "$DEMO_DIR/charts/inference-service" \
  -n hardfest-demo -f "$DEMO_DIR/platform/gemma.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

После успешной проверки:

```bash
git add -- "$DEMO_DIR/platform/gemma.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Launch Gemma through AI Inference"
git push
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-platform \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
```

Проверьте `Succeeded` и применённый SHA. Рецепт выбирается по Model,
оборудованию и стратегии; поле `recipeName` не добавляем.

## 4. Проверить план и законченный ответ

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get inferenceservice hf-platform-gemma -o yaml
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get statefulsets,deployments,services \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name'
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
export GEMMA_WORKLOAD=REPLACE_KIND_AND_WORKLOAD_NAME
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get "$GEMMA_WORKLOAD" -o yaml
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status "$GEMMA_WORKLOAD" --timeout=40m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs "$GEMMA_WORKLOAD" --all-containers --tail=200
```

Выберите дочерние workload и Service по ownerReferences.
Сверьте конфигурацию процесса с согласованными параметрами последней измеренной B:
окно, точность KV, CPU KV, assistant, chunked prefill и CUDA graphs.
Справочная таблица Gemma 64K из шага 1 относится только к отдельному базовому
варианту, не к полному переносу B. При расхождении парный замер не начинайте.
В отдельном терминале пробросьте API-порт выбранного Service:

```bash
export GEMMA_SERVICE=REPLACE_GEMMA_SERVICE
export GEMMA_PORT=8000
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward "svc/$GEMMA_SERVICE" "18005:$GEMMA_PORT"
```

В основном терминале введите ключ сервиса из менеджера секретов без вывода в лог:

```bash
set +x
printf 'Model API key: ' >&2
IFS= read -r -s GEMMA_API_KEY
printf '\n' >&2
curl --fail --silent --show-error --max-time 30 \
  --header @<(printf 'Authorization: Bearer %s\n' "$GEMMA_API_KEY") \
  http://127.0.0.1:18005/v1/models
```

Скопируйте точный ID модели из ответа в запрос:

```bash
curl --fail-with-body --silent --show-error --max-time 180 \
  --header @<(printf 'Authorization: Bearer %s\n' "$GEMMA_API_KEY") \
  -H 'Content-Type: application/json' http://127.0.0.1:18005/v1/chat/completions \
  --data-binary '{"model":"REPLACE_SERVED_MODEL_ID","max_tokens":512,"temperature":0,"messages":[{"role":"user","content":"Зачем KV-кешу отдельный бюджет памяти?"}]}'
unset GEMMA_API_KEY
```

Нужен содержательный завершённый ответ. `Ready` и HTTP 200 без текста недостаточны.

## 5. Подключить чат и графики

Добавьте Service отдельным маршрутом ai-mcp-gateway с названием
`Gemma A — DP`. Сохраните личные VK и учёт расхода.
Повторите запрос из обычной учётной записи и найдите его на графике сервиса.
[Подключение](../docs/CHAT_AND_ACCESS.md), [дашборд](../docs/OBSERVABILITY.md).

### Короткий вариант: A вручную, B через платформу

Этот вариант заменяет ручные итерации B, а не добавляет третью GPU.
Оставьте `hf-gemma-a` на одной H100; ручной `hf-gemma-b` должен быть выключен.
На второй карте запустите `hf-platform-gemma`.
В чате его можно назвать `Gemma B — Tune`, убрав конфликтующий ручной маршрут.
A остаётся 16K, платформенная B — 64K; для сравнения используйте одинаковый
вход 8192 и выход 2048. Больший предел B сам по себе не считается ускорением.
Проверьте суммарный RAM-бюджет до запуска.

## Проверка

- [ ] Заказ использует ai-models; чарт не создаёт ручной дубликат runtime.
- [ ] Нужный SHA применён, сервис и workload готовы, GPU выделена.
- [ ] Зафиксированы реальные параметры, а не только название рецепта.
- [ ] API и чат вернули полный ответ, метрики относятся к этому Service.
- [ ] У заказа один источник управления: GitOps, без параллельного изменения из Console.

Далее — [A30](05-mig-mps.md) и [Qwen TP2](06-tp2.md).
