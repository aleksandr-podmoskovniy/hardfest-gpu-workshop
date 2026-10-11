# Клиент измерений PRO 6000

Эти пять Python-файлов скопированы из **immutable ConfigMap выполненных Jobs**
Gemma capacity и matched chat 11 октября 2026. Алгоритм запросов не изменён.
[SHA256](SHA256SUMS) фиксирует опубликованную копию. Клиент Qwen использовал
тот же интерфейс capacity-пробы; точный архив его runtime-профиля не найден.

Нужны Linux, Python 3 и прямой доступ к подготовленным vLLM через закрытую сеть
или локальный port-forward. Capacity-транспорт использует Linux TCP keepalive;
на macOS напрямую не запускается. Внешний публичный HTTP для этих проб не открывается.
Клиент не реализует авторизацию Bifrost и не проверяет персональный чат.

## 1. Подключение к A и B

Каждый port-forward выполняется в отдельном терминале на машине Linux-клиента.
Никаких правок workloads эти команды не делают:

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/selectel-gemma-a 18001:8000
```

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/selectel-gemma-b 18002:8000
```

В третьем терминале, из корня репозитория:

```bash
export PROBE=examples/pro6000-load-client
export URL_A=http://127.0.0.1:18001 URL_B=http://127.0.0.1:18002
mkdir -p .local/pro6000-run
curl --fail "$URL_A/v1/models"
curl --fail "$URL_B/v1/models"
```

Port-forward добавляет свой транспорт в задержку. Исторический прогон выполнялся
из отдельного CPU-клиента в кластере; для сопоставимых чисел нужен такой же путь.
`/tokenize`, `/metrics` и API генерации должны принадлежать одному runtime.
На моделях не должно быть чужой нагрузки.

## 2. Предельное окно: десять запросов с выходом 32K

**Это многочасовая нагрузка**, не команда для запуска во время выступления.
Проверьте память, выбранный профиль и SSE heartbeat 15 секунд заранее.
Полный дедлайн — 12 часов, таймаут отсутствия данных в потоке — 90 секунд.
Не уменьшайте окно или выход при ошибке, продолжая называть прогон предельным.

Первая команда — A, вторая — B. Их можно выполнять одновременно в двух
терминалах, как в сохранённом опыте; оба используют одну RAM/CPU ноды.
Файл результата не перезаписывается. При повторе нужен новый путь отчёта.

```bash
python3 "$PROBE/inference-probe.py" \
  --url "$URL_A" --model selectel-gemma-a \
  --input-tokens 134752 --output-tokens 32768 --sessions 10 \
  --request-timeout 43200 --stream-idle-timeout 90 \
  --report .local/pro6000-run/capacity-a.json
```

```bash
python3 "$PROBE/inference-probe.py" \
  --url "$URL_B" --model selectel-gemma-b \
  --input-tokens 229376 --output-tokens 32768 --sessions 10 \
  --request-timeout 43200 --stream-idle-timeout 90 \
  --report .local/pro6000-run/capacity-b.json
```

В обоих случаях `ignore_eos=true`: выход принудительно заполняет 32K.
Критерии — десять уникальных `ok=true`, `[DONE]`, `finish_reason=length`,
точные входные/выходные счётчики. Окно A 167520 получено auto-fit только
на измеренной конфигурации; при другом плане памяти сначала фиксируется её новый предел.

Qwen запускается отдельно, после освобождения обеих GPU, с входом **229376**,
выходом **32768**, `--sessions 10` и его реальным `served-model-name` из `/v1/models`.
До повторения нужен восстановленный и сверенный runtime-профиль с TP2/offload160;
одного изменения URL в команде недостаточно.

## 3. Сопоставимый чат A/B

Этот тест не требует повторять предельную серию на каждом выступлении.
Для нового отчёта передайте завершённые capacity-отчёты **той же конфигурации**.
Без `--execute` скрипт только проверяет их и печатает план:

```bash
python3 "$PROBE/chat_comparison_probe.py" \
  --url-a "$URL_A" --url-b "$URL_B" \
  --capacity-report-a .local/pro6000-run/capacity-a.json \
  --capacity-report-b .local/pro6000-run/capacity-b.json \
  --report .local/pro6000-run/matched.json
```

Реальная нагрузка ограничивается внешним дедлайном 7200 секунд:

```bash
python3 "$PROBE/run_probe_with_hold.py" --timeout 7200 --hold 0 -- \
  python3 "$PROBE/chat_comparison_probe.py" \
  --url-a "$URL_A" --url-b "$URL_B" \
  --capacity-report-a .local/pro6000-run/capacity-a.json \
  --capacity-report-b .local/pro6000-run/capacity-b.json \
  --report .local/pro6000-run/matched.json --execute
```

Шесть волн по десять запросов: короткая A/B, длинная A/B, повтор длинной A/B.
Длинный вход калибруется около 98K; ID токенов проверяются на равенство A/B.
В каждой волне общий префикс. Выходной лимит 32K, обычный EOS, короткий
контрольный ответ `ORBIT-42 … ГОТОВО`. Скрипт прекращает сравнение при ошибке.
Это проверка завершения и задержки, не оценка качества свободного ответа.

## 4. Отдельно проверить возврат KV из RAM

После capacity B, пока тот же runtime и его кеш сохранены:

```bash
python3 "$PROBE/run_probe_with_hold.py" --timeout 7200 --hold 0 -- \
  python3 "$PROBE/verify_offload_replay.py" \
  --url "$URL_B" --capacity-report .local/pro6000-run/capacity-b.json \
  --report .local/pro6000-run/restore.json --execute
```

Проба отправляет не более трёх кандидатов с одним выходным токеном и требует
роста `CPU_to_GPU`. После перезапуска движка прежнего RAM-кеша нет:
старый отчёт не доказывает восстановление в новом процессе.

## Как читать результат

`exit 0` без готового отчёта не засчитывается. Незавершённые потоки,
исключения и watchdog-abort остаются ошибками. Метрики движка снимаются
на том же интервале; [готовый пакет](../../results/pro6000/README.md)
показывает формат публикации. Ключи, адреса и полные внутренние метрики
перед публикацией проверяются отдельно.
