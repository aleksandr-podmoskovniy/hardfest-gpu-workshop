# 3. Gemma assistant / MTP

Используются основная Gemma и соответствующий assistant из [lock](../models.lock.json). Другая draft-модель не подставляется только потому, что тоже называется Gemma.

Сравнение: **B tuned → B spec**, без добавления CPU KV-offload в тот же переход. В [spec-профиле](../manifests/profiles/gemma-b-spec.json) `method: mtp`, локальный assistant и один speculative token. [vLLM для Gemma assistant](https://docs.vllm.ai/en/v0.30.0/features/speculative_decoding/).

```bash
python3 scripts/hf.py stop b-tuned --ack
python3 scripts/hf.py apply b-spec --ack
python3 scripts/hf.py start b-spec --ack
python3 scripts/hf.py logs b-spec
```

Если B был cache, stop b-cache тоже останавливает тот же `hf-gemma-b`. Три B-профиля — **одна** реплика, не три одновременно занимающие карту.

## Измерить

- Engine действительно загрузил assistant и включил нужный метод, а не silently fell back.
- Материалы генерации: отдельно естественный язык, код/структурированный текст; синтетический prompt не единственное свидетельство.
- Одинаковый output budget; сравнить фактическое число выходных токенов.
- По работающему exporter получить число предложенных/принятых tokens и acceptance по позициям. Не делить counters от разных серий или ranks без понимания агрегации.
- Сравнить output tok/s, TTFT, ITL, host/GPU память при concurrency 1 и 8.
- Проверить содержательность ответов и отсутствие зацикливания.

Затем можно проверить 2–3 speculative tokens **отдельными** сериями. Не выбирать максимум acceptance без учёта итоговой скорости: предложение и проверка тоже стоят времени.

Если нет выигрыша, финальный профиль остаётся tuned. Доклад не требует «победы» каждого механизма.
