# Динамический MIG и MPS на A30

На A30 заранее включён MIG mode, но геометрия не преднарезается статическим
MIG Manager config. GPUClass/GPUPool создаёт DeviceClass; DRA выдаёт заявки
и формирует MIG-разделы по потребности.

Подготовленные исходники: [MIG](../values/embed-mig.yaml) и
[MPS поверх MIG](../values/embed-mps.yaml). Эти workload по умолчанию выключены.
Их Application должен указывать на кластер с A30, не автоматически на кластер H100.

MIG задаёт аппаратную границу памяти и вычислительных ресурсов. MPS позволяет
нескольким процессам работать внутри одного раздела; time-slicing чередует
выполнение, но отдельную память не выделяет. Две заявки одного DeviceClass
могут попасть на разные разделы: совместный MPS проверяется по одному MIG UUID.

## Согласовать класс и квоту

```bash
kubectl --context "$MIG_CONTEXT" get deviceclasses
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get resourceclaims
```

Замените placeholders созданными контроллером классами для `1g6gb` и
`2g12gb-mps-percent`, нодой и PVC эмбеддера. Посмотрите их selectors,
а не угадывайте класс по названию.

MPS-пример запрашивает sharePercent 25 и задаёт 4 GiB pinned memory limit
через драйвер `gpu.deckhouse.io`. Эта схема driver-specific: dry-run и фактическое
выделение обязательны. vLLM получает gpu-memory-utilization 0,25, поскольку CUDA
показывает полную память MIG, а не только квоту клиента.

## Запуск через GitOps

Используйте тот же чарт и отдельные values; создайте [Application MIG](../argocd/embed-mig.yaml) и [Application MPS](../argocd/embed-mps.yaml). Задайте для каждого собственный site-файл с классом, нодой и PVC.
Порядок такой же, как для Gemma: diff → dry-run → commit/push → sync.
Из k8s-config подготовьте привязки и Application:

```bash
for PROFILE in embed-mig embed-mps; do
  cp "../hardfest-gpu-workshop/examples/site-$PROFILE.yaml" "$DEMO_DIR/site/$PROFILE.yaml"
  cp "../hardfest-gpu-workshop/argocd/$PROFILE.yaml" "$DEMO_DIR/argo-app/$PROFILE.yaml"
done
```

Заполните site-файлы, project, source и destination каждого Application.
Затем включите реплики и проверьте рендер:

```bash
set -o pipefail
for PROFILE in embed-mig embed-mps; do
  yq -i '.replicaCount = 1' "$DEMO_DIR/values/$PROFILE.yaml"
  helm template "hf-$PROFILE" "$DEMO_DIR/charts/vllm-runtime" -n hardfest-demo \
    -f "$DEMO_DIR/values/$PROFILE.yaml" -f "$DEMO_DIR/site/$PROFILE.yaml" |
    kubectl --context "$MIG_CONTEXT" apply --dry-run=server -f - || exit 1
done
```

Отправьте коммит, зарегистрируйте эти два Application и выполните sync по
[общему порядку](../docs/GITOPS.md#3-зарегистрировать-application-и-выполнить-sync).
После запуска:

```bash
kubectl --context "$MIG_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$MIG_CONTEXT" -n hardfest-demo logs deployment/hf-embed-mps --tail=100
kubectl --context "$MIG_CONTEXT" -n hardfest-demo port-forward svc/hf-embed-mig 18003:8000
```

В другом терминале:

```bash
curl --fail --max-time 60 http://127.0.0.1:18003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embedding","input":["Динамический MIG","Квоты MPS"]}'
```

Проверьте два вектора и реальное выделение в ResourceClaim.
Для второго клиента MPS подготовьте отдельный профиль и достаточную квоту.
Проверьте одинаковый MIG UUID у обоих клиентов и ответы обоих API.

Сравните одиночную и совместную нагрузку: latency, ошибки, пик памяти.
25% квоты не равны 25% скорости. Аппаратная граница изоляции — MIG.

Для освобождения изменяйте replicaCount в values через Git и sync. Проверяйте исчезновение
заявок и доступную ёмкость. Геометрия может сохраняться согласно политике драйвера;
это не повод удалять GPUClass, namespace или finalizers.
