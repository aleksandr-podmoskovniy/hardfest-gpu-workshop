# Манифесты и профили

Основной источник для участников — [Helm-чарт](../charts/vllm-runtime/README.md), values и [GitOps](../docs/GITOPS.md). Каталог ниже сохраняет старые JSON-профили
и операторский генератор для воспроизведения прежних экспериментов. Не используйте
его apply/start/stop параллельно с Application, управляющим теми же Deployment.

`profiles/` содержит читаемые конфигурации vLLM. [scripts/hf.py](../scripts/hf.py) собирает из профиля и private site Kubernetes `List` в JSON — Kubernetes принимает его как YAML/JSON через `-f`.

```bash
python3 scripts/hf.py --site config/site.example.json render a
python3 scripts/hf.py render b-cache
```

Для каждой стадии получаются ConfigMap, ResourceClaimTemplate, Deployment, ClusterIP Service и NetworkPolicy. Deployment создаётся с нулём реплик. Namespace/модели/Secret/PVC/pools не создаются и не удаляются. Пути к весам read-only, compile cache отдельный ephemeral volume.

| Стадия | Deployment | Профиль |
| --- | --- | --- |
| a | hf-gemma-a | gemma-a.json |
| b-tuned | hf-gemma-b | gemma-b-tuned.json |
| b-cache | hf-gemma-b | gemma-b-cache.json |
| b-spec | hf-gemma-b | gemma-b-spec.json |
| tp2 | hf-qwen-tp2 | qwen-tp2.json |
| embed-mig | hf-embed-mig | pooling + site-generated MIG class |
| embed-mps | hf-embed-mps | pooling + site-generated MIG/MPS class |
| rerank-mps | hf-rerank-mps | проверенный site recipe; запуск по умолчанию запрещён |

Три стадии B заменяют **одну** реплику после явной остановки. Они не занимают три карты. Менять template DRA in place нельзя: имя включает hash devices spec.

Рендер с примером содержит `REPLACE_…` и предназначен только для чтения/tests. `apply` требует заполненную привязку. Сначала server-side dry-run, затем apply при явном `--ack`; successful apply не подтверждает GPU/runtime compatibility.

Платформенный InferenceService появится отдельным обезличенным экспортом после проверки установленной CRD, см. [лабораторную](../labs/04-deckhouse.md). Не выдаём raw vLLM Deployment за реализацию сервиса Deckhouse.
