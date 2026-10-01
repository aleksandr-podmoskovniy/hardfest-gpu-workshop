# Как дополнять

Одна правка — одна понятная цель. Теорию отделять от измерений и от версии площадки. Любое число производительности сопровождается проверяемым отчётом.

Для проверок нужны Python 3 и Helm 3+. Тесты рендерят все восемь профилей локально,
сравнивают ресурсы с контрольным снимком до миграции и проверяют checksum/DRA-ссылки.

```bash
python3 -m pip install -r requirements-dev.txt
for profile in values/*.yaml; do
  helm lint charts/vllm-runtime --strict -f "$profile" || exit 1
done
python3 scripts/check_manifests.py
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
git diff --check
git commit -S -s -m "Describe one workshop change"
```

`-S` подписывает существующим GPG-ключом автора, `-s` добавляет DCO sign-off. Не переносить чужую identity и private key в свой fork. Своё имя/email/key настроить локально. [DCO](https://developercertificate.org/).

Не публиковать credentials/веса/необезличенную telemetry. Чужие документы и screenshots должны иметь подходящие права на распространение. Changes to live infrastructure требуют отдельного согласования; тесты этого проекта не должны скрыто подключаться к GPU или Kubernetes.
