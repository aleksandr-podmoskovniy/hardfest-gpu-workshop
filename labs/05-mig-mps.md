# Разместить эмбеддер, реранкер и Whisper на A30

Две MIG-партиции по 2g.12gb: первую делят эмбеддер и реранкер через MPS,
вторая целиком выделена Whisper large-v3. Все три приложения — InferenceService
из ai-models. Вручную запускаем только проверочные HTTP-запросы.

![Два раздела A30: совместные MPS-клиенты и отдельное распознавание речи](../assets/08-mig-mps.svg)

## Перед началом

- [ ] Выбран `MIG_CONTEXT` кластера A30, не H100.
- [ ] MIG mode подготовлен заранее, нет чужих занятых разделов.
- [ ] Контроллер GPUClass/GPUPool создал подходящие DeviceClass.
- [ ] Три Model из [catalog/a30.yaml](../catalog/a30.yaml) готовы на ноде A30.
- [ ] Три Applications и чарт подготовлены по [GitOps](../docs/GITOPS.md#9-подготовить-helm-заказы-ai-inference).
- [ ] В runtime поставлены рецепты pooling/score/transcription и проверен CUDA-запуск.

## 1. Сверить классы, квоты и модели

```bash
kubectl --context "$MIG_CONTEXT" get nodes,deviceclasses,inferenceserviceclasses
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get models.ai.deckhouse.io
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
kubectl --context "$MIG_CONTEXT" get deviceclass REPLACE_MPS_DEVICECLASS -o yaml
kubectl --context "$MIG_CONTEXT" get deviceclass REPLACE_DEDICATED_MIG_DEVICECLASS -o yaml
```

| Заказ | Model | Политика класса и рецепт |
| --- | --- | --- |
| `hf-embedding` | `qwen3-embedding-4b-w4a16` | Partition + Shared, MPS; pooling LAST, 2048, max-num-seqs 2 |
| `hf-reranker` | `qwen3-reranker-4b-w4a16` | Та же MIG-партиция и Shared; sequence classification |
| `hf-whisper` | `whisper-large-v3` | Partition + Dedicated на второй 2g.12gb; transcription |

Целевой бюджет каждого MPS-клиента — 46% вычислений и 5 GiB pinned memory;
параметры должны поддерживаться установленным GPU-драйвером и классом.
Для vLLM этих двух моделей рецепт задаёт `gpu-memory-utilization: 0.38`:
CUDA показывает память MIG, не отдельную долю MPS.

Не переносите поля драйвера в произвольный InferenceService и не создавайте
DeviceClass вручную. Проверьте фактический план и DRA allocation после запуска.
MPS — совместное использование, а не дополнительная граница изоляции памяти.

## 2. Включить три Helm-заказа

В `$A30_DIR/platform/{embedding,reranker,whisper}.yaml` заполните
Model, InferenceServiceClass и DeviceClass, затем задайте `order.enabled: true`.
Проверьте destination всех трёх Applications.

```bash
set -o pipefail
for SERVICE in embedding reranker whisper; do
  helm lint "$A30_DIR/charts/inference-service" --strict \
    -f "$A30_DIR/platform/$SERVICE.yaml" || exit 1
  helm template "hf-$SERVICE" "$A30_DIR/charts/inference-service" -n hardfest-demo \
    -f "$A30_DIR/platform/$SERVICE.yaml" |
    kubectl --context "$MIG_CONTEXT" apply --dry-run=server -f - || exit 1
done
git add -- "$A30_DIR/platform"
git diff --cached --check
git diff --cached
git commit -S -s -m "Start three A30 inference services"
git push
```

Только после успешных проверок и push:

```bash
REVISION=$(git rev-parse HEAD)
for SERVICE in embedding reranker whisper; do
  kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "hardfest-$SERVICE-platform" \
    --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
done
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get inferenceservices
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
```

## 3. Проверить размещение и готовность

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get inferenceservice hf-embedding -o yaml
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get inferenceservice hf-reranker -o yaml
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get inferenceservice hf-whisper -o yaml
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get services \
  -o custom-columns='NAME:.metadata.name,OWNER:.metadata.ownerReferences[*].name,PORT:.spec.ports[*].port'
```

Эмбеддер и реранкер должны получить **один MIG UUID**, Whisper — другой.
Одинаковый DeviceClass без этой проверки не доказывает совместное размещение.
Дождитесь Ready с актуальной generation. Сопоставьте план с конфигурацией
Pod, затем проверьте каждый API.

## 4. Выполнить три запроса

Для каждого сервиса выберите Service и порт из шага 3.
Ниже один port-forward, который перезапускается для следующего сервиса.
Для одновременных проверок нужны разные локальные порты.

```bash
export A30_SERVICE=REPLACE_SERVICE
export A30_PORT=8000
kubectl --context "$MIG_CONTEXT" -n hardfest-demo port-forward "svc/$A30_SERVICE" "18003:$A30_PORT"
```

В основном терминале вводите ключ **выбранного сервиса** из менеджера секретов.
Повторяйте ввод после смены Service, не используйте автоматически чужой ключ.

```bash
set +x
printf 'Model API key: ' >&2
IFS= read -r -s A30_API_KEY
printf '\n' >&2
curl --fail --silent --show-error --max-time 30 \
  --header @<(printf 'Authorization: Bearer %s\n' "$A30_API_KEY") \
  http://127.0.0.1:18003/v1/models
```

В каждом запросе замените ID на значение из API этого сервиса.

Эмбеддер:

```bash
curl --fail-with-body --silent --show-error --max-time 120 \
  --header @<(printf 'Authorization: Bearer %s\n' "$A30_API_KEY") \
  -H 'Content-Type: application/json' http://127.0.0.1:18003/v1/embeddings \
  -d '{"model":"REPLACE_EMBEDDING_ID","input":["Динамическое выделение GPU","Квоты и изоляция памяти"]}'
```

Ожидаются два вектора по 2560 элементов. Проверьте отсутствие NaN/Inf
и соответствие размерности конфигурации модели.

Реранкер — после переключения Service и ключа:

```bash
curl --fail-with-body --silent --show-error --max-time 120 \
  --header @<(printf 'Authorization: Bearer %s\n' "$A30_API_KEY") \
  -H 'Content-Type: application/json' http://127.0.0.1:18003/rerank \
  -d '{"model":"REPLACE_RERANKER_ID","query":"Как ограничить память GPU?","documents":["MIG разделяет ресурсы GPU.","Git хранит историю файлов."],"top_n":2}'
```

Ожидаются два результата с конечными score; релевантный документ должен быть выше.
Это прямой endpoint vLLM. Для подключения WebUI через шлюз используется
другой, Cohere-совместимый маршрут из [инструкции доступа](../docs/CHAT_AND_ACCESS.md).

Whisper — после переключения Service и ключа; `sample.wav` —
заранее подготовленная короткая запись с известной фразой без личных данных:

```bash
curl --fail-with-body --silent --show-error --max-time 180 \
  --header @<(printf 'Authorization: Bearer %s\n' "$A30_API_KEY") \
  http://127.0.0.1:18003/v1/audio/transcriptions \
  -F 'model=REPLACE_WHISPER_ID' -F 'file=@sample.wav' -F 'language=ru'
unset A30_API_KEY
```

Ожидается узнаваемый текст записи, не только HTTP 200.

## 5. Подключить RAG и голос

Подключите три API в [WebUI через ai-mcp-gateway](../docs/CHAT_AND_ACCESS.md#4-подключить-базы-знаний-и-whisper).
При замене эмбеддера перестройте индекс; сохраните исходные документы и ACL.
Проверьте вопрос по базе знаний с известным ответом и голосовой ввод.

В [дашборде](../docs/OBSERVABILITY.md) выберите datasource A30, а не H100.
Одновременно нагрузите эмбеддер и реранкер небольшими запросами:
оба должны отвечать без OOM, а метрики — разделяться по сервису.
Whisper оставьте отдельным сервисом на второй партиции.

## Проверка

- [ ] В Console кластера A30 видны три Model и три InferenceService.
- [ ] MPS-клиенты делят один MIG UUID, Whisper использует другой.
- [ ] Успешны embeddings, rerank и transcription напрямую и через шлюз.
- [ ] База знаний возвращает релевантные источники; голос распознаётся.
- [ ] Обычному участнику MCP недоступен.
- [ ] Сохранены ошибки, latency и пик памяти при совместной нагрузке.

A30 остаётся включённой при переходе к [Qwen](06-tp2.md).
Для завершения всего стенда используйте [адресную остановку заказов](../docs/GITOPS.md#остановка),
не удаляя GPUClass, namespace, Model и PVC.
