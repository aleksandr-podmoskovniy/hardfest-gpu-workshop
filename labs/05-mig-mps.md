# Динамический MIG и MPS на A30

На A30 заранее включён MIG mode, но геометрия не преднарезается статическим
MIG Manager config. GPUClass/GPUPool создаёт DeviceClass; DRA выдаёт заявки
и формирует MIG-разделы по потребности.

Подготовленные исходники: [MIG](../deploy/embed-mig/resources.yaml) и
[MPS поверх MIG](../deploy/embed-mps/resources.yaml). Эти workload по умолчанию выключены.
Их Application должен указывать на кластер с A30, не автоматически на кластер H100.

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

Перенесите каталоги в свою GitLab-репу и создайте отдельные Application.
Порядок такой же, как для Gemma: render → dry-run → commit/push → sync.
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
Оба эмбеддера на стенде уже отвечали векторами 1024; это не доказывает совместную
работу двух клиентов MPS и реранкера. Следующий клиент должен иметь собственный
проверенный профиль и достаточную квоту. Эти проверки отражены в [STATUS](../docs/STATUS.md).

Сравните одиночную и совместную нагрузку: latency, ошибки, пик памяти.
25% квоты не равны 25% скорости. Аппаратная граница изоляции — MIG.

Для освобождения изменяйте replicas через Git и sync. Проверяйте исчезновение
заявок и доступную ёмкость. Геометрия может сохраняться согласно политике драйвера;
это не повод удалять GPUClass, namespace или finalizers.
