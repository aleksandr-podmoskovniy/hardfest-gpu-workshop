# Динамический MIG и MPS поверх MIG

**Условие:** отдельная A100 с заранее включённым MIG mode. H100 A/B не перенарезаем. Никаких статических `nvidia-smi -cgi`, MIG Manager geometry или reset всей карты в ходе упражнения.

## Источник классов

GPUClass/GPUPool контроллер создаёт DeviceClass; driver удовлетворяет DRA-заявку и создаёт/освобождает MIG instances динамически. Перед запуском проверить фактическую CRD-версию и selectors. Не копировать профили A30 (`1g6gb`) на A100.

```bash
python3 scripts/hf.py get gpuclasses
# Если установленная версия использует GPUPool, вместо предыдущей команды:
python3 scripts/hf.py get gpupools
python3 scripts/hf.py get deviceclasses
python3 scripts/hf.py get resourceslices
```

В site выбрать **существующие generated** `mig_device_class` и `mps_device_class` одной допустимой геометрии A100. Числа GiB у A100 40/80 GB различаются. Compute-долю и память клиента подобрать по runtime footprint маленьких моделей, не только по весам.

Генератор использует DRA `capacity.requests.sharePercent` и `MigDeviceConfig.sharing` из ранее проверенного контракта Deckhouse-драйвера. Перенос на эту площадку требует server-side dry-run **и** аппаратной проверки. Не менять opaque config только по документации общего NVIDIA device plugin.

## Сценарий с сервисами

1. Исходная геометрия: сфотографировать пустые/занятые instances и список claims.
2. Запустить `embed-mig`: маленький Qwen embedding в отдельном MIG instance.
3. Запустить `embed-mps`, затем `rerank-mps`: два процесса внутри другого MPS-enabled MIG instance, если это подтвердил allocator.
4. Обратиться к обоим API, параллельно проверить compute-share, memory limit и фактическую нагрузку.
5. Остановить один MPS-сервис: второй должен продолжить работу.
6. Остановить второй: дождаться удаления его claim и освобождения instance.
7. Остановить embed-mig: проверить возврат геометрии к свободной ёмкости.

```bash
python3 scripts/hf.py apply embed-mig --ack
python3 scripts/hf.py start embed-mig --ack
python3 scripts/hf.py apply embed-mps --ack
python3 scripts/hf.py start embed-mps --ack
python3 scripts/hf.py apply rerank-mps --ack
python3 scripts/hf.py start rerank-mps --ack
python3 scripts/hf.py get resourceclaims
```

Reranker запуск **заблокирован по умолчанию**: нужно подтвердить model-specific conversion и `/rerank` в этом vLLM, затем записать `reranker_profile` и `reranker_config_verified` в private site. Ошибочный pooling endpoint нельзя выдавать за работающий reranker.

Для embeddings, port-forward в отдельном терминале:

```bash
python3 scripts/hf.py port-forward embed-mig 18003
```

```bash
curl --fail --max-time 30 http://127.0.0.1:18003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embedding","input":["Динамическое разделение GPU","Очередь инференса"]}'
```

MPS quota не гарантирует co-location двух claims на одном MIG UUID. После запуска сверить allocation; если allocator развёл процессы по разным instances, это не демонстрация MPS-sharing одного instance. Не решать это небезопасным ручным изменением DeviceClass.

Для уже проверенного reranker, в отдельном терминале:

```bash
python3 scripts/hf.py port-forward rerank-mps 18005
```

В основном терминале:

```bash
curl --fail --max-time 30 http://127.0.0.1:18005/rerank \
  -H 'Content-Type: application/json' \
  -d '{"model":"reranker","query":"Как освободить GPU после демо?","documents":["Остановить demo workload и дождаться освобождения его claims.","Векторный индекс хранит представления документов."],"top_n":2}'
```

Ожидаем корректную структуру scores и более релевантный первый фрагмент, а не только HTTP 200. API и conversion проверяются до выставления `reranker_config_verified`; [версионная документация scoring](https://docs.vllm.ai/en/v0.30.0/models/pooling_models/scoring/).

## Геометрия, квоты, метрики

Сравнить три источника: claims/ResourceSlice, read-only GPU topology и telemetry. DCGM может некорректно отражать динамический hotplug: не рисовать «0%» вместо отсутствующей серии. Смена геометрии считается подтверждённой по инстансам/allocations, а consumption — по достоверным metrics. Проблемы мониторинга записать отдельно.

Для проверки нехватки квоты подготовьте отдельный небольшой запрос, который по расчёту должен остаться Pending. Не превышать память тестовым OOM в общем сервисе. Квоты могут иметь дискретность: `25%` — не обещание ровно четверти throughput.

## Освобождение

```bash
python3 scripts/hf.py stop rerank-mps --ack
python3 scripts/hf.py stop embed-mps --ack
python3 scripts/hf.py stop embed-mig --ack
python3 scripts/hf.py get resourceclaims
```

Ждать GC Pod-owned claims и reconcile драйвера. Если instance остался: проверить ссылку на claim и policy retention. Не снимать finalizers вслепую. Templates сами GPU не выделяют; общие PVC/models не удаляем.

GPU-loadgen позволяет дополнительно проверить квоты на синтетической нагрузке; его код/образ фиксируется отдельно и не копируется из dirty worktree в учебник. Синтетический loadgen не заменяет live API сервиса.
