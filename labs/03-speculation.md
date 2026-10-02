# Итерация 2: chunked prefill и speculative decoding

После prefix cache и KV-offload применяем [второй профиль B](../values/gemma-b-spec.yaml).
Он сохраняет FP8 KV, 32 GiB CPU KV и окно 64K, меняет бюджет prefill
4096 → 2048, включает CUDA graphs и Gemma assistant. Это основной этап
перед переносом конфигурации в AI Inference, а не упражнение после Qwen.
Проверка совместной конфигурации на GPU и её производительности обязательна:
подтверждённых замеров этого профиля в репозитории пока нет.

Speculative decoding предлагает несколько токенов черновой моделью и проверяет их
основной. Ускорение зависит от доли принятия, длины принятых цепочек и затрат
на вычисление черновика. Один процент принятия не описывает итоговую скорость.

![Черновик предлагает токены, основная модель принимает префикс и исправляет первый отказ](../assets/07-speculation.svg)

Например, предложение за 3 мс и проверка за 9 мс дают 3 мс на токен,
если цикл выдал четыре токена, и 12 мс, если только один. Это условный
расчёт: для оценки ускорения сравните его с обычным decode на той же нагрузке.

## Что меняется

В профиль добавлены отдельный mount assistant и:

```yaml
speculative-config:
  method: mtp
  model: /models/assistant
  num_speculative_tokens: 1
```

Название метода и поддержка assistant должны соответствовать закреплённому runtime.
Не переносите настройку MTP одной архитектуры на другую по одному имени флага.

Сравнивайте первую и вторую B при одинаковых контексте, входе, выходе,
concurrency и состоянии кэша. CPU KV остаётся включённым в обеих сериях.
Суммарный эффект трёх изменений — не отдельный выигрыш assistant.
Для его выделения повторите второй профиль с выключенным только `speculative-config`.

## Переключить профиль через GitOps

Заранее заполните `site/gemma-assistant.yaml` по [примеру](../examples/site-gemma-assistant.yaml).
Сохраните те же ноду, DeviceClass, основной PVC и разрешения сети; добавьте PVC assistant.
В Application B меняется только последний site-файл; профиль по-прежнему один.
Если Application управляется родителем, синхронизируйте изменение его source через родителя.

```bash
cp "$DEMO_DIR/values/gemma-b-spec.yaml" "$DEMO_DIR/values/gemma-b.yaml"
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-b.yaml"
yq -i '.spec.source.helm.valueFiles[-1] = "../../site/gemma-assistant.yaml"' \
  "$DEMO_DIR/argo-app/gemma-b.yaml"
set -o pipefail
helm template hf-gemma-b "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-b.yaml" -f "$DEMO_DIR/site/gemma-assistant.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/values/gemma-b.yaml" "$DEMO_DIR/site/gemma-assistant.yaml" \
  "$DEMO_DIR/argo-app/gemma-b.yaml"
git diff --cached --check
git commit -S -s -m "Tune Gemma prefill and add assistant with KV offload"
git push
```

Для отдельно зарегистрированного Application, без родителя:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply -f "$DEMO_DIR/argo-app/gemma-b.yaml"
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-b \
  --type merge -p "$(jq -nc --arg rev "$REVISION" '{operation:{sync:{revision:$rev,prune:false}}}')"
```

Дождитесь завершения sync именно этой revision, затем rollout:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
```

## Проверка

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=150
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
```

В логах должны быть загрузка assistant и включённый метод. Ответ API — обязательная
проверка; server-side dry-run не подтверждает поддержку speculative decoding.
Откройте port-forward в отдельном терминале:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/hf-gemma-b 18002:8000
```

Проверьте ответ и метрики:

```bash
curl --fail --max-time 120 http://127.0.0.1:18002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-31b","messages":[{"role":"user","content":"Зачем нужен KV-кэш?"}],"max_tokens":256,"temperature":0}'
curl --fail http://127.0.0.1:18002/metrics
```

Найдите счётчики предложенных/принятых токенов, длины цепочек, TTFT и generation time.
Повторите [нагрузку](01-ab.md) с `SLOT=b SERIES=b-spec`, отдельно проверьте качество
на учебных вопросах и [возврат KV из RAM](02-kv-ram.md) при включённом assistant.
При отсутствии ускорения это результат опыта, а не повод скрыть время черновика.

Откат: отменить свой коммит, push и sync того же Application и его source, если менялся.
Веса assistant не удалять. После успешной проверки переходите к
[Gemma через AI Inference](04-deckhouse.md), сохранив настройки второй итерации.
