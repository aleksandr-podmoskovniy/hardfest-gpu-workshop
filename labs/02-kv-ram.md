# Проверить возврат KV из оперативной памяти

Prefix cache экономит повторный prefill. KV-offload сохраняет блоки
за пределами GPU и возвращает их при повторном запросе. Проверим именно
возврат из RAM, а не просто быстрый ответ из GPU-кэша.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; переменные заданы по [GitOps](../docs/GITOPS.md).
- [ ] B отвечает с текущим профилем: окно 64K, FP8 KV,
  prefix cache и 32 GiB CPU KV. Первый проход — с [первой B](../values/gemma-b.yaml),
  повтор после assistant — со второй B, не заменяя её профиль.
- [ ] RAM request/limit — 56/80 GiB, shared memory — 40 GiB.
- [ ] На VM 128 GiB A остановлена; посторонних запросов к B нет.

Серия использует вход 32 768 и выход 128 токенов. Она не заменяет замер
decode из A/B, где выход равен 2048. Веса остаются на GPU.

## 1. Подготовить одинаковые входы

Создадим десять синтетических последовательностей token ID обычным shell.
Первый токен у каждой последовательности различается.

```bash
export INPUT_TOKENS=32768
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

## 2. Выполнить серию с RAM-кэшем

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
ответ без `error`, `usage.prompt_tokens: 32768` и выход 128 токенов.
Если curl прервал цикл, серию сначала нужно завершить без ошибок.

Теперь вернитесь к первому префиксу, сняв метрики вокруг одного запроса:

```bash
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/before-return.txt"
curl --fail --max-time 600 http://127.0.0.1:18002/v1/completions \
  -H 'Content-Type: application/json' --data-binary "@$REQUEST_DIR/0.json" \
  -o "results/hardfest/$SERIES/return.json"
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/after-return.txt"
```

## 3. Сопоставить счётчики

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

## 4. Провести контроль без offload

В редакторе откройте `$DEMO_DIR/values/gemma-b.yaml` и удалите **только**
блок `vllm.kv-transfer-config`. Не меняйте prefix cache, FP8 KV, окно
или ресурсные лимиты. Доставьте изменение по [GitOps](../docs/GITOPS.md).

После нового запуска повторите шаги 2–3 с:

```bash
export SERIES=cache-off
```

У обеих серий должны быть новый процесс движка и одинаковый прогрев
на отдельном префиксе. По завершении верните `kv-transfer-config`
из первого профиля B через Git/Argo и проверьте запуск.

## Отдельный опыт: окно 128K после второй итерации

Этот шаг выполняется после [настройки assistant](03-speculation.md).
Сначала сохраните результаты второй B при 64K. В активном
`$DEMO_DIR/values/gemma-b.yaml` измените **только** `vllm.max-model-len`:

```yaml
max-model-len: 131072
```

Не заменяйте весь профиль: `speculative-config`, prefill 2048, offload и
оба mount из `site/gemma-assistant.yaml` должны сохраниться.

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
Для новой серии повторите шаг 1 с `INPUT_TOKENS=65536` и новым `REQUEST_DIR`,
затем шаги 2–3. Ожидаемое `usage.prompt_tokens` теперь равно 65 536.
Этот вход проверяет offload, но сам по себе не проверяет всю границу 128K.
Для проверки границы увеличивайте вход отдельно, оставляя место под 128 токенов
ответа и контролируя отсутствие обрезки. Результаты храните отдельно от 64K.

> [!IMPORTANT]
> Активный контекст должен помещаться на GPU. Offload не превращает RAM
> в дополнительную HBM для произвольно длинной истории.
> В RAM-профиле остаются лимиты 56/80 GiB и shm 40 GiB; A на VM 128 GiB выключена.

После сохранения результата верните **только** `vllm.max-model-len: 65536`
через commit/push/sync того же Application. Сверьте наличие assistant,
prefill 2048 и CPU KV перед сравнением с платформенной Gemma.

Профили [128K без CPU KV](../values/gemma-b-128k.yaml) и
[128K с CPU KV](../values/gemma-b-ram.yaml) относятся к прежнему опыту без
assistant. Они сохранены для воспроизводимости тех результатов и в этом
переходе не применяются.

## Проверка

- [ ] Входы и ответы имеют согласованные длины.
- [ ] Возврат подтверждён байтами CPU → GPU и внешними cache hits.
- [ ] Контрольная серия без offload сохранена отдельно.
- [ ] После опыта восстановлен RAM-кэш B.

[Прежний опыт](../results/kv-ram/README.md) дал 2,89 GiB CPU → GPU
и 65 504 внешних попадания, но использовал другой генератор входов.
Это не ожидаемые цифры текущей серии.

Далее примените [вторую итерацию B](03-speculation.md): она возвращает
окно 64K и сохраняет 32 GiB CPU KV, RAM 56/80 GiB и shm 40 GiB.
