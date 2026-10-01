# Персональные ключи Bifrost для Open WebUI

Адаптер автоматически создаёт Bifrost Virtual Key для одобренного пользователя
Open WebUI и подставляет его в запросы к моделям. Ключ остаётся на сервере:
пользователю не нужно копировать его в настройки или выпускать API-ключ WebUI.

Модели, провайдеры, квоты и тарифы задаются в Helm values. Зависимости от Gemma,
названия мероприятия или фиксированного числа моделей в коде нет.
[Пример для двух Gemma](../../examples/webui-access.yaml) — отдельный overlay.

## Выдача и отзыв

1. Пользователь регистрируется с ролью `pending`; доступа к чату ещё нет.
2. Администратор меняет роль на `user`. Сверка создаёт выключенный VK с заданной
   политикой, проверяет права и тариф, затем активирует его.
3. Open WebUI передаёт подписанный `X-OpenWebUI-User-Jwt`. Адаптер проверяет
   подпись, срок действия и актуальную роль через API WebUI, затем отправляет
   запрос в Bifrost с личным VK.
4. Возврат в `pending` или удаление аккаунта запрещает новые запросы.
   Следующая сверка выключает VK; уже начатый ответ может завершиться.

Сверка выполняется раз в 15 секунд после окончания предыдущей; интервал
настраивается. Первый запрос также проверяет наличие ключа. Обрабатываются все
аккаунты с ролью `user` или `admin`, кроме указанной служебной учётки.
Групповые политики и разные квоты по ролям здесь не реализованы.

Идентификатор связи — неизменяемый UUID WebUI, а не email или имя.
Имя нового VK: `<managed_by>:<user-id>`; описание содержит имя пользователя.
Повторный вход, рестарт и повторное одобрение используют тот же VK и расход.
Чужие ключи адаптер не принимает, не удаляет и не перенастраивает.

Если POST завершился ошибкой, объект мог сохраниться. Адаптер ищет его через GET;
при неопределённом результате отказывает, не повторяя POST в текущем процессе.
Уникальное имя VK в целевом Bifrost дополнительно защищает от дубликата после
рестарта. Ошибку видимости объектов нужно устранить до повторной попытки.

## Конфигурация

[Чарт](../../charts/webui-access/values.yaml) по умолчанию остановлен.
Минимальный `access-site.yaml` для одной модели:

```yaml
replicaCount: 0
existingSecret: webui-access
config:
  webui_url: http://open-webui:8080
  gateway_url: https://gateway.example.com
  service_user_id: REPLACE_WITH_WEBUI_SERVICE_USER_UUID
  managed_by: team-webui
  key_name_prefix: Team WebUI
  provisioning_mode: create
  chat_models:
    - vllm/assistant
  providers:
    - provider: vllm
      allowed_models: [assistant]
      key_ids: [REPLACE_WITH_BIFROST_PROVIDER_KEY_ID]
  budget_usd: 10
  budget_reset: 24h
  requests_per_minute: 10
  tokens_per_minute: 200000
  max_concurrent_total: 8
  max_concurrent_per_user: 1
  reconcile_interval_seconds: 15
  pricing: []
```

`chat_models` содержит внешние ID вида `provider/model`. В `providers`
перечисляются разрешённые модели и точные ID ключей провайдера из Bifrost.
Можно задать несколько провайдеров и любое непустое число моделей.
При маршрутизации через alias внесите в политику необходимые alias и конечные
имена моделей. Wildcard-доступ и все ключи провайдера не разрешаются.

`managed_by` должен быть уникальным для установки и постоянным при обновлениях.
Его изменение создаёт другую область владения; это не способ ротации ключей.
`key_name_prefix` задаёт имена тарифных правил и назначаемых резервных ключей;
для новых ключей в режиме `create` используется стабильное имя выше.

Пустой `pricing` оставляет тарифы шлюза. Для персонального тарифа добавьте правило
с точным именем модели:

```yaml
config:
  pricing:
    - model: assistant
      input_usd_per_million_tokens: 10
      output_usd_per_million_tokens: 20
```

Это учёт стоимости, не платёжная система. Квоты и расход применяются в Bifrost;
локальное ограничение параллельных запросов защищает адаптер.
Служебные запросы WebUI, например заголовки чатов, используют тот же VK.

При несовпадении политики существующего VK с values адаптер отказывает.
Он не переписывает бюджет и не обнуляет расход. Меняйте действующие политики
через API управления Bifrost с сохранением usage, затем согласуйте values.

## Сборка и развёртывание

```bash
export ACCESS_IMAGE=registry.example.com/integrations/webui-access:0.2.0
docker build --platform linux/amd64 \
  -t "$ACCESS_IMAGE" integrations/webui-access/bridge
docker push "$ACCESS_IMAGE"

helm lint charts/webui-access --strict
helm template webui-access charts/webui-access \
  --namespace workshop-ui -f access-site.yaml
```

