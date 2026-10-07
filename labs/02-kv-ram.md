# Проверить возврат KV из оперативной памяти

Prefix cache экономит повторный prefill. KV-offload сохраняет блоки
за пределами GPU и возвращает их при повторном запросе. Проверим именно
возврат из RAM, а не просто быстрый ответ из GPU-кэша.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; переменные заданы по [GitOps](../docs/GITOPS.md).
- [ ] Результат A сохранён; первая B подготовлена, вторая H100 свободна.
- [ ] Профиль первой B: окно 16K, FP8 KV, prefix cache и 32 GiB CPU KV.
  Если B уже отвечает с этими настройками, пропустите запуск в шаге 1.
  Повтор после assistant выполняется со второй B, не заменяя её профиль.
- [ ] RAM request/limit — 56/80 GiB, shared memory — 40 GiB.
- [ ] На VM 128 GiB A остановлена; посторонних запросов к B нет.

Серия использует вход 8192 и выход 128 токенов. Она не заменяет замер
decode из A/B, где выход равен 2048. Веса остаются на GPU.

## 1. Запустить первую B

В `$DEMO_DIR/values/gemma-b.yaml` установите `replicaCount: 1`.
Профиль — [первая B](../values/gemma-b.yaml), без assistant, chunked prefill
и CUDA graphs. Application `hardfest-gemma-b` использует этот файл и
подготовленный `site/gemma.yaml`. На стенде с достаточной RAM A остаётся работать.

```bash
set -o pipefail
helm template hf-gemma-b "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-b.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

После успешного dry-run:

```bash
git add -- "$DEMO_DIR/values/gemma-b.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Start Gemma with prefix cache and RAM KV"
git push
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-b \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=30m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims -o wide
```

Проверьте ответ API по [первой лабораторной](01-ab.md#2-выполнить-серию)
с `SLOT=b`, затем включите подготовленный маршрут `Gemma B — Tune` в WebUI.
На этом этапе Tune означает первую итерацию B, а не наличие assistant.
Повторите контрольную A/B-серию. После неё отдельно проверяйте возврат KV из RAM.

## 2. Подготовить одинаковые входы

Начните с десяти синтетических последовательностей token ID обычным shell.
Первый токен у каждой последовательности различается. Десять запросов не
гарантируют вытеснение: размер серии подбирается по фактическому KV-пулу
и счётчикам, с контролем RAM. Не выдавайте обычный GPU hit за CPU reload.

```bash
export INPUT_TOKENS=8192
export REQUEST_DIR="results/hardfest/kv-requests-$INPUT_TOKENS"
mkdir -p "$REQUEST_DIR"
for DOC in 0 1 2 3 4 5 6 7 8 9; do
  awk -v doc="$DOC" -v tokens="$INPUT_TOKENS" 'BEGIN {
    printf "{\"model\":\"gemma-4-31b\",\"prompt\":[%d", 1000 + doc
    for (i = 1; i < tokens; i++) printf ",1250"
    printf "],\"max_tokens\":128,\"ignore_eos\":true,\"temperature\":0,\"stream\":false}\n"
  }' > "$REQUEST_DIR/$DOC.json"
