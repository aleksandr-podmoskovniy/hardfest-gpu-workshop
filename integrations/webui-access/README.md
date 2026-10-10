# Личные ключи Bifrost для Open WebUI

Адаптер связывает аккаунт WebUI с **Virtual Key (VK)** — личным ключом шлюза.
После одобрения пользователь пишет в чат, а адаптер подставляет его VK на сервере.
Ключ не попадает в браузер.

`WebUI → адаптер: личность пользователя → Bifrost: личный VK → модель`

Модели и квоты задаются в values; код не привязан к Gemma. Полная схема доступа
и подключение документов — в [настройке чата](../../docs/CHAT_AND_ACCESS.md).

## Что меняет одобрение аккаунта

| Состояние | Действие адаптера |
| --- | --- |
| `pending` | Запрещает запросы |
| `user` или `admin` | Создаёт либо находит прежний VK, проверяет политику |
| Каждый запрос | Проверяет подпись и срок JWT, актуальную роль через WebUI, права VK |
| Возврат в `pending` или удаление | Запрещает новые запросы, пытается выключить VK в Bifrost |

Связь хранится по UUID WebUI, не по email. Рестарт и повторный вход не должны
менять ID VK или расход. Сверка выполняется каждые 15 секунд после предыдущей;
первый запрос также проверяет наличие ключа. Служебная учётка исключается.

> [!IMPORTANT]
> Блокировка в адаптере не доказывает отзыв самого VK. Если core API Bifrost
> не видит native-ключ, его выключение не подтверждено. Нужны административный
> отзыв и проверка прямого запроса. Native DELETE лишь отвязывает владельца;
> использовать его как отзыв нельзя. Уже начатый ответ может завершиться.

## Сборка и развёртывание

Нужны работающие WebUI/Bifrost, отдельные служебные аккаунты и namespace WebUI.
Адаптер запускается **в одной реплике**; это не HA-сервис.

Из корня репозитория:

```bash
export ACCESS_IMAGE=registry.example.com/integrations/webui-access:0.4.0
docker build --platform linux/amd64 \
  -t "$ACCESS_IMAGE" integrations/webui-access/bridge
docker push "$ACCESS_IMAGE"
```

Запишите digest опубликованного образа в `access-site.yaml`. Начальная
конфигурация остановлена; значения `REPLACE_...` заменяются до запуска:

```yaml
replicaCount: 0
image: registry.example.com/integrations/webui-access@sha256:REPLACE_DIGEST
existingSecret: webui-access
persistence:
  enabled: true
  size: 64Mi
  storageClass: REPLACE_STORAGE_CLASS
config:
  webui_url: http://open-webui:8080
  gateway_url: https://gateway.example.com
  service_user_id: REPLACE_WEBUI_SERVICE_UUID
  managed_by: team-webui
  key_name_prefix: Team WebUI
  state_dir: /state
  provisioning_mode: create
  chat_models: [vllm/assistant]
  providers:
    - provider: vllm
      allowed_models: [assistant]
      key_ids: [REPLACE_PROVIDER_KEY_ID]
  budget_usd: 10
  budget_reset: 24h
  requests_per_minute: 10
  tokens_per_minute: 200000
  max_concurrent_total: 8
  max_concurrent_per_user: 3
  reconcile_interval_seconds: 15
  pricing: []
```

| Поле | Как выбрать |
| --- | --- |
| `managed_by` | Уникальный и постоянный ID установки; смена — не ротация ключа |
| `chat_models` | Внешние ID работающих маршрутов `provider/model` |
| `providers` | Точные модели и provider key IDs; при alias учесть конечные имена |
| `max_concurrent_*` | Слоты чата и фоновых задач WebUI вместе; предел 1 может заблокировать чат заголовком |
| `pricing` | Пустой список сохраняет тарифы шлюза; пример персональных тарифов — [overlay](../../examples/webui-access.yaml) |

Wildcard и доступ ко всем provider keys не разрешаются. Лимиты параллельности
применяются по UUID сразу к новым и существующим пользователям, не сбрасывая
бюджеты Bifrost. Групповые квоты по ролям не реализованы.

Создайте Secret **вне Git**, используя менеджер секретов или защищённые файлы
для `kubectl create secret generic --from-file`:

| Поле Secret | Назначение |
| --- | --- |
| `WEBUI_ADMIN_API_KEY` | Чтение пользователей и актуальных ролей WebUI |
| `BIFROST_MANAGEMENT_KEY` | Управление собственными VK и, при настройке, тарифами |
| `WEBUI_TRANSPORT_KEY` | Авторизация WebUI перед адаптером |
| `FORWARD_USER_INFO_HEADER_JWT_SECRET` | Проверка подписанной личности пользователя |