Получите digest опубликованного образа и задайте `image: registry/...@sha256:...`.
Перед запуском замените placeholders, настройте `webuiSelector` и создайте Secret
вне Git. Чарт размещается в namespace WebUI; сетевые правила по умолчанию
рассчитаны на WebUI:8080, DNS и HTTPS-шлюз:443. Для своего окружения проверьте порты.

| Поле Secret | Назначение |
| --- | --- |
| `WEBUI_ADMIN_API_KEY` | Чтение списка и актуальной роли пользователей WebUI |
| `BIFROST_MANAGEMENT_KEY` | Создание, чтение и изменение собственных VK; при `pricing` также тарифов |
| `WEBUI_TRANSPORT_KEY` | Авторизация подключения WebUI к адаптеру |
| `FORWARD_USER_INFO_HEADER_JWT_SECRET` | Проверка подписанной личности WebUI |

Используйте отдельные служебные учётки. Все четыре значения должны быть не короче
32 символов; транспортный и подписывающий секреты — разные случайные значения.
Храните их в менеджере секретов или защищённых файлах для
`kubectl create secret generic --from-file`, не в values и не в истории shell.
Для внутреннего CA используйте `caConfigMap`; проверку TLS не отключайте.

После подготовки измените `replicaCount` на 1 и доставьте чарт и values через
[Git/Argo CD](../../docs/GITOPS.md). Чарт не создаёт Secret, Namespace и учётные записи.

## Подключение WebUI

В используемой версии WebUI должна поддерживаться передача подписанного JWT:

```yaml
ENABLE_FORWARD_USER_INFO_HEADERS: "True"
FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS: "60"
# FORWARD_USER_INFO_HEADER_JWT_SECRET — ссылка на тот же Secret.
```

Сохраните существующие Secret-ссылки WebUI при изменении env: Helm заменяет списки
целиком. Добавьте OpenAI-подключение к `http://webui-access:8080/v1` с
`WEBUI_TRANSPORT_KEY`. Разрешите нужные модели группе пользователей в WebUI.
Адаптер не управляет модельными ACL, регистрацией и базами знаний.

Участники не получают MCP через этот путь: запросы с tools/functions и MCP
отклоняются, VK создаются без MCP grants. OIDC и административный MCP оставьте
в отдельном подключении с собственными OAuth/RBAC-правами.

## Проверка

С тестовым аккаунтом проверьте `pending → user → pending → user`:
до одобрения — отказ, после — ответ, после отзыва — отказ; повторное одобрение
не меняет ID VK и расход. Проверьте два разных аккаунта, SSE, бюджет и лимиты.
В логах Bifrost выбирайте **«Виртуальные ключи»**, а не «Ключи провайдера».
На HA-шлюзе проверьте доступ и стоимость через каждую реплику.

```bash
(cd integrations/webui-access/bridge && go vet ./... && go test -race ./...)
python3 -m unittest discover -s tests -p 'test_webui_access_chart.py' -v
```

Тесты проверяют контракт API на локальных HTTP-серверах, без обращения к кластеру.
Схема управления соответствует Bifrost d8-edition: `/api/governance/virtual-keys`
с `provider_configs.key_ids`, `budgets` и `rate_limit`; тарифы —
`/api/governance/pricing-overrides`. Совместимость другого релиза проверяется
перед переключением рабочего подключения.

## Обновление с 0.1

Сохраните прежние `name`, `existingSecret`, `managed_by` и `key_name_prefix`.
Для старой установки это могло быть `hardfest-webui-access`,
`hardfest-webui-access`, `hardfest-webui-access` и `HardFest` соответственно.
Описание без имени пользователя, заканчивающееся на `user-id=<UUID>`, распознаётся.

Перенесите `allowed_models` и `provider_key_ids` внутрь `providers[]`;
`price_models` и общие цены — в `pricing[]`. Старые поля отвергаются схемой.
Сначала проверьте рендер и доступ management-ключа к уже выданным VK.

`provisioning_mode: reserve` оставлен для установок, где автоматическое создание
пока недоступно. Он назначает заранее подготовленный выключенный VK с описанием
`managed-by=<managed_by>; reserve` и подходящей политикой. В режиме `create`
резерв не нужен; тихого переключения между режимами нет.

## Ограничения

Одна реплика адаптера; это не HA-сервис. `/healthz` проверяет только процесс,
ошибки сверки нужно отслеживать в логах. Поддерживаются чат и SSE, не Responses API,
Whisper, embeddings или reranking. Их отдельные подключения и учёт не меняются.
RAG-контекст в сообщении учитывается как входные токены чата.
