# Ручной vLLM через Helm

Чарт запускает Gemma A, B Cache и B Tune. Он создаёт конфигурацию vLLM,
Deployment, шаблон заявки DRA, Service и NetworkPolicy. Платформенные модели
принадлежат контроллеру AI Inference, а не этому чарту.

## Какой профиль выбрать

Профили H100, vLLM 0.31.0:

| Values | Опыт |
| --- | --- |
| [gemma-a](../../values/gemma-a.yaml) | 16K, BF16 KV, без prefix cache, chunked prefill, graphs и offload |
| [gemma-b](../../values/gemma-b.yaml) | Итерация 1: 16K, FP8 KV, prefix cache, CPU KV 32 GiB, полный prefill |
| [gemma-b-spec](../../values/gemma-b-spec.yaml) | Итерация 2: 128K, кэши первой, prefill 2048, graphs и assistant |
| [qwen-tp2](../../values/qwen-tp2.yaml) | Ручной эталон параметров; основной запуск — AI Inference |

Для RTX — отдельные [профили и маршрут](../../RTX5060.md).
Размер порции prefill не ограничивает окно: Tune обрабатывает 128K частями.

**Один профиль, затем привязка площадки.** Несколько вариантов B через `-f`
сливаются, а не заменяют друг друга. Для Tune привязка должна содержать
основную модель и assistant; списки Helm заменяет целиком.

## Проверить без кластера

Из корня публичного репозитория, без подключения к кластеру:

```bash
helm lint charts/vllm-runtime --strict -f values/gemma-b.yaml
helm template hf-gemma-b charts/vllm-runtime -n hardfest-demo -f values/gemma-b.yaml
```

Добавьте `-f site/gemma.yaml` после профиля. Примеры привязки:
[основная модель](../../examples/site-gemma-catalog.yaml),
[модель и assistant](../../examples/site-gemma-assistant-catalog.yaml).
Placeholders допустимы при нуле реплик для просмотра render, но не при запуске.

## Что задаётся в values

| Поле | Назначение |
| --- | --- |
| `vllm` | Единственный набор параметров движка |
| `replicaCount`, `image` | 0 или 1 реплика; образ с digest |
| `dra` | Существующий DeviceClass, количество, capacity и настройки драйвера |
| `dra.selectors` | CEL-фильтры, например UUID; реальные атрибуты — из ResourceSlice, необязательные проверяются через `has()` |
| `modelRefs` | Model из ai-models; аннотация на metadata Deployment |
| `modelVolumes` | Альтернатива: существующие PVC, read-only пути весов |
| `resources`, `shmSize` | Бюджет процесса, включающий CPU KV и memory-backed shm |
| `nodeSelector`, `tolerations`, `imagePullSecrets` | Размещение и получение образа |
| `networkPolicy.extraIngress` | Точечный доступ шлюза и мониторинга |

ai-models доставляет веса в `/data/modelcache/models/<Model>`.
Namespace, PVC, Secret и GPU-классы подготавливаются отдельно.
API по умолчанию закрыт для других namespace; участники обращаются через Bifrost.

## Применить и проверить

Доставка — через [GitOps](../../docs/GITOPS.md), без `helm upgrade/rollback`
поверх Argo. Изменение vLLM обновляет checksum Pod, изменение DRA — имя шаблона.
Стратегия Recreate освобождает GPU перед новым Pod; Cache и Tune заменяют один B.

Startup probe допускает 30 минут, Deployment — 40 минут на размещение и старт.
Это предел ожидания, не обещанная скорость. Готовность подтверждается ответом API.

A30 и финальный Qwen запускаются через [заказ AI Inference](../inference-service/README.md).
