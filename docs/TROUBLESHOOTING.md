# Диагностика

## Argo не применяет изменения

Проверьте repoURL, ветку, source.path, destination и revision. Push в Git не равен
успешному sync. На учебных Application autosync выключен.

```bash
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b -o yaml
```

Нельзя одновременно запускать второй sync, пока первая операция выполняется.
Не используйте prune, force или удаление Application для исправления неясного diff.

## Pod Pending

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get pods,resourceclaims,pvc
kubectl --context "$GPU_CONTEXT" -n hardfest-demo get events --sort-by=.lastTimestamp
```

Проверьте свободную DRA-ёмкость, Ready ноды, taint/toleration, allocatable CPU/RAM,
PVC node affinity. Две Gemma требуют две разные GPU.
Не обходите чужой cordon и не удаляйте активные claims.

## OOM или shared memory

Сверьте GPU KV с окном и архитектурой модели, RAM cgroup с процессами и CPU KV,
shm с бюджетом коннектора. Свободная RAM хоста не отменяет лимит контейнера.
Сокращение max-num-seqs не гарантирует вместимость одной длинной истории.

## API отвечает, но модель в UI не работает

Проверьте последовательно vLLM Service, NetworkPolicy, точный маршрут Bifrost,
Virtual Key и соединение Open WebUI. A и B имеют один upstream model ID, но разные
Service и ключи провайдера. Один общий балансируемый маршрут разрушает A/B.

Локальный пользователь не имеет OIDC-токена. Для него нужна отдельная серверная
авторизация inference, без передачи сервисного ключа браузеру.
Сохранённая конфигурация WebUI может иметь приоритет над env Helm values.
Управляющий ключ Bifrost с ограниченным DAC может не видеть созданные объекты;
используйте штатную администраторскую сессию, а не обход проверки доступа.

## Непонятные цифры

Холодный prefill, ожидание в очереди, TTFT, рассуждения и финальный текст — разные
измерения. Отдельно проверьте prefix cache движка и кэш готовых ответов шлюза.
Один быстрый повтор не доказывает RAM-offload или p95 под нагрузкой.

Новые uncorrectable ECC — повод остановить аппаратный опыт. Перезапуск Pod
не является ремонтом GPU.
