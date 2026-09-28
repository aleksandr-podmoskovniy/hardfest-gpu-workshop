# Инференс без простоя GPU — HardFest

Мастер-класс на **90 минут**: от очереди на одной GPU к управляемому инференсу, KV-кэшу в RAM и динамическому разделению ускорителей.

**Ведущему: открыть [WORKSHOP.md](WORKSHOP.md).** Это один линейный файл для показа; команды выполнять из корня репозитория.

[![Offline checks](https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop/actions/workflows/check.yml/badge.svg)](https://github.com/aleksandr-podmoskovniy/hardfest-gpu-workshop/actions/workflows/check.yml)

**Читателю:** [четыре главы теории](docs/THEORY.md) → [подготовка](docs/SETUP.md) → [команды для копирования](docs/COMMANDS.md) → [лабораторные](labs/). [Все 52 слайда](docs/SLIDES_MAP.md) перенесены по смыслу: Q/K/V и GQA, расчёт памяти, cache, scheduler, speculation, RAG и разделение GPU.

> Статус: сценарий и локальные инструменты подготовлены. Полный прогон на H100 ещё не подтверждён. Профили — кандидаты для репетиции, не обещание производительности. См. [что проверено](docs/STATUS.md) и [критерии готовности](docs/REHEARSAL.md).

## Что собираем

| Ресурс | Назначение |
| --- | --- |
| H100 №1 | Gemma A: штатные настройки выбранной версии vLLM, затем эквивалентный сервис Deckhouse |
| H100 №2 | Та же Gemma B: явное управление KV, батчингом, графами, затем assistant |
| Отдельная A100 | Динамические MIG-разделы и MPS поверх MIG; независимые embedding/reranking сервисы |
| Две освобождённые H100 | Дополнительный финал: Qwen TP2; NVLink сначала проверяется, а не предполагается |

Участнику без трёх GPU доступны теория, offline-проверки и последовательный A/B на одной совместимой карте. Это меняет расписание и требует перезапуска между вариантами. Большие веса, доступ к gated-моделям и достаточная память приобретаются/настраиваются самостоятельно.

## Быстрый старт без изменений кластера

Требуются Python 3.10+, Git; для живого стенда — kubectl и доступный kubeconfig.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
python3 scripts/check_docs.py
python3 scripts/hf.py --site config/site.example.json render a
```

Для нового checkout выполните `python3 scripts/hf.py init-site`: создаст `.local/site.json` по [образцу](config/site.example.json), не затронув существующий файл. Замените ВСЕ `REPLACE_…`, кроме полей необязательных этапов. Не коммитьте этот файл. [Подготовка](docs/SETUP.md) объясняет каждое поле.

```bash
python3 scripts/hf.py preflight
# Только посмотреть: ни одного apply/scale.
python3 scripts/hf.py render a
```

## Навигация

- [90-минутный показ](WORKSHOP.md): что говорить, что выполнять, что увидеть, когда идти дальше.
- [Теория](docs/THEORY.md): память, KV, очередь, prefill/decode, TP/PP, MIG/MPS.
- [Методика измерений](docs/MEASUREMENTS.md): холодный/повторный вход, длинный контекст, TTFT и очередь отдельно.
- [Лабораторные](labs/): A/B, RAM-cache, speculation, Deckhouse, динамический MIG/MPS, TP2.
- [Профили vLLM](manifests/profiles/): явный diff параметров, без выгрузки весов в CPU.
- [Точный состав моделей](models.lock.json): версии и исторически проверенные размеры файлов.
- [Репетиция](docs/REHEARSAL.md), [диагностика](docs/TROUBLESHOOTING.md), [публикация](docs/PUBLISHING.md).
- [Источники](docs/SOURCES.md), [план иллюстраций](assets/README.md).

## Где что хранится

```text
WORKSHOP.md              экран ведущего, тайминг и переходы
docs/                   теория, подготовка, измерения, репетиция
labs/                   подробные шаги отдельных экспериментов
manifests/profiles/      переносимые параметры vLLM
config/site.example.json пример привязки к оборудованию и PVC
scripts/                безопасная генерация, запуск, измерения
tests/                  проверки без Kubernetes и GPU
results/                форма отчёта; сырые результаты игнорируются
assets/                 только проверенные обезличенные иллюстрации
.local/                 частные настройки и материалы площадки (ignored)
```

## Отношение к старому GitOps-проекту

Этот репозиторий — учебник и переносимые профили. Старый `hardfest-demo` остаётся эксплуатационным источником каталога моделей, доставки и сведений о стенде. Он здесь не копируется целиком. Новые ручные Deployment имеют префикс `hf-`, не перехватывают старые `qwen38`/`gemma-*`. Общие PVC монтируются read-only; cleanup их не удаляет.

Перед живым запуском ведущий проверяет владельцев ресурсов и отсутствие конкурирующего autosync. Единого автоматического синхронизатора между двумя репозиториями нет: перенос изменений делается осознанным diff, см. [публикацию](docs/PUBLISHING.md).

Автор: [Aleksandr Podmoskovniy](https://github.com/aleksandr-podmoskovniy). Структура продолжает идею [IT Elements 2025](https://github.com/myskat90/vllm-habr/tree/main/IT%20Elements%202025), но его runtime, аппаратные настройки и результаты не переносятся на новые модели.

Условия повторного использования: [LICENSE](LICENSE); лицензии моделей и сторонних компонентов отдельные.
