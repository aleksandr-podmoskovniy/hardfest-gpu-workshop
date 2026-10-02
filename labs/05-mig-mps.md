# Выделить MIG-разделы и разделить их через MPS

На A30 проверим две вещи: создание MIG-раздела по DRA-заявке и совместное
использование раздела несколькими MPS-клиентами. Геометрию формирует драйвер
по запросу, а не заранее подготовленный конфиг MIG Manager.

MIG задаёт аппаратную границу памяти и вычислительных ресурсов.
MPS запускает несколько процессов внутри раздела. Time-slicing чередует
выполнение, но не выделяет изолированную память.

## Перед началом

- [ ] Рабочий каталог — `k8s-config`; [GitOps](../docs/GITOPS.md) подготовлен.
- [ ] На выделенной A30 заранее включён MIG mode.
- [ ] GPUClass/GPUPool создаёт DeviceClass; вручную создавать их не требуется.
- [ ] Определены контекст кластера A30, нода, готовый PVC с эмбеддером.
- [ ] Чужие заявки на этой карте проверены; изменения не затрагивают их.

```bash
export MIG_CONTEXT=REPLACE_A30_CLUSTER_CONTEXT
kubectl --context "$MIG_CONTEXT" get deviceclasses
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaims
```

Не считайте, что A30 находится в том же кластере, что H100.
У Applications эмбеддера должна быть правильная `destination`.

## 1. Подготовить привязки A30

```bash
for PROFILE in embed-mig embed-mps; do
  cp "../hardfest-gpu-workshop/examples/site-$PROFILE.yaml" "$DEMO_DIR/site/$PROFILE.yaml"
  cp "../hardfest-gpu-workshop/argocd/$PROFILE.yaml" "$DEMO_DIR/argo-app/$PROFILE.yaml"
done
```

Откройте файлы редактором:

| Файл | Что заполнить |
| --- | --- |
| `site/embed-mig.yaml` | Нода A30, созданный контроллером DeviceClass `1g6gb`, PVC и путь эмбеддера |
| `site/embed-mps.yaml` | Та же площадка, DeviceClass `2g12gb-mps-percent`, PVC и путь |
| `argo-app/embed-mig.yaml`, `argo-app/embed-mps.yaml` | Git URL, ветка, путь, AppProject и кластер A30 |
| `values/embed-mig.yaml`, `values/embed-mps.yaml` | `replicaCount: 1` |

Пути в таблице относительны `$DEMO_DIR`. Перед использованием проверьте
selectors выбранного DeviceClass, а не только его имя:

```bash
kubectl --context "$MIG_CONTEXT" get deviceclass REPLACE_GENERATED_DEVICECLASS -o yaml
```

MPS-профиль просит `sharePercent: 25` и pinned memory limit 4 GiB
через `gpu.deckhouse.io`. Это схема конкретного драйвера.
У vLLM задано `gpu-memory-utilization: 0.25`: CUDA показывает полную
память MIG, а не только квоту клиента.

## 2. Проверить и отправить оба профиля

```bash
set -o pipefail
for PROFILE in embed-mig embed-mps; do
  helm template "hf-$PROFILE" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
    -f "$DEMO_DIR/values/$PROFILE.yaml" -f "$DEMO_DIR/site/$PROFILE.yaml" |
    kubectl --context "$MIG_CONTEXT" apply --dry-run=server -f - || break
done
```

Продолжайте только после успешной проверки обоих рендеров:

```bash
git add -- "$DEMO_DIR/values/embed-mig.yaml" "$DEMO_DIR/values/embed-mps.yaml" \
  "$DEMO_DIR/site/embed-mig.yaml" "$DEMO_DIR/site/embed-mps.yaml" \
  "$DEMO_DIR/argo-app/embed-mig.yaml" "$DEMO_DIR/argo-app/embed-mps.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Add A30 MIG and MPS workloads"
git push
```

Для отдельно зарегистрированных Applications:

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/embed-mig.yaml" -f "$DEMO_DIR/argo-app/embed-mps.yaml"
REVISION=$(git rev-parse HEAD)
for APP in hardfest-embed-mig hardfest-embed-mps; do
  kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application "$APP" \
    --type merge \
    --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
