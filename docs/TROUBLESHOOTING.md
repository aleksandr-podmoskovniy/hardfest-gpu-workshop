# Диагностика без разрушительных «лечений»

| Симптом | Проверка | Чего не делать автоматически |
| --- | --- | --- |
| API timeout/TLS | VPN, маршрут, expected_server, срок SSO | Менять глобальный kubeconfig или отключать TLS всем кластерам |
| Pending | Events, nodeSelector/taints, generated DeviceClass, free claims | Снимать неизвестный cordon, удалять чужие claims |
| ImagePullBackOff | Digest и наличие namespaced pull Secret | Печатать credentials в терминал |
| Model path missing | PVC/subPath, digest и index файлов | Повторно качать всю модель в root PVC |
| CUDA unavailable | Allocation UUID, driver binding, CUDA внутри Pod | Путать запись inventory с работающей GPU |
| OOM при старте | Weights/workspace/graphs/KV, общая HBM, context | Незаметно включать CPU weight offload |
| Host OOM с cache | Pinned RAM и cgroup limit, другие Pod | Считать 64 GiB connector единственным потребителем RAM |
| Результат A/B одинаков | Effective defaults, workload, cache state | Специально выключать baseline до нереалистичной конфигурации |
| Длинный TTFT | Разделить queue/prefill/transport; проверить preemptions | Вычитать p95 queue из p95 TTFT |
| Нет CPU cache hit | Убедиться в store/load и GPU eviction | Считать повторный быстрый ответ доказательством RAM |
| Speculation хуже | Acceptance, стоимость draft/verify, batch size | Повышать speculative tokens без измерений |
| MPS на разных instances | DRA allocation и MIG UUID | Обещать co-location по одинаковому имени класса |
| DCGM пуст после MIG | Topology vs metric series, label churn, hotplug | Показывать отсутствие данных как 0% использования |
| После stop остался MIG | Pod/claim ownership, retention/reconcile | Удалять finalizers или reset всей GPU |

```bash
python3 scripts/hf.py get events
python3 scripts/hf.py get pods
python3 scripts/hf.py get resourceclaims
python3 scripts/hf.py logs b-tuned
```

`apply` отказывается менять профиль работающего Deployment: сначала явный `stop`. После остановки и смены профиля старый port-forward нужно запустить заново. Для изменения immutable DRA config renderer создаёт новое имя template по hash, а не форсирует удаление занятой заявки. Неиспользуемые templates можно убрать позже после проверки владельцев; они не занимают GPU сами по себе.
