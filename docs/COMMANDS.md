# Команды для GitOps

Полный bootstrap — [GITOPS](GITOPS.md). Здесь рабочий цикл после настройки контекстов
и копирования исходников в GitLab.

## Посмотреть профиль и итоговый YAML

```bash
diff -u "$DEMO_DIR/gemma-a/profile.yaml" "$DEMO_DIR/gemma-b/profile.yaml"
kubectl kustomize "$DEMO_DIR/gemma-b"
kubectl --context "$GPU_CONTEXT" diff -k "$DEMO_DIR/gemma-b"
```

У diff код 1 означает различия. Проверяйте конкретные ресурсы, а не весь большой
GitOps-репозиторий.

## Проверить и отправить изменение

```bash
kubectl --context "$GPU_CONTEXT" apply --dry-run=server -k "$DEMO_DIR/gemma-b"
git diff -- "$DEMO_DIR/gemma-b"
git add -- "$DEMO_DIR/gemma-b"
git diff --cached --check
git commit -S -s -m "Tune Gemma B"
git push
```

## Применить отправленный коммит

```bash
REVISION=$(git rev-parse HEAD)
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" patch application hardfest-gemma-b \
  --type merge -p "$(jq -nc --arg rev "$REVISION" '{operation:{sync:{revision:$rev,prune:false}}}')"
kubectl --context "$ARGO_CONTEXT" -n "$ARGO_NAMESPACE" get application hardfest-gemma-b -o yaml
kubectl --context "$GPU_CONTEXT" -n hardfest-demo rollout status deployment/hf-gemma-b --timeout=15m
```

Не запускайте вторую sync operation, пока первая не завершена. В UI Argo доступны
тот же diff, Sync и журнал операции.

## Проверить API

```bash
kubectl --context "$GPU_CONTEXT" -n hardfest-demo port-forward svc/hf-gemma-b 18002:8000
```

В другом терминале:

```bash
curl --fail http://127.0.0.1:18002/health
curl --fail http://127.0.0.1:18002/v1/models | jq .
curl --fail http://127.0.0.1:18002/metrics
```

Нагрузка — [vllm bench serve](../labs/01-ab.md), RAM-кэш — [отдельный опыт](../labs/02-kv-ram.md).
Готовые ответы Bifrost не должны подменять работу движка при измерениях.
