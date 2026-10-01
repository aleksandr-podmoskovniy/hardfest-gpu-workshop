# Возврат KV из оперативной памяти

В этом опыте B имеет окно 131 072, вход 65 536 и выход 128.
Это отдельный опыт, не продолжение A/B 64K + 2048.

Две конфигурации: [без RAM](../values/gemma-b-128k.yaml) и
[с 32 GiB CPU KV](../values/gemma-b-ram.yaml).
[Values с согласованными лимитами](../values/gemma-b-ram.yaml):
request 56 GiB, limit 80 GiB, shm 40 GiB. На VM 128 GiB A остановлена.
Веса в RAM не выгружаются; увеличенный активный контекст должен помещаться на GPU.

Переносите изменения в одно Application B по [GitOps](../docs/GITOPS.md).
Для каждой серии нужен новый процесс движка и одинаковый прогрев на другом префиксе.

## Подготовить запросы без Python

Для демонстрации механизма используем десять **синтетических последовательностей
token ID**, не осмысленные тексты. Первые токены различаются, остальная длина одинакова.
Эти результаты нельзя выдавать за качество ответов или измерения по учебным документам.

```bash
mkdir -p results/hardfest/kv-requests
for DOC in 0 1 2 3 4 5 6 7 8 9; do
  jq -n --argjson doc "$DOC" '{
    model:"gemma-4-31b",
    prompt: ([1000+$doc] + [range(0;65535) | 1250]),
    max_tokens:128, ignore_eos:true, temperature:0, stream:false
  }' > "results/hardfest/kv-requests/$DOC.json"
done
shasum -a 256 results/hardfest/kv-requests/*.json
```

Token ID относятся к конкретному токенизатору Gemma. Для другой модели сначала
проверьте словарь. Сохраните usage.prompt_tokens первого ответа: оно должно быть
65 536; иначе не используйте эту серию как сравнимую.

Откройте port-forward B на 18002, как в основном руководстве. Дальше задайте имя
серии `cache-off` или `cache-on`; не смешивайте их результаты:

```bash
export SERIES=cache-on
mkdir -p "results/hardfest/$SERIES"
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/before.txt"
for DOC in 0 1 2 3 4 5 6 7 8 9; do
  curl --fail --max-time 600 http://127.0.0.1:18002/v1/completions \
    -H 'Content-Type: application/json' \
    --data-binary "@results/hardfest/kv-requests/$DOC.json" \
    -o "results/hardfest/$SERIES/$DOC.json"
done
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/before-return.txt"
curl --fail --max-time 600 http://127.0.0.1:18002/v1/completions \
  -H 'Content-Type: application/json' --data-binary @results/hardfest/kv-requests/0.json \
  -o "results/hardfest/$SERIES/return.json"
curl --fail http://127.0.0.1:18002/metrics > "results/hardfest/$SERIES/after-return.txt"
jq .usage "results/hardfest/$SERIES/return.json"
```

Этот curl-запрос не измеряет клиентский TTFT: stream выключен.
TTFT возврата берите из приращения server histogram sum/count при **единственном**
выполненном запросе и отсутствии посторонней нагрузки, либо используйте streaming benchmark.
Не используйте curl time_starttransfer как TTFT нестримингового ответа.

## Что считать подтверждением

Нужны приращения:
`vllm:kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"}`,
`vllm:external_prefix_cache_hits_total` и `vllm:prefix_cache_hits_total`.
Последний относится к GPU-кэшу. Сравните before-return и after-return.

Если X остался на GPU, быстрый повтор не доказывает offload. Если X исчез и из RAM,
полное prefill не доказывает неисправность коннектора. Число документов подбирается
по фактическим счётчикам, не по одной оценке памяти.

[Ранее зафиксированный опыт](../results/kv-ram/README.md) дал 2,89 GiB CPU → GPU,
65 504 внешних cache hits и 0 локальных hits. Там использовался другой генератор
документов; новые команды выше — самостоятельная серия, старые цифры к ней
автоматически не относятся.

Для завершения верните в Git профиль B 64K и исходные memory/shm, выполните sync,
дождитесь Ready и только затем включайте A. PVC сохраняется.
