# Публикация и сопровождение

## Два проекта, две роли

Учебник — этот отдельный Git-репозиторий. Частная доставка площадки — существующий эксплуатационный `hardfest-demo`. Не добавлять весь k8s-config в public subtree и не включать автоматическое двустороннее копирование: там credentials, адреса, исторические состояния и отличающаяся ответственность.

Профили переносить review-диффом. После успешной репетиции обновить lock, anonymized результаты и status. Публичный пример должен оставаться независимым от личного kubeconfig автора.

## Git и подпись

Локальная identity настроена на автора; GPG использует уже существующий ключ, закрытый ключ не копируется. Проверка без изменения global config:

```bash
git config --local --get user.name
git config --local --get user.email
git config --local --get user.signingkey
git config --local --get commit.gpgsign
```

Перед commit:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_public.py
git status --short --untracked-files=all
```

Просмотреть ВСЕ добавляемые файлы и картинки. Проверка не является полноценным secret scanner; она не распознаёт секреты на скриншотах. Не пользоваться слепым `git add -f .local`.

После review:

```bash
git add README.md WORKSHOP.md AGENTS.md LICENSE NOTICE CONTRIBUTING.md .gitignore .github \
  models.lock.json config docs labs manifests scripts tests examples results/REPORT.template.md assets/README.md
git diff --cached --check
git diff --cached
git commit -S -s -m "Add HardFest GPU inference workshop"
git log -1 --show-signature
```

## GitHub — отдельный шаг

Текущий аккаунт автора — `aleksandr-podmoskovniy`; старые материалы были в `myskat90/vllm-habr`. Новый remote не создаётся только из-за наличия gh login. Когда владелец подтвердит имя/видимость и review:

```bash
gh auth status
gh repo create aleksandr-podmoskovniy/hardfest-gpu-workshop --public --source=. --remote=origin
git push -u origin main
```

Эти команды **публикуют** проект. Не выполнять до разрешения и проверки материалов. MR для первого самостоятельного репозитория не обязателен; процесс дальнейших правок можно согласовать позже.

Нельзя публиковать: .local, raw exports Kubernetes, registry credentials, kubeconfig, HF tokens, персональные промпты, необезличенные логи/картинки. Лицензия репозитория не даёт права распространять веса моделей или закрытые части платформы.
