<p align="center">
  <a href="https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop">
    <img src="assets/workshop-qr.svg" width="250" height="250" alt="QR-код: открыть этот мастер-класс на GitHub">
  </a>
</p>

# Инференс без простоя GPU: чиним LLM-сервис руками и делим карту на живом кластере

Александр Подмосковный, Флант / Deckhouse Platform

У LLM-сервиса может закончиться память задолго до того, как GPU загрузится вычислениями. Разберём это на Gemma: сравним две конфигурации, расширим контекст и вернём KV-кэш из RAM. Затем разделим A30 между небольшими сервисами.

Основная практика рассчитана на 90 минут при заранее загруженных весах. [Проверки и воспроизводимость](docs/STATUS.md).

<a id="contents"></a>

## Содержание

1. [Стенд и подключение](#topology)
2. [Подготовка окружения](#setup)
3. [Где теряется время](#latency)
4. [Сколько памяти нужно Gemma](#memory)
5. [Gemma A — Base и Gemma B — Tune](#ab)
6. [Тот же запуск через AI Inference](#platform)
7. [128K и KV-кэш в RAM](#ram)
8. [Динамический MIG и MPS](#placement)
9. [Бонус: Qwen TP2](#tp2)
10. [Дополнительно: черновая генерация](#speculation)
11. [Остановка](#cleanup)
12. [Результаты](#results)

<a id="topology"></a>

## Стенд и подключение

![Два кластера: Open WebUI обращается к HA Bifrost, который направляет запросы к двум Gemma и сервисам поиска](assets/01-topology.svg)

На двух H100 работают **одинаковые веса Gemma 4 31B**. Отличаются параметры движка, а не модель или квантование весов. A30 выделена сервисам поиска. После основной практики обе H100 можно освободить для одного Qwen с TP=2.

Образ vLLM, ревизии и размеры моделей закреплены в [models.lock.json](models.lock.json). Здесь 64K — 65 536 токенов, 128K — 131 072, GiB — двоичные гигабайты.

<a id="chat"></a>

В Open WebUI выберите **Gemma A — Base**, затем **Gemma B — Tune**. Задайте одинаковый вопрос по одному документу. Документы и индекс остаются в базе знаний при смене LLM.

Регистрация участников — с подтверждением администратора. Им предназначены модели, базы знаний и голосовой ввод; Kubernetes MCP — отдельный доступ администратора через OIDC. Персональные Virtual Key и квоты требуют настройки: они не возникают от регистрации в WebUI. [Подключение и права доступа](docs/CHAT_AND_ACCESS.md).

<a id="setup"></a>

## Подготовка окружения

Нужны Git, Helm 3+, kubectl, jq и **yq Mike Farah v4**. На ноутбуке не нужны Python, CUDA и веса моделей.

Один Helm-чарт описывает запуск vLLM, отдельные values — настройки каждого эксперимента. В GitLab лежат чарт, профили и привязки площадки; Argo CD рендерит выбранный коммит и применяет его в GPU-кластер.

![Публичные примеры переносятся в GitLab; Argo CD управляющего кластера применяет выбранный коммит в GPU-кластер](assets/12-gitops.svg)

[Однократная подготовка GitOps](docs/GITOPS.md): готовые PVC, подстановка DeviceClass, регистрация двух Application. Драйвер, DRA и режим MIG на A30 должны работать **до** начала занятия.

Дальнейшие команды выполняются из вашей репы **k8s-config**. Замените контексты и каталог своими:

```bash
export ARGO_CONTEXT=management
export GPU_CONTEXT=gpu-cluster
export ARGO_NAMESPACE=argocd
export DEMO_DIR=argo-projects/gpu-cluster/hardfest-demo

kubectl config get-contexts
kubectl --context "$GPU_CONTEXT" get nodes
kubectl --context "$GPU_CONTEXT" get deviceclasses
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pvc,pods,resourceclaims
```

Для запуска нужны три части:

| Файл | Назначение |
| --- | --- |
| [charts/vllm-runtime](charts/vllm-runtime/README.md) | Deployment, ConfigMap, DRA-заявка, Service и NetworkPolicy |
| [values/gemma-b.yaml](values/gemma-b.yaml) | Параметры vLLM, GPU и CPU/RAM; `replicaCount` |
| `site/gemma.yaml` в вашей GitLab-репе | Нода, DeviceClass, существующий PVC и доступ Bifrost; [пример](examples/site-gemma.yaml) |

В примерах `replicaCount: 0`, autosync выключен. DeviceClass берём у GPUClass/GPUPool-контроллера, не создаём вручную. [Требования к RAM и ноде](docs/SETUP.md).

До нагрузки откройте **AI Inference / Live performance** в мониторинге Console. [Манифесты дашборда и сбор метрик](docs/OBSERVABILITY.md) доставляются тем же Argo CD.

<a id="latency"></a>

## 1. Где теряется время

![Временная шкала: очередь, обработка входа, первый токен и начало видимого ответа](assets/02-latency.svg)

Документ сначала проходит **prefill** — обработку входа. Затем начинается **decode** — генерация продолжения. При нехватке памяти или слотов обработки запрос стоит в очереди.

TTFT — время от отправки до первого токена, не обязательно первого слова итогового ответа: перед ним может идти рассуждение. Очередь и prefill смотрим отдельно. Вычитание p95 очереди из p95 TTFT не даёт p95 prefill.

<a id="compute"></a>

![Чанкирование позволяет чередовать порции длинного входа с генерацией уже работающих запросов](assets/06-scheduler.svg)

`max-model-len` ограничивает длину **одной истории вместе с ответом**, `max-num-seqs` — число обрабатываемых последовательностей, `max-num-batched-tokens` — общий бюджет токенов одного шага.

Chunked prefill меняет планирование длинного входа. CUDA graphs сокращают накладные расходы запуска вычислений, но сами занимают видеопамять.

<a id="memory"></a>

## 2. Сколько памяти нужно Gemma

KV-кэш хранит ключи и значения предыдущих токенов, чтобы attention не вычислял их заново. При GQA несколько голов запросов используют общие K/V. Память считаем по **KV-головам**, не по всем головам внимания.

У нашей Gemma 10 слоёв полного внимания и 50 локального. Локальным слоям достаточно окна до 1024 токенов.

![Формула KV-памяти Gemma: полное и локальное внимание, два массива K/V и размер элемента BF16 или FP8](assets/11-gemma-kv.svg)

Это полезные K/V одной истории. Округление блоков, рабочие буферы и способ выделения памяти движком считаются отдельно. Удвоение контекста не удваивает память весов.

![Расчёт памяти одной истории Gemma при 64K, 128K и 256K в BF16 и FP8; замер весов 57,91 GiB](assets/03-memory.svg)

Расчёт **256K не означает, что такой профиль запущен**. Основное сравнение — 64K, опыт вместимости — 128K. Размер фактического KV-пула проверяем в логах после запуска:

```bash
for SLOT in a b; do
  kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs "deployment/hf-gemma-$SLOT" |
    grep -E 'model loading|KV cache|GPU KV|Maximum concurrency|CUDA graph'
done
```

[Выкладка и бюджет RAM](docs/MEMORY_BUDGET.md); [расчёт GPT-OSS из исходной презентации](docs/chapters/01-memory.md) оставлен отдельным примером.

<a id="ab"></a>

## 3. Gemma A — Base и Gemma B — Tune

![Конфигурации при одинаковом окне 64K: у A BF16 KV без кэша префикса и графов, у B FP8 KV с ними](assets/04-ab.svg)

A намеренно отключает prefix cache и CUDA graphs. Это **не настройки по умолчанию vLLM 0.30 и не запуск старой версии**. Чанкирование 4096 оставлено у обеих реплик: без него baseline не вместил выбранное окно. Веса в RAM не выгружаются.

У B также другой attention backend. Измеряем совместный эффект настроек; приписывать весь выигрыш одному флагу нельзя. Префиксное кеширование полезно прежде всего при повторяющихся входах.

Посмотрите различия и включите реплики в Git:

```bash
diff -u \
  <(yq '.vllm' "$DEMO_DIR/values/gemma-a.yaml") \
  <(yq '.vllm' "$DEMO_DIR/values/gemma-b.yaml")

yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-a.yaml"
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-b.yaml"

set -o pipefail
for SLOT in a b; do
  helm template "hf-gemma-$SLOT" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
    -f "$DEMO_DIR/values/gemma-$SLOT.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
    kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f - || exit 1
done
git diff -- "$DEMO_DIR"
git add -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git diff --cached --check
git commit -S -s -m "Start Gemma A and B at 64K"
git push
```

У `diff` код 1 означает найденные различия. Примените **отправленный коммит**:

```bash
REVISION=$(git rev-parse HEAD)
for APP in hardfest-gemma-a hardfest-gemma-b; do
  kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
    --type merge -p "$(jq -nc --arg rev "$REVISION" '{operation:{sync:{revision:$rev,prune:false}}}')"
done
```

Дождитесь завершения операции в Argo и совпадения её revision с `$REVISION`. Прежний Ready Pod не подтверждает новый sync.

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications hardfest-gemma-a hardfest-gemma-b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-a --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
```

### Нагрузка на длинном входе

Каждый запрос получает 32 768 входных и 2048 выходных токенов. Клиент запускается внутри контейнера A и обращается к обоим Service по очереди:

```bash
for SLOT in a b; do
  kubectl --context "$GPU_CONTEXT" -n hardfest-demo exec deployment/hf-gemma-a -- \
    vllm bench serve \
      --backend vllm \
      --base-url "http://hf-gemma-$SLOT.hardfest-demo.svc.cluster.local:8000" \
      --endpoint /v1/completions \
      --model gemma-4-31b --tokenizer /models/gemma \
      --dataset-name random --seed 42 \
      --random-input-len 32768 --random-output-len 2048 --random-range-ratio 0 \
      --num-prompts 8 --max-concurrency 4 --ignore-eos --temperature 0 \
      --percentile-metrics ttft,tpot,itl --metric-percentiles 95,99 \
      --save-result --result-dir /runtime/bench --result-filename "$SLOT.json"
done
```

Это синтетическая пробная серия, не проверка качества и не устойчивый p95. CLI делает предварительный запрос: серию нельзя целиком называть холодной. Для итоговых замеров клиент выносят на CPU-ноду и повторяют тест не менее трёх раз в одинаковых условиях. [Методика и выгрузка JSON до перезапуска Pod](labs/01-ab.md).

В WebUI два имени должны вести к **разным** Service, без балансировки A/B и внешнего кэша ответов Bifrost. На графиках сравнивайте сервисы: `model_name` у них одинаковый.

<a id="platform"></a>

## 4. Тот же запуск через AI Inference

![Модель и рецепт преобразуются в план, DRA-заявку и Pod; проверяется вся цепочка до ответа API](assets/09-platform.svg)

Этот этап идёт **до** расширения B: платформенный и ручной запуски сравниваем при одинаковых 64K.

Используйте рецепт Gemma 64K: он переносит настройки ручного B в AI Inference. Для расширения контекста, KV-кэша в RAM, Gemma assistant и Qwen TP2 с MTP предусмотрены отдельные рецепты. [Состав рецептов и порядок запуска](labs/04-deckhouse.md).

Остановите A: `replicaCount: 0` в `values/gemma-a.yaml`, commit/push/sync. Создайте в Console сервис `hf-platform-gemma`: те же веса, один H100, 64K, стратегия Throughput и рецепт Gemma 64K. Helm-чарт обслуживает ручной эксперимент; платформенный Deployment создаёт AI Inference.

Откройте сформированный план: runtime, FP8 KV, prefix cache, чанкирование и CUDA graphs заданы рецептом; веса остаются на GPU. Сопоставьте параметры с ручным B, повторите прежнюю нагрузку и подключите новый Service отдельным маршрутом Bifrost.

<a id="ram"></a>

## 5. 128K и KV-кэш в RAM

Теперь проверяем большую вместимость и сохранение истории между обращениями, а не продолжаем таблицу ускорения A/B.

На VM со 128 GiB RAM остановите A через Git. Если вместо него работает `hf-platform-gemma`, удалите **только этот InferenceService** через Console. Дождитесь освобождения Pod и заявки; данные моделей сохраняются.

### Сначала только 128K

В values B поменяйте `max-model-len` на `131072`:

```bash
yq -i '.vllm.max-model-len = 131072' "$DEMO_DIR/values/gemma-b.yaml"
```

Чарт сам обновляет checksum конфигурации: изменение шаблона Pod вызывает перезапуск. Выполните commit/push/sync для того же Application B. [Полный профиль B 128K](values/gemma-b-128k.yaml) нужен для сверки, не для второго приложения.

### Затем 32 GiB KV в RAM

В [профиле B с offload](values/gemma-b-ram.yaml) эти параметры находятся в `vllm`:

```yaml
max-model-len: 131072
kv-transfer-config:
  kv_connector: OffloadingConnector
  kv_role: kv_both
  kv_connector_extra_config:
    cpu_bytes_to_use: 34359738368
```

Переключите активный профиль B целиком, вместе с лимитами памяти:

```bash
cp "$DEMO_DIR/values/gemma-b-ram.yaml" "$DEMO_DIR/values/gemma-b.yaml"
yq -i '.replicaCount = 1' "$DEMO_DIR/values/gemma-b.yaml"
```

Выполните проверку, commit/push/sync того же Application B. Привязки остаются в `site/gemma.yaml`. Профиль задаёт RAM request 56 GiB, limit 80 GiB, `/dev/shm` 40 GiB; checksum рассчитывает чарт. 32 GiB offload уже входят в лимиты — повторно не прибавляются.

![Обработать документ, вытеснить его KV из GPU другими документами и загрузить сохранённые блоки из RAM](assets/05-kv-ram.svg)

Повторите документ после других длинных документов. Если первый ещё в GPU, быстрый повтор доказывает только prefix cache. Для offload нужны **чтение из RAM и отсутствие локальных GPU hits**. [Запросы и счётчики](labs/02-kv-ram.md).

В сохранённом опыте восстановлено **2,89 GiB**: 65 504 токена из RAM, локальных попаданий — 0. TTFT первого входа — **57,29 с**, повтора после вытеснения — **1,47 с**. Это два отдельных запроса, **не p95 и не результат A/B**. [Исходные измерения](results/kv-ram/README.md).

RAM не заменяет HBM при вычислении: рабочие блоки KV должны вернуться на GPU. Выгрузка KV и выгрузка весов — разные механизмы; `cpu-offload-gb` здесь не используется.

<a id="placement"></a>

## 6. Динамический MIG и MPS

![Геометрия A30: отдельный MIG для эмбеддера, MPS внутри другого MIG и свободная доля карты](assets/08-mig-mps.svg)

Режим MIG включён заранее. **Разделы не преднарезаны конфигом карты**: GPUClass/GPUPool создаёт классы, ResourceClaim запрашивает профиль, DRA-драйвер готовит раздел.

MIG выделяет аппаратную часть памяти и вычислительных ресурсов. MPS позволяет процессам работать внутри одного GPU или MIG-раздела. Time-slicing только чередует выполнение и не увеличивает память.

Подготовлены values для [эмбеддера в MIG](values/embed-mig.yaml) и [эмбеддера с MPS](values/embed-mps.yaml). Они используют тот же чарт, но отдельные [Application](argocd/embed-mig.yaml) и [Application MPS](argocd/embed-mps.yaml), собственные site-values и destination кластера с A30.

```bash
export MIG_CONTEXT=cluster-with-a30
kubectl --context "$MIG_CONTEXT" get deviceclasses
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaims
```

У MPS-профиля квота 25% active threads и 4 GiB памяти. В vLLM отдельно ограничен VRAM-бюджет: CUDA может показывать полный объём MIG, а не квоту клиента. 25% MPS не означают четверть скорости.

После commit/push/sync и Ready Pod откройте доступ к API:

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo port-forward svc/hf-embed-mig 18003:8000
```

В другом терминале:

```bash
curl --fail --max-time 30 http://127.0.0.1:18003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embedding","input":["Динамическое разделение GPU","Очередь инференса"]}'
```

Ответ содержит два вектора. Посмотрите устройства в ResourceClaim, остановите сервис через Git и проверьте освобождение заявки. Второй клиент MPS и реранкер на схеме — следующий опыт: их совместный запуск пока не подтверждён. [Профили, квоты и освобождение геометрии](labs/05-mig-mps.md).

<a id="tp2"></a>

## Бонус. Qwen на двух H100

![TP2: веса одной модели разделены между двумя H100, процессы обмениваются данными через NVLink и NCCL](assets/10-tp2.svg)

Qwen3.8-Flash-Next-NVFP4 занимает обе H100 одной ноды. TP=2 — **одна модель на двух картах**, не две реплики. PP делит слои, TP делит вычисления внутри слоёв; выбор зависит от архитектуры, памяти и межкарточного обмена.

До остановки Gemma выполните [проверки GPU, NVLink/NCCL и runtime](labs/06-tp2.md). При неисправимых ECC этот этап не запускается.

После проверки освободите обе карты и создайте сервис через AI Inference с рецептом Qwen TP2: две H100, MTP, prefix cache, chunked prefill, CUDA graphs и заданный в рецепте бюджет KV-кэша в RAM. Откройте план и настройки движка; затем подключите сервис к тому же Bifrost и Open WebUI. [Порядок запуска и проверки](labs/06-tp2.md).

Повышайте нагрузку ступенями: 1, 2, 4, 8, 16, 32, 50 запросов одновременно. Допустимое число сессий определяется ошибками, очередью и приемлемой задержкой, а не только отсутствием OOM.

<a id="speculation"></a>

## Дополнительно. Черновая генерация

![Черновик предлагает токены, основная модель принимает совпавшее начало и исправляет первое отклонение](assets/07-speculation.svg)

Gemma assistant — эксперимент вне основного A/B. Сравниваем B без черновика и с ним, **без RAM-offload**, при одинаковом входе и состоянии кэша. [Профиль и проверка](labs/03-speculation.md).

Важны и принятые токены, и время самого черновика. Высокий acceptance не гарантирует ускорения. Пока нет подтверждённой генерации на выбранном runtime, работающий B не заменяем кандидатом.

<a id="cleanup"></a>

## Остановка

Сначала выгрузите результаты из `/runtime`: это временный каталог Pod. Затем остановите ручные Gemma через Git:

```bash
yq -i '.replicaCount = 0' "$DEMO_DIR/values/gemma-a.yaml"
yq -i '.replicaCount = 0' "$DEMO_DIR/values/gemma-b.yaml"
git add -- "$DEMO_DIR/values/gemma-a.yaml" "$DEMO_DIR/values/gemma-b.yaml"
git commit -S -s -m "Stop HardFest Gemma workloads"
git push
```

Синхронизируйте коммит по блоку из раздела A/B. Для других ручных сервисов повторите действие в их values. Платформенные сервисы удаляйте через InferenceService, не через дочерний Deployment.

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims,pvc
```

Проверьте также кластер A30, если выполняли этап MIG/MPS. Namespace, PVC, веса, GPUClass/GPUPool и чужие заявки не удаляются. Освобождение claim и удаление MIG-раздела могут происходить не одновременно — это определяется политикой драйвера.

<a id="results"></a>

## Результаты

Сохраните SHA коммита, параметры vLLM и исходные JSON. В [отчёт](results/REPORT.template.md) отдельно входят сравнение A/B, опыт 128K и возврат KV из RAM. Нельзя собирать одну таблицу ускорения из разных входов, окон и числа клиентов.

Для своего стенда: [подготовка](docs/SETUP.md), [GitOps](docs/GITOPS.md), [метрики](docs/MEASUREMENTS.md), [неполадки](docs/TROUBLESHOOTING.md). Теория из презентации сохранена в [дополнительных главах](docs/THEORY.md).

[К содержанию](#contents)
