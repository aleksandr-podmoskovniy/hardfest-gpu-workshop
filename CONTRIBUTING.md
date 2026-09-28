# Как дополнять

Одна правка — одна понятная цель. Теорию отделять от измерений и от версии площадки. Любое число производительности сопровождается проверяемым отчётом.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
git diff --check
git commit -S -s -m "Describe one workshop change"
```

`-S` подписывает существующим GPG-ключом автора, `-s` добавляет DCO sign-off. Не переносить чужую identity и private key в свой fork. Своё имя/email/key настроить локально. [DCO](https://developercertificate.org/).

Не публиковать credentials/веса/необезличенную telemetry. Чужие документы и screenshots должны иметь подходящие права на распространение. Changes to live infrastructure требуют отдельного согласования; тесты этого проекта не должны скрыто подключаться к GPU или Kubernetes.
