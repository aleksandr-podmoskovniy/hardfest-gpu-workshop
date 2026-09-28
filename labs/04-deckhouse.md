# 4. Повтор ручного запуска через Deckhouse

**Gate:** эту лабораторную нужно дополнить экспортом манифестов установленной версии на репетиции. Сейчас нет проверенного публичного универсального CRD-манифеста. Не применять YAML из другой версии ai-inference наугад.

## До мероприятия

Записать версии Console, ai-inference, ai-models, GPU-модуля и runtime image. Проверить схемы через `kubectl api-resources` / `kubectl explain` с явным kubeconfig/context. Console из main и CRD другого релиза могут отображать несовместимые поля — Ready UI не заменяет проверку API.

В namespace `hardfest-demo` через мастер Console создать сервис `hf-platform-gemma`:

1. Источник ai-models, точная модель и revision из lock.
2. GPU-профиль от **реального** GPUClass/GPUPool, выделяющий одну H100.
3. Общее окно A/B, без weight offload.
4. Стратегия Latency как начальная для интерактивного чата. Throughput — другой набор компромиссов, не синоним «включить выгрузку KV».
5. Перед подтверждением показать предложенный план и причины размещения.

Сохранить безопасный экспорт spec и effective args в `.local/platform/`, без Secret/status/managedFields. Проверить server-side dry-run в той же версии. После рецензии переносить обезличенный пример в `manifests/platform/` с указанием версии CRD. Не заменять controller-owned Deployment руками: следующая reconciliation затрёт патч.

## На сцене

```bash
python3 scripts/hf.py stop a --ack
python3 scripts/hf.py get resourceclaims
```

Дождаться освобождения A, создать сервис из мастера. Показать цепочку Model → InferenceService → план → ResourceClaim → Pod → API.

Сопоставить:

| Ручная настройка | Что доказать у платформы |
| --- | --- |
| Model revision / tokenizer | Тот же артефакт, не похожее имя |
| Context / precision | Реальные args, capacity и effective config |
| FP8 KV / chunking / graphs | Поддерживаемые поля плана действительно дошли в engine |
| CPU KV connector | Точный connector и memory budget, если поддерживается |
| Speculation | Не обещать поддержку, если её нет в CRD/runtime |
| GPU | Claim получил нужное физическое устройство |

Прогнать тот же dataset через прямой endpoint. Если engine или часть функций другие — сравнение подписать как отдельное, не требовать одинаковых цифр.

## Перед финалом TP2

В Console удалить **только** `hf-platform-gemma`, созданный на этом занятии. Убедиться, что управляемые Pod/claims исчезли и модели/PVC остались. Пока сервис жив, обе H100 для Qwen недоступны. Наш wrapper его сам не удаляет.
