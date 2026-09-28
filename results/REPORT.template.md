# Отчёт прогона: заполнить после измерения

Статус: НЕ ЗАПОЛНЕНО. Пустые поля — не нулевые значения.

## Идентичность

- Run ID / время / версия Git:
- Image digest, driver, CUDA, GPU SKU/VRAM и topology:
- Model/tokenizer revisions, precision weights/KV:
- Dataset SHA256, реальные input/output lengths, sampling:
- Context / max-num-seqs / batched tokens / graphs / CPU cache / speculation:
- Endpoint direct/gateway, cache state, warmup:
- Raw results и server metrics/trace (обезличенные):

## Сравнение

| Метрика | A | B | Условия/единицы |
| --- | --- | --- | --- |
| Успешно / errors | — | — | Число запросов |
| Client TTFT mean / sample p95 | — | — | Секунды |
| Server queue mean / p95 | — | — | Секунды, серия завершённых запросов |
| Server prefill mean / p95 | — | — | Секунды |
| ITL / TPOT | — | — | Указать способ расчёта |
| Output throughput | — | — | Токенов/с всего сервиса |
| E2E | — | — | Секунды |
| GPU/CPU cache hit и reload evidence | — | — | Счётчики, интервал |
| Preemptions / OOM | — | — | Число |
| Speculation acceptance / per-position | — | — | Указать denominator |

## Вывод

Что ускорилось/ухудшилось? Отдельно вместимость, latency, throughput и качество. Какие утверждения подтверждены, какие остаются гипотезами? Пакет параметров не даёт отдельный вклад каждого флага без ablation.

Не переносить сравнение на другое окно, модель или concurrency без нового прогона.
