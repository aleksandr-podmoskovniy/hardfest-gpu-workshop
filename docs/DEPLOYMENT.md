# Порядок развёртывания

1. Подготовьте ноды, DRA, классы, namespace и PVC по [SETUP](SETUP.md).
2. Перенесите [манифесты](../deploy/README.md) в GitLab.
3. Зарегистрируйте два Application в управляющем кластере.
4. Примените через Argo коммит с нулём реплик и проверьте состав ресурсов.
5. Включите A, дождитесь API; затем B. Две модели занимают две разные DRA-заявки.
6. Подключите два точных маршрута Bifrost к двум Service.
7. В Open WebUI добавьте два разных имени моделей и проверьте обычную одобренную учётную запись.
8. Только после этого запускайте сравнительную нагрузку.

Все команды commit/push/sync — в [GITOPS](GITOPS.md). Autosync для учебных Application
не включён. Ветка GitLab — источник состояния; прямой kubectl scale создаёт drift
и не используется для нормального переключения этапов.

## Проверка после каждого изменения

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get applications hardfest-gemma-a hardfest-gemma-b
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
kubectl --context "$GPU_CONTEXT" -n hardfest-demo logs deployment/hf-gemma-b --tail=100
```

Сверьте revision Argo с SHA отправленного коммита. Healthy сам по себе не доказывает
правильную модель; отправьте запрос через API и через Open WebUI.

## Переходы

A/B 64K → остановка A → B 128K без RAM → B 128K с RAM.
Следующие этапы assistant, платформенный рецепт и Qwen выполняются только после
отдельного подтверждения в [STATUS](STATUS.md).
MIG/MPS использует собственные Application с правильным destination для A30.

## Откат

Отмените свой коммит через git revert, отправьте изменения и синхронизируйте
ту же Application. Не удаляйте PVC и не используйте force для неизменяемой DRA-заявки.
Для нового DeviceClass создайте новый ResourceClaimTemplate и обновите ссылку в Pod.

Перед остановкой моделей согласуйте завершение работы участников.
Open WebUI, Bifrost, базы знаний, namespace и веса не входят в очистку GPU-нагрузки.