done
shasum -a 256 "$REQUEST_DIR"/*.json
```

Это искусственная нагрузка для проверки механизма, не качества ответов.
Token ID относятся к токенизатору Gemma; для другой модели сначала
проверьте словарь.

## 3. Выполнить серию с RAM-кэшем

В отдельном терминале откройте доступ к B:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/hf-gemma-b 18002:8000
```

В основном терминале отправьте разные префиксы:

```bash
export SERIES=cache-on
mkdir -p "results/hardfest/$SERIES"
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/before.txt"
for DOC in 0 1 2 3 4 5 6 7 8 9; do
  curl --fail --max-time 600 http://127.0.0.1:18002/v1/completions \
    -H 'Content-Type: application/json' --data-binary "@$REQUEST_DIR/$DOC.json" \
    -o "results/hardfest/$SERIES/$DOC.json" || break
done
```

Проверьте все десять файлов ответа в редакторе. У каждого должны быть полный
ответ без `error`, `usage.prompt_tokens: 8192` и выход 128 токенов.
Если curl прервал цикл, серию сначала нужно завершить без ошибок.

Теперь вернитесь к первому префиксу, сняв метрики вокруг одного запроса:

```bash
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/before-return.txt"
curl --fail --max-time 600 http://127.0.0.1:18002/v1/completions \
  -H 'Content-Type: application/json' --data-binary "@$REQUEST_DIR/0.json" \
  -o "results/hardfest/$SERIES/return.json"
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/after-return.txt"
```

## 4. Сопоставить счётчики

Откройте `before-return.txt` и `after-return.txt`. Сравните приращения
только за повтор первого запроса:

| Счётчик | Что показывает |
| --- | --- |
| `vllm:kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"}` | Переданные из RAM на GPU байты |
| `vllm:external_prefix_cache_hits_total` | Повторно использованные внешние блоки |
| `vllm:prefix_cache_hits_total` | Попадания в локальный GPU-кэш |

Точные имена и labels сверяйте с `HELP/TYPE` вашего runtime.
Для доказательства offload нужны ненулевые CPU → GPU передачи и внешние
попадания на повторе.

| Наблюдение | Следующее действие |
| --- | --- |
| Префикс остался на GPU | Увеличить вытесняющую серию; быстрый повтор ещё не доказывает offload |
| Префикс вытеснен также из RAM | Уменьшить серию; полный prefill не доказывает неисправность коннектора |
| Возврат из RAM подтверждён | Сохранить ответы и приращения метрик |

Запросы нестриминговые. `curl time_starttransfer` не равен TTFT первого
токена. Серверное время повтора берите из приращений histogram `sum/count`
без посторонней нагрузки либо измеряйте отдельно streaming-клиентом.

## 5. Провести контроль без offload

В редакторе откройте `$DEMO_DIR/values/gemma-b.yaml` и удалите **только**
блок `vllm.kv-transfer-config`. Не меняйте prefix cache, FP8 KV, окно
или ресурсные лимиты. Доставьте изменение по [GitOps](../docs/GITOPS.md).

После нового запуска повторите шаги 3–4 с:

```bash
export SERIES=cache-off
```

У обеих серий должны быть новый процесс движка и одинаковый прогрев
на отдельном префиксе. По завершении верните `kv-transfer-config`
из первого профиля B через Git/Argo и проверьте запуск.

## Отдельный опыт: большее окно после второй итерации

Этот шаг выполняется после [настройки assistant](03-speculation.md).
Сначала сохраните результаты второй B при 16K. Пройдите ступень 65536,
а после успешного ответа переходите к 131072. В активном
`$DEMO_DIR/values/gemma-b.yaml` измените **только** `vllm.max-model-len`:

```yaml
max-model-len: 131072
```

Не заменяйте весь профиль: `speculative-config`, prefill 2048, offload и
обе ссылки Model либо оба mount из `site/gemma-assistant.yaml` должны сохраниться.

```bash
helm template hf-gemma-b "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-b.yaml" -f "$DEMO_DIR/site/gemma-assistant.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
```

После успешной проверки:

```bash
git diff -- "$DEMO_DIR/values/gemma-b.yaml"
git add -- "$DEMO_DIR/values/gemma-b.yaml"
git commit -S -s -m "Measure tuned Gemma at 128K"
git push
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-b \
  --type merge --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b \
  -o jsonpath='{.status.operationState.phase}{" "}{.status.sync.revision}{"\n"}'
```

Дождитесь `Succeeded` для `$REVISION`, затем rollout B и успешного короткого ответа.
Для новой серии повторите шаг 2 с новым `REQUEST_DIR`, затем шаги 3–4.
Оставьте место под выход; не подставляйте размер всего окна в длину входа:

| Окно | `INPUT_TOKENS` | Выход | Ожидаемое `usage.prompt_tokens` |
| --- | ---: | ---: | ---: |
| 65 536 | 57344 | 128 | 57 344 |
| 131 072 | 122880 | 128 | 122 880 |

Это длинные входы для проверки offload, не заполнение окна до последнего токена.
Для проверки границы увеличивайте вход отдельно, оставляя место под 128 токенов
ответа и контролируя отсутствие обрезки. Результаты храните отдельно от сравнения 16K.

> [!IMPORTANT]
> Активный контекст должен помещаться на GPU. Offload не превращает RAM
> в дополнительную HBM для произвольно длинной истории.
> В RAM-профиле остаются лимиты 56/80 GiB и shm 40 GiB; A на VM 128 GiB выключена.

Для нового сравнения с контрольной A верните **только** `vllm.max-model-len: 16384`
через commit/push/sync того же Application. Если следующим идёт платформенный
опыт, оставьте проверенное длинное окно B и требуйте такое же от рецепта.
Сверьте assistant, prefill 2048, CPU KV и окно обеих моделей до начала сравнения.

## Проверка

- [ ] Входы и ответы имеют согласованные длины.
- [ ] Возврат подтверждён байтами CPU → GPU и внешними cache hits.
- [ ] Контрольная серия без offload сохранена отдельно.
- [ ] После опыта восстановлен RAM-кэш B.

[Прежний опыт](../results/kv-ram/README.md) дал 2,89 GiB CPU → GPU
и 65 504 внешних попадания, но использовал другой генератор входов.
Это не ожидаемые цифры текущей серии.

Далее примените [вторую итерацию B](03-speculation.md): она возвращает
окно 16K и сохраняет 32 GiB CPU KV, RAM 56/80 GiB и shm 40 GiB.