Все значения — от 32 символов. Транспортный и подписывающий секреты должны быть
разными случайными значениями. Не передавайте их через values или историю shell.
Для внутреннего CA задайте `caConfigMap`; проверку TLS не отключайте.

Проверьте `webuiSelector` и сетевые правила: чарт рассчитан на WebUI:8080,
DNS и HTTPS-шлюз:443. Namespace, Secret и аккаунты чарт не создаёт.

```bash
helm lint charts/webui-access --strict
helm template webui-access charts/webui-access \
  --namespace workshop-ui -f access-site.yaml
```

Сначала создайте PVC через [GitOps](../../docs/GITOPS.md) при нуле реплик,
инициализируйте журнал ниже, затем включите `replicaCount: 1` через Git/Argo CD.

### Журнал выдачи: защита от повторного создания ключей

Журнал хранит UUID пользователя, ID и имя VK, **не значение ключа**.
Попытка сохраняется до POST. Неопределённый ответ API или рестарт не вызывают
повторную выдачу; повреждённый журнал закрывает доступ.

Для новой установки подготовьте файл `inventory.json`:

```json
{"version":1,"managed_by":"team-webui","keys":[]}
```

Для существующей установки сначала остановите адаптер через Git/Argo. Получите
полный список его VK, включая неактивные, и сверьте markers `managed-by`/`user-id`.
Вместо пустого `keys` внесите по одной записи
без секретных значений:

```json
{"user_id": "UUID", "key_id": "VK_ID", "name": "VK_NAME"}
```

При неоднозначности миграцию не продолжайте.

Временный служебный Pod должен подключать PVC к `/state`, использовать тот же
образ, UID/GID 65532 и `fsGroup: 65532`. В нём выполните:

```bash
kubectl -n workshop-ui exec -i webui-access-initialize -- \
  /bridge --initialize-state /state team-webui < inventory.json
```

Инициализация не перезаписывает существующий журнал. Удалите только временный Pod,
сохраните PVC и включите адаптер. `Recreate` и файловая блокировка исключают
двух писателей. Новый VK получает имя `Team WebUI: Имя (UUID)`, доступное поиску Bifrost.

Не заменяйте PVC пустым и не отключайте журнал после миграции. Защита от Helm/Argo
prune не защищает от ручного удаления. Откат на старый адаптер с разрешённой выдачей
опасен дублями. Если процесс упал между записью попытки и POST, проверьте API
и журнал вручную: автоматической повторной выдачи не будет.

### Native-выдача с владельцем

По умолчанию используется core API. Для native API добавьте **оба** параметра:

```yaml
config:
  issuer_user_id: REPLACE_BIFROST_OWNER_UUID
  issuer_user_access_profile_id: 7
```

Здесь `7` — пример ID профиля, привязанного к владельцу, не родительского шаблона.
`service_user_id` остаётся UUID **WebUI**, `issuer_user_id` — UUID **Bifrost**.
Профиль должен быть активен и точно совпадать с values по моделям, provider keys,
бюджету и rate limits, без wildcard и MCP grants.

| Проверка API | Что подтверждает |
| --- | --- |
| `GET /api/users/{owner}/access-profiles` | Правильный активный профиль владельца |
| `GET /api/users/{owner}/virtual-keys` | ID, владельца, модели и точные provider key IDs |
| `GET /api/governance/virtual-keys/quota` с личным `x-bf-vk` | Реальные лимиты конкретного VK и `allow_all_keys: false` |

Пустые `budgets` в owner-ответе не означают отсутствия лимита: он проверяется
отдельно через quota API, без management-credential. Несовпадение или недоступность
проверки закрывает чат; адаптер не подставляет вместо неё лимит профиля.

Native API может не сохранять team/customer профиля: `team_id` не гарантирует
группировку. Непустая команда должна совпасть с values, customer не поддерживается.
В core-режиме заданный `team_id` обязателен; management-учётке нужен доступ
к этой команде. Чужие и неоднозначные ключи не принимаются.

## Подключение WebUI

В WebUI включите подписанную передачу личности:

```yaml
ENABLE_FORWARD_USER_INFO_HEADERS: "True"
FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS: "60"
# FORWARD_USER_INFO_HEADER_JWT_SECRET — ссылка на тот же Secret.
```

