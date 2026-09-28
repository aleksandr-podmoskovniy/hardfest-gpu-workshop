# Справочник команд

Выполняйте команды из каталога репозитория. Нужен Python 3.10+, а для работы с кластером — kubectl. Скрипт берёт kubeconfig, контекст и адрес API из `.local/site.json`; текущий контекст kubectl не используется.

## Скачать материалы

```bash
git clone https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop.git
cd hardfest-gpu-workshop
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
python3 scripts/check_docs.py
```

Если `.local/site.json` ещё нет, создайте его:

```bash
python3 scripts/hf.py init-site
```

Заполните [параметры стенда](SETUP.md) в созданном файле. Токены и пароли туда не добавляйте. Существующий файл команда не перезаписывает.

## Посмотреть — без изменений

```bash
python3 scripts/hf.py preflight
python3 scripts/hf.py get nodes
python3 scripts/hf.py get deviceclasses
python3 scripts/hf.py get resourceclaims
python3 scripts/hf.py render a
python3 scripts/hf.py diff a b-tuned
python3 scripts/hf.py diff b-tuned b-cache
python3 scripts/hf.py diff b-tuned b-spec
```

`render` и `diff` работают локально. Чтобы посмотреть пример без настройки стенда, укажите `--site config/site.example.json` перед именем подкоманды.

## Запустить конфигурацию

```bash
python3 scripts/hf.py apply a --ack
python3 scripts/hf.py start a --ack
python3 scripts/hf.py logs a
python3 scripts/hf.py model-info a
python3 scripts/hf.py snapshot a --out .local/runs/a-before.json
```

`apply` проверяет принадлежность ресурсов, выполняет серверную проверку манифеста и создаёт Deployment с нулём реплик. `start` проверяет ноду, увеличивает число реплик до одной и ждёт готовности до 300 секунд. При таймауте Pod остаётся в кластере — проверьте его состояние и логи.

`snapshot` сохраняет Deployment, Pod, идентификаторы образов, профиль и ResourceClaim в `.local/`, не читая Secret. Для следующего прогона выберите другое имя файла: снимки не перезаписываются.

## Подготовить длинные запросы

После готовности A:

```bash
python3 scripts/hf.py dataset a --input-fraction 0.5 --output-tokens 2048 \
  --documents 32 --out .local/long.jsonl
```

Генератор выполняется в контейнере и использует токенизатор модели; файл JSONL сохраняется на вашем компьютере. Скачивать веса и устанавливать transformers локально не требуется. После первого запроса сверьте длину входа с `usage.prompt_tokens` в ответе сервера.

## Подключиться к API

T1:

```bash
python3 scripts/hf.py port-forward a 18001
```

T2:

```bash
python3 scripts/hf.py port-forward b-tuned 18002
```

Оставьте обе команды работать. Остальные команды выполняйте в третьем терминале, T0. После смены профиля B остановите старый `port-forward` через Ctrl+C и запустите его для нового профиля.

## Полный сравнительный замер

Перед серией перезапустите A и B или очистите их кэш проверенным способом. Дайте обоим серверам одинаковый прогрев на других документах. Затем выполните:

```bash
python3 scripts/bench.py --url http://127.0.0.1:18001 --dataset .local/long.jsonl \
  --concurrency 8 --requests 32 --cold --metrics --label a-cold --out-dir results/raw/ab
python3 scripts/bench.py --url http://127.0.0.1:18002 --dataset .local/long.jsonl \
  --concurrency 8 --requests 32 --cold --metrics --label b-cold --out-dir results/raw/ab
python3 scripts/report.py --directory results/raw/ab
```

На длинных входах серия может занять много времени. `--cold` запрещает повтор строк набора, но не очищает кэш сервера. `--metrics` сохраняет `/metrics` до и после серии и вычисляет среднее время очереди и prefill по разнице счётчиков. Если метрик нет или счётчики сбросились, среднее не вычисляется. Во время замера на этих API не должно быть посторонних запросов.

## Смена B и завершение

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-cache --ack
python3 scripts/hf.py start b-cache --ack
```

В T2 заново выполните `python3 scripts/hf.py port-forward b-cache 18002`. Для перехода к `b-spec` повторите тот же порядок: остановка, применение, запуск. При удалении Pod теряется кэш компиляции из `emptyDir`; загрузку и компиляцию измеряйте отдельно от обработки запросов.

В конце остановите созданные конфигурации. Для B достаточно одной команды `stop`: все его профили используют общий Deployment.

```bash
python3 scripts/hf.py stop a --ack
python3 scripts/hf.py stop b-spec --ack
python3 scripts/hf.py get resourceclaims
```

Если запускали TP2, MIG или MPS, остановите их отдельно. Для несуществующего Deployment скрипт вернёт `not found`. Созданный через Console сервис удалите по его имени в Console. Namespace, модели и PVC оставьте для следующих упражнений.
