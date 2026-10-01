# Динамический MIG и MPS поверх MIG

Для этой работы нужна отдельная A30 с заранее включённым режимом MIG. Карты H100 с репликами A/B не меняем. Во время упражнения не создаём разделы через `nvidia-smi -cgi`, не задаём статическую геометрию через MIG Manager и не сбрасываем всю карту.

## Источник классов

Контроллер GPUClass/GPUPool создаёт DeviceClass. Драйвер выполняет DRA-заявки и динамически создаёт и освобождает MIG-разделы. На A30 используем профили `1g.6gb` и `2g.12gb`. Названия созданных DeviceClass берём из статуса GPUClass, а не конструируем вручную. На A100 другие профили и размеры памяти.

Создайте `.local/site-mig.json` из примера и задайте контекст кластера с A30, `mig_node`, namespace `hardfest-demo` и PVC с моделями в этом namespace. Это отдельная привязка площадки; файл `.local/site.json` остаётся для H100. Если обе карты в одном кластере, контекст в двух файлах совпадает.

```bash
python3 scripts/hf.py --site .local/site-mig.json get gpuclasses
# Если установленная версия использует GPUPool, вместо предыдущей команды:
python3 scripts/hf.py --site .local/site-mig.json get gpupools
python3 scripts/hf.py --site .local/site-mig.json get deviceclasses
python3 scripts/hf.py --site .local/site-mig.json get physicalgpus
python3 scripts/hf.py --site .local/site-mig.json get resourceslices
```

В `.local/site-mig.json` задайте `mig_device_class` для выделенного `1g.6gb`, а `mps_device_class` — для MPS поверх `2g.12gb`. Под две роли потребуется три из четырёх долей A30; четвёртая остаётся свободной. Фактически драйвер сообщает 5952 MiB и 12032 MiB доступной памяти соответственно, поэтому названия профилей нельзя считать точным объёмом памяти приложения. Долю вычислений и память клиента подбирайте по полному потреблению запущенной модели, а не только по размеру весов.

Генератор использует DRA `capacity.requests.sharePercent` и `MigDeviceConfig.sharing` из ранее проверенной схемы Deckhouse-драйвера. На новой площадке нужны и server-side dry-run, и проверка на оборудовании. Конфигурация драйвера не взаимозаменяема с настройками обычного NVIDIA device plugin.

### Память vLLM внутри MPS

Квота MPS на память не меняет размер раздела, который vLLM использует в расчёте `gpu-memory-utilization`. На проверенной A30 CUDA показала 11,75 GiB всего и 3,96 GiB доступно при квоте 4 GiB. Значение 0,75 потребовало 8,81 GiB, и движок отказал ещё до загрузки модели.

Для этой квоты добавьте в `.local/site-mig.json`:

```json
{"mps_gpu_memory_utilization": 0.25}
```

Так бюджет движка составляет около 2,94 GiB от 11,75 GiB, оставляя запас внутри квоты 4 GiB. Это **не** доля SM из `mps_percent`: совпадение значений 25% случайно, параметры управляют разными ресурсами. На другом разделе или с другой моделью бюджет рассчитывается заново. Сначала проверяется API, затем пиковая память под нагрузкой.

## Запуск моделей поиска

1. Сохраните исходный список MIG-разделов и ResourceClaim.
2. Запустите `embed-mig`: модель эмбеддингов Qwen в отдельном MIG-разделе.
3. Запустите `embed-mps`, затем `rerank-mps`. Они должны работать внутри другого MIG-раздела через MPS; проверьте их фактическое размещение.
4. Обратитесь к обоим API. Во время запросов проверьте доли вычислений, лимиты памяти и нагрузку.
5. Остановите один MPS-сервис. Второй должен продолжить работу.
6. Остановите второй и дождитесь удаления его заявки и освобождения раздела.
7. Остановите `embed-mig` и проверьте освобождение занятой им части карты.