Сохраните существующие Secret-ссылки при изменении env: Helm заменяет списки
целиком. Создайте OpenAI-подключение `http://webui-access:8080/v1` с транспортным
ключом. Через ACL разрешите модели участникам; адаптер не управляет ACL и регистрацией.

| Настройка модели участника | Значение |
| --- | --- |
| Function Calling | `Legacy` |
| Builtin Tools | Выключено |
| File Context, File Upload | Включено |
| Web Search, Image Generation, Code Interpreter, Terminal, Memory | Выключено |

Legacy передаёт найденные документы в сообщениях. Native может добавлять
инструменты даже к обычному браузерному запросу; адаптер тогда возвращает
`request capability not allowed`. Не исправляйте это выдачей MCP-прав.
[Поведение WebUI](https://docs.openwebui.com/reference/server-side-tool-calling/).

Административные OIDC/OAuth/MCP-модели остаются на отдельном подключении.
У участников вызовы tools/functions и MCP запрещены; пустые поля инструментов
удаляются. В Bifrost должны быть включены mandatory VK и enforce-auth,
чтобы глобальный fallback не расширял права.

## Добавление модели в работающую установку

Одного изменения `chat_models` недостаточно: несовпадение политик даёт
`personal access is not ready` (`key model policy drift` или `quota model policy drift` в логах).

1. Подготовьте backend, маршрут, provider key и тариф; проверьте каждую реплику шлюза.
2. Для native-выдачи создайте отдельный профиль с полной целевой политикой.
3. В согласованное окно обновите **только модельные разрешения** действующих VK
   этой установки. Сохраните ID связей; не передавайте value, бюджет, rate limits
   или флаг активации.
4. Доставьте согласованные `providers`, `chat_models` и ID профиля через Git/Argo.
5. Проверьте owner/quota API всех реплик, старый аккаунт и новое одобрение.
   После этого откройте модель в WebUI; остановленные presets скройте.

Переход не атомарен: до согласования политик возможны отказы. Не перевыпускайте
VK и не распространяйте общий профиль поверх личных бюджетов.

## Проверить перед открытием доступа

- Два аккаунта проходят `pending → user → pending → user`: отказ, ответ,
  отказ, прежний VK без сброса расхода. Отдельно проверен реальный отзыв в Bifrost.
- Из браузера работает потоковый ответ вместе с фоновыми задачами. В журнале
  выбран фильтр **«Виртуальные ключи»**, у каждого аккаунта свой ID.
- Для проверки браузерной ветки через API передан непустой `session_id`,
  без принудительных `tools: []` и Function Calling, которые обходят настройки UI.
- Проверены запреты модели/MCP, лимиты и ошибка недоступного backend.
- Стоимость ответа совпадает с токенами и тарифом; журнал и счётчик бюджета
  сверены на каждой реплике Bifrost.

> [!IMPORTANT]
> Выдача VK и правильный тариф не доказывают общий HA-бюджет. На стенде
> [зафиксирован недосчёт](../../results/rtx5060-access-20261006.json);
> строгое общее ограничение расхода не подтверждено.

Локальные проверки без обращения к кластеру:

```bash
(cd integrations/webui-access/bridge && go vet ./... && go test -race ./...)
python3 -m unittest discover -s tests -p 'test_webui_access_chart.py' -v
```

Контракты восстановления — [owner.go](bridge/owner.go), проверка лимитов —
[quota.go](bridge/quota.go), журнал — [journal.go](bridge/journal.go).
Их тесты моделируют потерянный POST, рестарт, недоступные API и расхождение политик.
Совместимость другого Bifrost проверяется до переключения подключения.

## Ограничения и старые установки

- Поддерживаются чат и SSE. Responses API, embeddings, reranking и Whisper
  используют другие подключения; личный VK не персонализирует их автоматически.
- `/healthz` проверяет процесс, не успешность сверки: контролируйте логи.
- Повторяется только точный отказ `Virtual key is inactive`, ограниченно и
  с повторной проверкой прав. Бюджет, сеть, ошибка модели и начатый поток не повторяются.
- При обновлении с 0.1 сохраните `name`, `existingSecret`, `managed_by`,
  `key_name_prefix`; перенесите модели/ключи в `providers[]`, тарифы — в `pricing[]`.
  Старые поля не принимаются. Перед запуском проверьте доступ к прежним VK.
- Новая установка использует `create`, не устаревший `reserve`. Без постоянного
  журнала защита от повторной выдачи после рестарта слабее; читаемые имена
  и безопасная миграция описаны выше.
