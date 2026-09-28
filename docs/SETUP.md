# Подготовка площадки

## 1. Требования к окружению

Не использовать shared production GPU. Согласовать окно и владельцев нагрузки; сохранить исходные replicas/autosync вне публичного репозитория. Не снимать cordon, если неизвестно, кто и зачем его поставил.

Нужны две сопоставимые H100 для A/B, отдельная MIG-capable A100 для разделения, достаточно host RAM для 64 GiB KV-cache плюс обычной памяти процессов. Не выбирать запас RAM только по total памяти ноды: учитывать allocatable, другие Pod, cgroup limits и pinned memory.

Проверить: Kubernetes DRA API, установленный GPU-драйвер/модуль, ai-models, ai-inference и Console. Их версии **записать**, а не автоматически обновлять между сравниваемыми запусками. Изменение GPUClass → GPUPool в UI не доказывает изменение имени API.

## 2. Личная привязка

Создать `.local/site.json` на основе [примера](../config/site.example.json). Это обычный JSON, без токенов и закрытых ключей.

| Поле | Откуда взять |
| --- | --- |
| kubeconfig, context, expected_server | Явно выбранный kubeconfig; API должен совпадать точно |
| namespace | `hardfest-demo`; этот выпуск скриптов ограничен этим namespace |
| h100_node / mig_node | Реальные node names; допускается одна нода с разными GPU |
| h100_device_class | Созданный контроллером класс именно выделенных H100, без A100/VFIO |
| mig_device_class / mps_device_class | Два сгенерированных класса выбранного A100 MIG-профиля |
| image | Проверенный amd64 image по digest; reference по умолчанию в models.lock.json |
| image_pull_secrets | Имена заранее созданных Secret в namespace; не их содержимое |
| models.*.pvc / sub_path | Реальный Bound PVC и путь к корню модели внутри него |
| context_tokens | Одинаковое реально запускаемое окно A/B; 262144 — первоначальная цель |
| mps_percent / mps_memory_limit | Compute-доля и измеренный лимит памяти одного MPS-клиента |
| reranker_profile | Подтверждённые параметры conversion/pooling для данной модели и runtime |
| reranker_config_verified | Только после успешного `/rerank` и проверки правильного ранжирования |

Не подставлять PVC от другой модели. В доставке ai-models корень модели может быть `store/sha256:<digest>/model`, а не корень volume. Простой `ls` не заменяет проверку safetensors index/headers и готовности materialization.

Для H100 должны существовать **две свободные** физические DRA-единицы. Разные claims не гарантируют разные физические GPU, если DeviceClass допускает sharing. Проверить selection и allocation device IDs. Не закреплять универсальные публичные примеры на PCI-адрес конкретного стенда.

## 3. Модели и runtime

[models.lock.json](../models.lock.json) фиксирует репозитории, revisions и digest runtime. Веса не входят в Git. Получить разрешения и принять лицензии gated-моделей отдельно. Не вставлять HF token в YAML, shell history или скриншот.

В исходном эксплуатационном проекте модели уже доставлялись через ai-models. Новый учебник использует готовые PVC read-only: никакой повторной загрузки ~186 GiB файлов через `apply`. Для другого кластера сначала настроить ai-models по **его** CRD/документации либо вручную наполнить отдельные PVC. По окончании сверить revisions, файлы и пути с lock.

Размер safetensors ≠ VRAM: runtime может конвертировать представление, держать временные буферы, создавать graph pools и KV. Для NVFP4-модели на Hopper нельзя обещать native Blackwell FP4 throughput.

Прямые запуски учебника используют отдельный pinned vLLM, не меняют default image модуля ai-inference. Его поддержка моделей проверяется отдельно. Профили читаются как JSON (совместимый с YAML формат для `vllm serve --config`).

## 4. Доступ и безопасный запуск

```bash
python3 scripts/hf.py preflight
python3 scripts/hf.py render a
python3 scripts/hf.py apply a --ack
```

Последняя команда сначала проверит API и ownership, выполнит server-side dry-run и создаст Deployment **с нулём реплик**. Она не создаёт Namespace, GPUClass, Secret, PVC и не меняет драйвер. Namespace, DRA-классы и PVC должны быть готовы заранее.

`start` — отдельное действие. Оно проверяет Ready/cordon и конфликт A/B с нашим TP2. Это **не** общий admission-контроллер: чужие workloads, старые `qwen38`, module-managed сервисы и занятые claims нужно проверить вручную. Список Pod по всем namespace на GPU-ноде проверяется администратором площадки до запуска.

Сервисы ClusterIP, ingress ограничен namespace, port-forward слушает только localhost. UI/CDN/VPN/IPsec — отдельный эксплуатационный контур; этот мастер-класс не открывает неавторизованный vLLM в Интернет. Авторизованный gateway можно подключить после проверки его кэширования, лимитов и трассировки.

## 5. Чего локальная подготовка не делает

Не включает MIG mode, не меняет VFIO, не делает reset/reboot, не выключает security policy, не удаляет общие ресурсы. Привилегированная GPU-диагностика — отдельный согласованный runbook площадки. Один `kubectl apply` не лечит hardware gate.

EmptyDir runtime cache исчезает вместе с Pod, поэтому после перезапуска компиляция может повториться. Учитывайте её время отдельно от обработки запросов. При необходимости используйте отдельный проверенный persistent compile cache с ключом image/model/profile; не переиспользуйте несовместимые артефакты между версиями.
