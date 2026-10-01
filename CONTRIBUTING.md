# Как дополнять

Одна правка — одна понятная цель. Теорию отделять от измерений и от версии площадки. Любое число производительности сопровождается проверяемым отчётом.

Для проверок нужны Python 3 и Helm 3+. Тесты рендерят все восемь профилей локально,
сравнивают ресурсы с контрольным снимком до миграции и проверяют checksum/DRA-ссылки.

Иллюстрации генерируются из `scripts/build_diagrams.py`: холст 1200×760,
отступ 48, единые шрифты и палитра. После правки пересоберите SVG и откройте
их в браузере: автоматические тесты не заменяют визуальную проверку текста
и стрелок. QR в `assets/workshop-qr.svg` ведёт на главную страницу GitHub.

```bash
python3 -m pip install -r requirements-dev.txt
for profile in values/*.yaml; do
  helm lint charts/vllm-runtime --strict -f "$profile" || exit 1
done
python3 scripts/check_manifests.py
python3 scripts/build_diagrams.py
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
python3 scripts/check_docs.py
git diff --check
git commit -S -s -m "Describe one workshop change"
```

`-S` подписывает существующим GPG-ключом автора, `-s` добавляет DCO sign-off. Не переносить чужую identity и private key в свой fork. Своё имя/email/key настроить локально. [DCO](https://developercertificate.org/).

Не публикуйте секреты, веса, личные данные, внутренние адреса и снимки с токенами
в адресной строке. Проверяйте права на распространение чужих документов и изображений.
Изменения работающего стенда требуют отдельного согласования; тесты проекта
не должны скрыто подключаться к GPU или Kubernetes.
