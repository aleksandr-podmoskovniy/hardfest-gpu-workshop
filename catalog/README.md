# Модели в ai-models

Каталог подготавливает веса один раз. Ручной vLLM и AI Inference могут ссылаться
на те же объекты `Model`; способ запуска движка не требует другой копии исходного
репозитория Hugging Face. Материализация для разных рабочих нагрузок может
занимать дополнительное место в хранилище доставки.

В `models.yaml` закреплены те же ревизии, что в `models.lock.json`:
Gemma, её assistant и Qwen NVFP4. Только веса занимают около 183,0 GiB;
нужен запас для метаданных, временных загрузок, уже имеющихся моделей и доставки.
Квота бакета и реальная доступная ёмкость хранилища — разные ограничения.

Добавьте `charts/model-catalog` и `catalog/models.yaml` в частный `k8s-config`.
Для отдельного Argo Application укажите Helm source на чарт, namespace
`hardfest-demo` и value file `../../catalog/models.yaml`. Autosync не требуется.
Предварительная проверка из корня репозитория:

```bash
helm lint charts/model-catalog --strict -f catalog/models.yaml
helm template hf-models charts/model-catalog -n hardfest-demo \
  -f catalog/models.yaml |
  kubectl apply --dry-run=server -f -
```

После commit/push и синхронизации этого Application:

```bash
kubectl -n hardfest-demo get models.ai.deckhouse.io
kubectl -n hardfest-demo describe model gemma-4-31b
kubectl -n hardfest-demo describe model qwen3-8-flash-next-nvfp4
```

Дождитесь `Ready`, проверьте `status.artifact.digest` и размер артефакта.
Это готовность весов, ещё не успешный запуск CUDA или инференса.

Для ручного Deployment модуль читает аннотацию в **верхнем metadata**:

```yaml
metadata:
  annotations:
    ai.deckhouse.io/model: gemma-4-31b,gemma-4-31b-assistant
```

После доставки пути для vLLM — `/data/modelcache/models/gemma-4-31b` и
`/data/modelcache/models/gemma-4-31b-assistant`. Не меняйте рабочий Deployment
на эти пути до готовности моделей и проверки доставки.

Для AI Inference источник задаётся ссылкой, без повторного URL загрузки:

```yaml
model:
  src: ai-models
  ref:
    kind: Model
    name: qwen3-8-flash-next-nvfp4
```

Источник существующего `Model` неизменяем. Для новой ревизии создайте новое имя,
проверьте его готовность и только затем переключите потребителей. Чарт запрещает
автоматическое удаление моделей через Argo prune и Helm uninstall.
Для закрытого репозитория передайте только `authSecretName`; сам Secret с ключом
`token` создавайте отдельно в namespace модели, не сохраняйте токен в Git.
