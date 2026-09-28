# Справочник команд

Все команды выполнять из корня checkout. Python 3.10+, kubectl для живых действий. Wrapper использует kubeconfig/context/API из `.local/site.json`, а не глобальный context.

## Новый checkout

```bash
git clone https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop.git
cd hardfest-gpu-workshop
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
python3 scripts/check_docs.py
```

Только в **новом** checkout:

```bash
python3 scripts/hf.py init-site
```

Команда создаёт личный шаблон без credentials, не выбирает молча активный кластер и не перезаписывает существующий файл. Один раз заполнить [поля site](SETUP.md). Если файл уже создан и заполнен, пропустите этот шаг.

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

`render` и `diff` не подключаются к кластеру. Для offline-примера указать `--site config/site.example.json` до подкоманды.

## Жизненный цикл стадии

```bash
python3 scripts/hf.py apply a --ack
python3 scripts/hf.py start a --ack
python3 scripts/hf.py logs a
python3 scripts/hf.py model-info a
python3 scripts/hf.py snapshot a --out .local/runs/a-before.json
```

`apply`: ownership → server dry-run → replicas=0. `start`: Ready/cordon → scale 1 → ожидание до 300 s. Таймаут не означает успешный старт или автоматическое удаление. Снимок содержит deployment/pods/imageIDs/profile/claims, не читает Secret и сохраняется приватно. Следующему прогону дать новое имя файла.

## Подготовить длинный dataset внутри runtime

После Ready A:

```bash
python3 scripts/hf.py dataset a --input-fraction 0.5 --output-tokens 2048 \
  --documents 32 --out .local/long.jsonl
```

Генератор передаётся в контейнер через stdin, читает его tokenizer; JSONL сохраняется на ноутбуке. Не нужно скачивать туда веса/CUDA/transformers или вручную угадывать mount path. Сверить server `usage.prompt_tokens` первым запросом.

## Два занятых терминала

T1:

```bash
python3 scripts/hf.py port-forward a 18001
```

T2:

```bash
python3 scripts/hf.py port-forward b-tuned 18002
```

Они остаются работать до Ctrl+C. Остальное выполнять в T0; после смены B переподключить T2 к новому stage.

## Полный сравнительный замер

Свежие A/B engine, одинаковые условия; не выполнять тот же cold дважды без нового cache-state:

```bash
python3 scripts/bench.py --url http://127.0.0.1:18001 --dataset .local/long.jsonl \
  --concurrency 8 --requests 32 --cold --metrics --label a-cold --out-dir results/raw/ab
python3 scripts/bench.py --url http://127.0.0.1:18002 --dataset .local/long.jsonl \
  --concurrency 8 --requests 32 --cold --metrics --label b-cold --out-dir results/raw/ab
python3 scripts/report.py --directory results/raw/ab
```

Может идти долго. `--cold` не сбрасывает server cache, только запрещает повтор строк. `--metrics` читает /metrics до/после, считает mean queue/prefill из counters и сохраняет raw snapshots. Нет серии/reset — нет данных, не ноль. Чужой трафик на endpoint искажает это среднее.

## Смена B и завершение

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-cache --ack
python3 scripts/hf.py start b-cache --ack
```

Переподключить T2: `python3 scripts/hf.py port-forward b-cache 18002`. Затем аналогично stop b-cache → apply b-spec → start b-spec. При перезапуске теряется EmptyDir compile-cache; время загрузки и компиляции учитывайте отдельно от времени запросов.

В конце остановить только созданные стадии. Для B достаточно одного stop — Deployment общий:

```bash
python3 scripts/hf.py stop a --ack
python3 scripts/hf.py stop b-spec --ack
python3 scripts/hf.py get resourceclaims
```

Для TP2/MIG/MPS — остановить соответствующие stage. Не созданный Deployment даст not found; это не основание выполнять delete namespace. Сервис Console удаляется отдельно по точному имени. Модели/PVC сохраняются.
