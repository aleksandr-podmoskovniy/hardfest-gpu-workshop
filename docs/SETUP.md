# Подготовка стенда

Основной способ развёртывания — [GitOps](GITOPS.md). Исходники — Helm-чарт и values, команды управления — Helm, Git и kubectl. Python на ноутбуке не нужен.

## 1. Предпосылки

Нужны Kubernetes/DKP с DRA, контроллер GPUClass/GPUPool и работоспособный драйвер.
Две H100 одной ноды используются сначала раздельно для Gemma A/B, затем вместе
для Qwen TP2. A30 используется отдельно для MIG.
Режим MIG согласуется и включается заранее, геометрия разделов создаётся DRA по заявкам.
Этот репозиторий не перенастраивает драйверы и не создаёт DeviceClass вручную.

В целевом namespace должны существовать PVC с весами. Модели, ревизии и digest
образа перечислены в [models.lock.json](../models.lock.json).
Проверьте лицензию модели, доступ к её весам и контрольные суммы файлов.
Не загружайте десятки гигабайт заново при каждом старте Pod.

Подготовьте обе большие модели и Gemma assistant до начала работы. Assistant
участвует во второй итерации, до платформенного запуска Gemma. Её веса и
mount должны быть доступны вместе с основными; используйте
[привязку с двумя моделями](../examples/site-gemma-assistant.yaml).
Проверьте ответ с одновременными CPU KV и assistant на закреплённом runtime.
Для Qwen нужны отдельные
PVC/subPath, рецепт AI Inference, класс физических H100 и проверенный обмен
между картами. Закреплённые файлы Qwen занимают около 123,6 GiB; это не объём
VRAM процесса. Проверьте свободное место с учётом Gemma, файлов загрузки и
кэшей компиляции. Подготовка весов не заменяет пробного запуска конечного образа.

В основную программу входит ответ Qwen через API, Bifrost и WebUI. Полную
нагрузочную серию до 50 клиентов сохраняйте для отдельного измерения, если
она не укладывается в занятие. Переход между моделями требует времени на
загрузку; заранее измерьте его на том же хранилище.

Для нагрузочного теста Qwen подготовьте отдельную CPU-ноду: закреплённый образ
клиента и токенизатор должны быть доступны до занятия. Первое скачивание большого
образа клиента не входит во время теста. [Шаблон временного Job](../examples/qwen-benchmark-job.yaml).

## 2. Параметры своего стенда

Скопируйте [чарт и values](../charts/vllm-runtime/README.md) в GitLab и замените placeholders:

| Где | Что задать |
| --- | --- |
| site-values / nodeSelector | Нода с двумя H100 |
| site-values / dra.deviceClassName | Физический DeviceClass от GPUClass/GPUPool |
| site-values / modelVolumes.claimName | Уже существующий PVC с весами |
| site-values / modelVolumes.subPath | Каталог конкретной модели внутри PVC |
| site-values / tolerations | Только нужный taint выделенной ноды |
| Application / source | Свой repoURL, ветка и каталог |
| Application / destination | Зарегистрированный в Argo GPU-кластер |

Путь внутри контейнера остаётся `/models/gemma`; он не является путём на ноутбуке.
Личные файлы kubeconfig и токены в эти YAML не входят.

## 3. RAM и GPU

У A CPU request 4, RAM request 24 GiB, limit 48 GiB. У B обеих итераций:
CPU request 4, RAM request 56 GiB, limit 80 GiB, shm 40 GiB и CPU KV 32 GiB.
На VM 128 GiB A и B работают **последовательно**. Перед платформенной Gemma
также останавливается B. Для одновременно работающих ручной и платформенной
Gemma с теми же настройками планируйте минимум 192 GiB RAM:
два лимита по 80 GiB плюс запас, который проверяется по фактическим потребителям.
После освобождения обеих H100 профиль Qwen задаёт CPU request 12,
RAM request 80 GiB, limit 104 GiB, shm 24 GiB и CPU KV 16 GiB.
Это плановые ограничения, не измеренное постоянное потребление.
[Расчёт памяти](MEMORY_BUDGET.md).

Проверьте не только установленную RAM, но и allocatable, уже выданные requests,
диск, состояние драйвера и DRA:

```bash
kubectl --context "$GPU_CONTEXT" describe node YOUR_GPU_NODE
kubectl --context "$GPU_CONTEXT" get deviceclasses
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pvc,resourceclaims,pods
```

Не начинайте при NotReady, cordon, новых неисправимых ECC или чужих заявках на GPU.
Не снимайте чужие блокировки ради запуска.

## 4. Контроль перед sync

Профили применяются в порядке: A → B с prefix cache и KV-offload →
B с настройкой prefill и assistant → Gemma через AI Inference → Qwen через AI Inference.
Этап A30 не занимает H100. Перед каждым новым запуском проверьте бюджет RAM,
а не только число свободных GPU.

```bash
yq '.vllm' "$DEMO_DIR/values/gemma-a.yaml"
set -o pipefail
helm template hf-gemma-a "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/gemma-a.yaml" -f "$DEMO_DIR/site/gemma.yaml" |
  kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f -
git diff -- "$DEMO_DIR"
```

Dry-run проверяет API-схему, не CUDA. Готовность — Ready Pod, корректные логи
инициализации, правильный GPU и успешный ответ модели.