```bash
python3 scripts/hf.py --site .local/site-mig.json apply embed-mig --ack
python3 scripts/hf.py --site .local/site-mig.json start embed-mig --ack
python3 scripts/hf.py --site .local/site-mig.json apply embed-mps --ack
python3 scripts/hf.py --site .local/site-mig.json start embed-mps --ack
python3 scripts/hf.py --site .local/site-mig.json apply rerank-mps --ack
python3 scripts/hf.py --site .local/site-mig.json start rerank-mps --ack
python3 scripts/hf.py --site .local/site-mig.json get resourceclaims
```

Запуск реранкера по умолчанию заблокирован. Сначала нужно проверить преобразование именно этой модели и работу `/rerank` в выбранной версии vLLM. После этого задайте `reranker_profile` и `reranker_config_verified` в `.local/site-mig.json`. Доступный интерфейс pooling сам по себе не подтверждает работу реранкера.

Для запроса эмбеддингов откройте отдельный терминал и запустите проброс порта:

```bash
python3 scripts/hf.py --site .local/site-mig.json port-forward embed-mig 18003
```

```bash
curl --fail --max-time 30 http://127.0.0.1:18003/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"embedding","input":["Динамическое разделение GPU","Очередь инференса"]}'
```

Квота MPS не гарантирует, что две заявки попадут на один MIG UUID. Сверьте назначенные устройства: если процессы оказались на разных разделах, совместное использование одного раздела не проверено. Не исправляйте это ручной правкой DeviceClass в обход контроллера.

Для уже проверенного реранкера запустите проброс порта в отдельном терминале:

```bash
python3 scripts/hf.py --site .local/site-mig.json port-forward rerank-mps 18005
```

В основном терминале:

```bash
curl --fail --max-time 30 http://127.0.0.1:18005/rerank \
  -H 'Content-Type: application/json' \
  -d '{"model":"reranker","query":"Как освободить GPU после демо?","documents":["Остановить demo workload и дождаться освобождения его claims.","Векторный индекс хранит представления документов."],"top_n":2}'
```

Проверьте структуру оценок и порядок фрагментов: первый документ должен быть релевантнее второму. HTTP 200 для этого недостаточно. API и преобразование модели проверяются до включения `reranker_config_verified`; см. [документацию scoring](https://docs.vllm.ai/en/v0.30.0/models/pooling_models/scoring/).

## Геометрия, квоты, метрики

Сравните ResourceClaim и ResourceSlice, топологию GPU, прочитанную без изменений, и метрики мониторинга. DCGM может некорректно отображать динамически созданные разделы: отсутствие серии не означает загрузку 0%. Изменение геометрии подтверждается списком разделов и назначениями устройств, потребление ресурсов — проверенными метриками. Ошибки мониторинга зафиксируйте отдельно.

Для проверки нехватки квоты подготовьте отдельную небольшую заявку, которая по расчёту останется Pending. Не вызывайте нехватку памяти в общем сервисе намеренно. Квоты могут быть дискретными: `25%` не гарантирует ровно четверть пропускной способности карты.

## Освобождение

```bash
python3 scripts/hf.py --site .local/site-mig.json stop rerank-mps --ack
python3 scripts/hf.py --site .local/site-mig.json stop embed-mps --ack
python3 scripts/hf.py --site .local/site-mig.json stop embed-mig --ack
python3 scripts/hf.py --site .local/site-mig.json get resourceclaims
```

Дождитесь удаления заявок, принадлежащих Pod, и обновления состояния драйвера. Если раздел остался, проверьте его связь с заявкой и правила сохранения ресурсов. Не удаляйте finalizers без выяснения причины. ResourceClaimTemplate сам по себе GPU не занимает; общие PVC и модели удалять не нужно.

Дополнительно квоты можно проверить синтетической нагрузкой GPU-loadgen. Для воспроизводимости зафиксируйте версию кода и образа, не используйте незакоммиченные изменения. Такой тест дополняет, но не заменяет запросы к API моделей.
