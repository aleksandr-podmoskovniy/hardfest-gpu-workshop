# Из 52 слайдов в руководство

Перенос содержательный, не экспорт картинок. Каждая группа превращена в объяснение, упражнение, команду или проверку. Модель-зависимые числа остаются отдельным учебным примером.

| Слайды | Сюжет | Где читать / выполнять |
| --- | --- | --- |
| 1–3 | Задача, документ и вопрос, два независимых запуска | [Воркшоп](../README.md), [A/B](../labs/01-ab.md) |
| 4–5 | Фазы ответа и четыре вопроса мастер-класса | [Время ответа](chapters/02-scheduler.md) |
| 6–7 | Веса, buffers, KV | [Память](chapters/01-memory.md) |
| 8–10 | Авторегрессия, Q/K/V, сохранение K/V | [От токена к состоянию](chapters/01-memory.md) |
| 11–13 | GQA, 2 KiB на слой, формула KV | [Расчёт](chapters/01-memory.md), [калькулятор](../scripts/kv_math.py) |
| 14–17 | Config, full/sliding layers, 36 KiB | [Разбор параметров](chapters/01-memory.md) |
| 18–19 | Окно 128K и 4,5044 GiB | [Контекст и единицы](chapters/01-memory.md) |
| 20–22 | Независимые истории, упражнение на 4/8 запросов | [Упражнение](chapters/01-memory.md) |
| 23–25 | Бюджет 16,4 GiB, три полных истории | [Условный memory plan](chapters/01-memory.md) |
| 26–27 | Блоки, округления, общий префикс | [Allocator и prefix reuse](chapters/01-memory.md) |
| 28–31 | RAM-tier, 0,180 с передачи, X → Y → X | [Теория](chapters/02-scheduler.md), [эксперимент](../labs/02-kv-ram.md) |
| 32–35 | Prefill/decode, 8192 → 2048, три ограничения | [Scheduler](chapters/02-scheduler.md) |
| 36–39 | EAGLE3, принятие черновика, стоимость цикла | [Теория](chapters/03-speculation.md), [Gemma assistant](../labs/03-speculation.md) |
| 40 | Новый документ, повтор, длинный ответ | [Матрица нагрузок](MEASUREMENTS.md) |
| 41–42 | MIG, MPS, time-slicing | [Размещение](chapters/04-placement.md) |
| 43–45 | Динамический MIG, два MPS-клиента, API | [MIG/MPS lab](../labs/05-mig-mps.md) |
| 46–49 | Models → InferenceService → план → контейнер | [Deckhouse lab](../labs/04-deckhouse.md) |
| 50–51 | RAG после переключения, три запуска | [Путь RAG](chapters/04-placement.md), [отчёт](../results/REPORT.template.md) |
| 52 | Нужны ли дополнительные карты? | [Финальные вопросы](chapters/04-placement.md), [TP2](../labs/06-tp2.md) |

## Осознанные изменения

- GPT-OSS/EAGLE3 → Gemma/assistant в практике; старый memory-example оставлен математической задачей.
- bge-m3 из рисунка → закреплённые Qwen3 Embedding/Reranker на стенде.
- Учебные 96 GiB не выдаются за память обнаруженной H100.
- Добавлены client/server метрики и генерация dataset с tokenizer runtime.
- TP2 Qwen — дополнительный финал, не третья конфигурация той же модели в A/B.
- Геометрия подтверждается allocation/UUID и освобождением claims. Динамический MIG не подменён преднарезкой.

Условия запуска — в [подготовке стенда](SETUP.md), порядок сравнения — в [методике измерений](MEASUREMENTS.md).
