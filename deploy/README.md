# Манифесты мастер-класса

Это исходники Kubernetes, не вывод закрытого генератора. В каждом каталоге:

- `configmap.yaml` — единственный источник параметров vLLM, в `data.profile.yaml`.
- `deployment.yaml` — Pod, ресурсы, подключение весов и checksum конфигурации.
- `resourceclaimtemplate.yaml` — запрос GPU через DRA.
- `service.yaml` — адрес API.
- `networkpolicy.yaml` — доступ к API.

| Каталог | Назначение |
| --- | --- |
| gemma-a | 64K, BF16 KV, без prefix cache и CUDA graphs; prefill 4096 |
| gemma-b | Те же 64K, FP8 KV, prefix cache, CUDA graphs; prefill 4096 |
| gemma-b-128k | Отдельная проверка окна 128K без RAM-кэша |
| gemma-b-ram | 128K, OffloadingConnector, 32 GiB KV в RAM, /dev/shm 40 GiB |
| gemma-b-spec | Кандидат с assistant; пока не подтверждён генерацией |
| embed-mig | Эмбеддер с заявкой на один MIG |
| embed-mps | Эмбеддер с MPS: 25% вычислительной квоты, 4 GiB памяти |
| qwen-tp2 | Кандидат на две GPU; не применять до проверки оборудования и runtime |

Все Deployment по умолчанию имеют **replicas: 0**. Образы закреплены digest.
Для Gemma A/B: CPU request 4, RAM request 24 GiB, limit 48 GiB.
Для B RAM: request 56 GiB, limit 80 GiB; 32 GiB CPU KV уже входят в эти значения.
На VM 128 GiB перед этапом с RAM остановите A.

Замените `REPLACE_...` в копии для своего стенда: ноду, **созданный контроллером** DeviceClass, PVC и каталог весов. Данные и GPUClass здесь не создаются. Namespace также должен существовать. Если на ноде есть taint, добавьте точный toleration, не универсальное разрешение всех taint.

Ничего собирать не требуется: Argo CD читает каталог с обычными YAML.
Проверка схемы после подстановки параметров площадки:

```bash
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -f "$DEMO_DIR/gemma-a"
```

Профили B — **альтернативы одного Deployment**, а не четыре одновременно работающих сервиса.
Для следующего этапа переносите его параметры из ConfigMap и нужные RAM/shm-настройки
в существующий каталог B в GitOps-репозитории. Обновите checksum конфигурации в шаблоне Pod в том же коммите —
[точная команда](../docs/GITOPS.md#5-изменение-профиля-и-откат). Application B остаётся прежним.
Не запускайте разные Application, которые владеют одним Deployment.

При изменении неизменяемой спецификации ResourceClaimTemplate задайте новое имя
и обновите ссылку в Pod. Не применяйте `--force` и не удаляйте активную заявку.

NetworkPolicy разрешает ingress только из своего namespace. Для Bifrost из другого
namespace добавьте отдельное разрешение с точными namespaceSelector **и** podSelector,
только TCP/8000. Не публикуйте vLLM без авторизации через внешний Ingress.

Перенос в GitLab, регистрация Application в управляющем кластере и переключение
профилей описаны в [GitOps](../docs/GITOPS.md). Старый `scripts/hf.py` оставлен
для воспроизведения операторских экспериментов; это не основной способ установки.
Его команды изменения ресурсов отказываются работать с объектами, помеченными Argo CD.