done
```

Если Applications управляются родителем, сначала синхронизируйте его.
Дождитесь `Succeeded` нужной ревизии, затем:

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaims -o yaml
kubectl --context "$MIG_CONTEXT" -n hardfest-demo logs deployment/hf-embed-mps --tail=100
```

## 3. Проверить эмбеддер

В отдельном терминале:

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo port-forward svc/hf-embed-mig 18003:8000
```

В основном терминале:

```bash
curl --fail --max-time 60 http://127.0.0.1:18003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embedding","input":["Динамический MIG","Квоты MPS"]}'
```

В ответе должны быть два вектора. Аналогично проверьте `hf-embed-mps`,
переключив port-forward на этот Service.

## 4. Проверить совместный MPS

Создайте второго клиента отдельно от первого:

```bash
cp "$DEMO_DIR/values/embed-mps.yaml" "$DEMO_DIR/values/embed-mps-2.yaml"
cp "$DEMO_DIR/argo-app/embed-mps.yaml" "$DEMO_DIR/argo-app/embed-mps-2.yaml"
```

В редакторе измените только следующие поля новых файлов:

| Файл | Поле | Значение |
| --- | --- | --- |
| `values/embed-mps-2.yaml` | `fullnameOverride` | `hf-embed-mps-2` |
| `argo-app/embed-mps-2.yaml` | `metadata.name` | `hardfest-embed-mps-2` |
| Тот же Application | `spec.source.helm.releaseName` | `hf-embed-mps-2` |
| Тот же Application | Первый элемент `spec.source.helm.valueFiles` | `../../values/embed-mps-2.yaml` |

Второй value file остаётся `../../site/embed-mps.yaml`: нода, DeviceClass
и веса одинаковы. `replicaCount` нового профиля равен `1`.

```bash
helm template hf-embed-mps-2 "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
  -f "$DEMO_DIR/values/embed-mps-2.yaml" -f "$DEMO_DIR/site/embed-mps.yaml" |
  kubectl --context "$MIG_CONTEXT" apply --dry-run=server -f -
```

После успешной проверки:

```bash
git add -- "$DEMO_DIR/values/embed-mps-2.yaml" "$DEMO_DIR/argo-app/embed-mps-2.yaml"
git diff --cached --check
git diff --cached
git commit -S -s -m "Add a second MPS client on A30"
git push
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" apply \
  -f "$DEMO_DIR/argo-app/embed-mps-2.yaml"
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-embed-mps-2 \
  --type merge \
  --patch "{\"operation\":{\"sync\":{\"revision\":\"$REVISION\",\"prune\":false}}}"
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims
```

Для App-of-Apps регистрируйте новый Application через родителя.

Проверьте оба ResourceClaim и фактические устройства:

| Условие | Что должно наблюдаться |
| --- | --- |
| Два клиента делят один раздел | Одинаковый MIG UUID |
| Оба приложения работоспособны | Оба API возвращают векторы |
| Квоты применены | Выделение соответствует запросам драйвера |
| Совместная нагрузка | Записаны latency, ошибки и пик памяти каждого клиента |

Две заявки одного DeviceClass могут попасть на разные разделы — это ещё
не совместный MPS. Квота 25% также не обещает ровно 25% скорости.
Аппаратная граница изоляции остаётся на уровне MIG.

## 5. Освободить учебную нагрузку

В редакторе верните `replicaCount: 0` только у созданных в этом упражнении
профилей. Отправьте коммит и синхронизируйте их Applications тем же способом.

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims
```

## Проверка

- [ ] MIG-раздел выделен динамически по заявке, API эмбеддера работает.
- [ ] MPS-клиенты действительно делят один MIG UUID и соблюдают квоты.
- [ ] После остановки владельцев исчезли их заявки и освободилась ёмкость.

Геометрия может сохраняться согласно политике драйвера. Не удаляйте ради
этого GPUClass/GPUPool, namespace, PVC или finalizers.
Следующий этап — [Qwen TP2 на H100](06-tp2.md).
