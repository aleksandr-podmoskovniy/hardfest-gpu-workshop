# HardFest workshop

Русскоязычный воспроизводимый мастер-класс, не оператор кластера.

- Основной экран ведущего — WORKSHOP.md. Подробности — docs/ и labs/.
- Не превращать планы, server-side dry-run и проверку файлов моделей в доказательство CUDA/inference.
- Производительность публиковать только с сырыми результатами, конфигурацией и одинаковой нагрузкой A/B.
- Публичные файлы не содержат внутренних адресов, kubeconfig, credentials, приватного registry или персональных путей. Привязка площадки — .local/site.json, не коммитить.
- Все workload-манифесты по умолчанию replicas: 0. Не создавать Argo autosync.
- Не создавать вручную DeviceClass: использовать классы установленного GPUClass/GPUPool-контроллера. Не выдумывать новую CRD-схему по названию продукта.
- Не менять MIG mode, драйверы, VFIO, cordon, маршруты, политики безопасности и чужие workloads без отдельного согласования.
- Не удалять Namespace, PVC, модели, GPUClass/GPUPool при cleanup.
- Проверки: python3 -m unittest discover -s tests -v; python3 scripts/check_public.py.
- Git commits: git commit -S -s. Не менять global Git/GPG, не экспортировать закрытые ключи.
- Публикация в GitHub и изменения кластера — отдельные действия, не следствие генерации Markdown.
