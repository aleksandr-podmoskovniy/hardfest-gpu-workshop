# Настроить prefill и добавить speculative decoding

Во второй итерации B сохраняем prefix cache и KV-offload, уменьшаем бюджет
prefill с 4096 до 2048, включаем CUDA graphs и Gemma assistant.
Проверим совместимость настроек и повторим ту же нагрузку.

![Черновик предлагает токены, основная модель проверяет предложение](../assets/07-speculation.svg)

Высокий процент принятия не доказывает ускорение: учитывайте время assistant,
длину принятых цепочек и итоговый TPOT. Например, черновик за 3 мс и проверка
за 9 мс дадут 3 мс на токен при четырёх выданных токенах или 12 мс при одном.
Это условный расчёт, не результат на нашем стенде.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; первая B и [RAM-кэш](02-kv-ram.md) проверены.
- [ ] Результаты `b-cache` сохранены вне Pod.
- [ ] Gemma и совместимый assistant закреплённой ревизии доступны на PVC.
- [ ] На VM 128 GiB A выключена; B доступны RAM request/limit 56/80 GiB и shm 40 GiB.

Полную комбинацию второй B нужно проверить на своей GPU: подтверждённых
замеров этого профиля в репозитории пока нет.

## 1. Подготовить профиль и два mount

```bash
cp "$DEMO_DIR/values/gemma-b-spec.yaml" "$DEMO_DIR/values/gemma-b.yaml"
```

Site-файл assistant подготовлен на этапе GitOps. Не перезаписывайте его
публичным примером: в нём уже находятся привязки площадки.
Откройте файлы редактором:

| Файл | Поле | Что установить |
| --- | --- | --- |
| `$DEMO_DIR/values/gemma-b.yaml` | `replicaCount` | `1` |
| `$DEMO_DIR/site/gemma-assistant.yaml` | `nodeSelector`, `dra`, `tolerations`, `networkPolicy` | Привязки из рабочего `site/gemma.yaml` |
| Тот же site-файл | `modelVolumes` | Оба mount: основная Gemma и assistant; существующие PVC и их `subPath` |
| `$DEMO_DIR/argo-app/gemma-b.yaml` | `spec.source.helm.valueFiles` | Два файла из блока ниже |

```yaml
valueFiles:
  - ../../values/gemma-b.yaml
  - ../../site/gemma-assistant.yaml
```

> [!IMPORTANT]
> Helm заменяет список `modelVolumes` целиком. Только mount assistant
> в site-файле уберёт mount основной Gemma.

В профиле должен сохраниться блок:

```yaml
speculative-config:
  method: mtp
  model: /models/assistant
  num_speculative_tokens: 1
```

Метод относится к этой архитектуре и закреплённому runtime. Одинаковое
название флага не делает произвольную draft-модель совместимой.

## 2. Проверить и отправить изменение

```bash
set -o pipefail
helm template hf-gemma-b "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-b.yaml" -f "$DEMO_DIR/site/gemma-assistant.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

Продолжайте только после успешного dry-run:

```bash
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/values/gemma-b.yaml" "$DEMO_DIR/site/gemma-assistant.yaml" \
  "$DEMO_DIR/argo-app/gemma-b.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Tune Gemma prefill and add assistant with KV offload"
git push
```

## 3. Синхронизировать Application B

Если Application зарегистрирован отдельно, обновите его source:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/gemma-b.yaml"
```

Если им управляет родительский Application, сначала синхронизируйте родителя.
Не создавайте второго владельца одной конфигурации.

```bash
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-b \
  --type merge \
  --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b \
  -o jsonpath='{.status.operationState.phase}{" "}{.status.sync.revision}{"\n"}'
```

Дождитесь `Succeeded` для `$REVISION`, затем проверьте rollout:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=40m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=200
```

## 4. Проверить ответ и счётчики

В отдельном терминале:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/hf-gemma-b 18002:8000
```

В основном терминале:

```bash
curl --fail --max-time 120 http://127.0.0.1:18002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-31b","messages":[{"role":"user","content":"Зачем нужен KV-кэш?"}],"max_tokens":256,"temperature":0}'
curl --fail http://127.0.0.1:18002/metrics
```

## Проверка

- [ ] В логах загружен assistant и включён нужный метод.
- [ ] API вернул содержательный ответ и `usage`, без ошибки.
- [ ] После запросов растут счётчики предложенных и принятых токенов.
- [ ] [Нагрузка A/B](01-ab.md) с `SLOT=b SERIES=b-spec` завершилась полностью.
- [ ] [Возврат KV из RAM](02-kv-ram.md) сохраняется с включённым assistant.
- [ ] Ответы на одинаковые учебные вопросы остаются приемлемыми.

Разница `b-cache` → `b-spec` включает сразу prefill, CUDA graphs и assistant.
Чтобы выделить вклад assistant, повторите второй профиль, удалив только
`vllm.speculative-config` через Git/Argo. При отсутствии ускорения сохраните
этот результат вместе с долей принятия и временем черновика.

## Откат

Отмените только свой коммит, отправьте его и повторите sync Application
и его source. Веса assistant не удаляйте.
Следующий этап — [Gemma через AI Inference](04-deckhouse.md).
