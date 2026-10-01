#!/usr/bin/env python3
"""Build self-contained, editable workshop SVGs; no network or cluster access."""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED = "#252528", "#5e6572"
BLUE, PALE = "#0750f5", "#edf3ff"
TEAL, MINT = "#177c83", "#eaf6f5"
AMBER, SAND = "#a15b0b", "#fff2da"
PURPLE, LILAC = "#6844bc", "#efe9fc"
GRAY, LINE = "#f1f4f8", "#cbd5e1"


class Diagram:
    def __init__(self, name, title, subtitle, height=620):
        self.name, self.height = name, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">',
                      f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>',
                      '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>',
                      '<style>text{font-family:Arial,Helvetica,sans-serif} .title{font-weight:700} </style>']
        self.rect(1, 1, 1198, height-2, "#ffffff", LINE, 22)
        self.rect(40, 36, 7, 42, BLUE, BLUE, 3)
        self.text(64, 66, title, 34, INK, bold=True)
        self.text(64, 104, subtitle, 21, MUTED)

    def rect(self, x, y, w, h, fill=GRAY, stroke=LINE, radius=14, dash=False):
        dashed = ' stroke-dasharray="8 6"' if dash else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="2"{dashed}/>')

    def text(self, x, y, lines, size=24, color=INK, bold=False, anchor="start"):
        lines = [lines] if isinstance(lines, str) else lines
        weight = ' font-weight="700"' if bold else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}"{weight}>')
        for i, line in enumerate(lines):
            self.parts.append(f'<tspan x="{x}" dy="{0 if i == 0 else size*1.35}">{escape(line)}</tspan>')
        self.parts.append('</text>')

    def path(self, path, color=BLUE, arrow=False, dash=False, width=3):
        end = ' marker-end="url(#arrow)"' if arrow else ''
        dashed = ' stroke-dasharray="7 6"' if dash else ''
        self.parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{end}{dashed}/>')

    def card(self, x, y, w, h, title, body=(), fill=PALE, color=BLUE):
        self.rect(x, y, w, h, fill, color)
        self.text(x+22, y+39, title, 27, color, True)
        if body:
            self.text(x+22, y+76, body, 22)

    def footer(self, text):
        self.text(48, self.height-28, text, 20, MUTED)

    def save(self):
        self.parts.append('</svg>')
        (ROOT / 'assets' / (self.name+'.svg')).write_text('\n'.join(self.parts)+'\n')


def topology():
    d = Diagram('01-topology', 'Схема стенда: два кластера',
                'Пользовательские запросы проходят через шлюз; модели можно менять за ним.', 830)
    d.rect(48, 145, 298, 610, GRAY, LINE)
    d.rect(408, 145, 744, 610, "#ffffff", BLUE)
    d.text(72, 181, 'КЛАСТЕР WEBUI', 21, MUTED, True)
    d.text(440, 181, 'GPU-КЛАСТЕР / DECKHOUSE', 21, BLUE, True)
    d.card(70, 220, 254, 126, 'Open WebUI', ['Чат и голосовой ввод', 'Пароль / OIDC'])
    d.card(70, 433, 254, 148, 'Базы знаний', ['Документы и индекс', 'не зависят от', 'выбранной LLM'], '#ffffff', MUTED)
    d.path('M 194 348 V 431', MUTED, True)
    d.card(440, 220, 680, 126, 'HA Bifrost', ['Маршруты к моделям, ключи, лимиты и учёт запросов'])
    d.path('M 326 280 H 438', BLUE, True)
    d.text(381, 261, 'HTTPS', 18, MUTED, anchor='middle')
    d.card(440, 425, 322, 128, 'Gemma A — Base', ['H100 №1', 'Затем AI Inference'])
    d.card(798, 425, 322, 128, 'Gemma B — Tune', ['H100 №2', '64K → 128K + RAM'])
    d.path('M 780 348 V 385 H 601 V 423', BLUE, True)
    d.path('M 780 385 H 959 V 423', BLUE, True)
    d.card(440, 600, 322, 123, 'A30 / MIG + MPS', ['Сервисы поиска', 'Эмбеддеры, реранкер'], MINT, TEAL)
    d.card(798, 600, 322, 123, 'Kubernetes MCP', ['Отдельная авторизация', 'Права администратора'], LILAC, PURPLE)
    d.path('M 451 348 H 422 V 660 H 438', TEAL, True)
    d.path('M 1108 348 H 1137 V 660 H 1122', PURPLE, True, True)
    d.text(72, 644, ['Участникам — модели', 'и базы знаний.', 'MCP — отдельное право.'], 21, MUTED)
    d.footer('Схема подключения. Готовность отдельных интеграций и моделей — в docs/STATUS.md.')
    d.save()


def latency():
    d = Diagram('02-latency', 'Первый токен — не то же самое, что первый ответ',
                'Условная временная шкала, не график измерений.', 590)
    spans = [(48, 220, SAND, AMBER, 'Очередь'), (268, 340, PALE, BLUE, 'Prefill'),
             (608, 220, LILAC, PURPLE, 'Рассуждение'), (828, 324, MINT, TEAL, 'Текст ответа')]
    for x, w, fill, color, label in spans:
        d.rect(x, 185, w, 80, fill, color, 0)
        d.text(x+w/2, 233, label, 26, color, True, 'middle')
    for x, lines in [(48, ['Запрос']), (608, ['Первый', 'токен']), (828, ['Первое слово', 'ответа'])]:
        d.path(f'M {x} 155 V 281', MUTED, dash=True, width=2)
        d.text(x if x == 48 else x+8, 303, lines, 19, MUTED)
    for y, end, label, color in [(372, 608, 'TTFT клиента', BLUE),
                                (421, 828, 'До первого текста ответа', PURPLE),
                                (470, 1152, 'Полное время запроса', TEAL)]:
        d.path(f'M 48 {y-9} V {y} H {end} V {y-9}', color)
        d.text((48+end)/2, y-13, label, 23, color, True, 'middle')
    d.text(48, 527, 'Очередь и prefill измеряем отдельно. Вычитать один p95 из другого нельзя.', 24)
    d.footer('Рассуждение может отсутствовать. Между фазами есть накладные расходы сети и движка.')
    d.save()


def memory():
    d = Diagram('03-memory', 'Во что обходится длинный контекст',
                'Gemma 4 31B. Полезные KV-данные одной истории; округления пула не включены.', 820)
    d.rect(48, 141, 1104, 143, PALE, BLUE)
    d.text(600, 197, 'HBM × бюджет − веса − runtime = KV-пул', 38, BLUE, True, 'middle')
    d.text(600, 251, 'В нашем запуске веса заняли 57,91 GiB — и у A, и у B.', 26, INK, False, 'middle')
    d.text(76, 348, 'ИСТОРИЯ', 22, MUTED, True)
    d.text(510, 348, 'BF16 / 2 байта', 22, MUTED, True, 'middle')
    d.text(946, 348, 'FP8 / 1 байт', 22, BLUE, True, 'middle')
    for y, context, bf16, fp8 in [(385, '64K', '5,78 GiB', '2,89 GiB'),
                                   (509, '128K', '10,78 GiB', '5,39 GiB'),
                                   (633, '256K', '20,78 GiB', '10,39 GiB')]:
        d.rect(48, y, 1104, 99, '#ffffff', LINE)
        d.rect(754, y, 398, 99, PALE, BLUE)
        d.text(78, y+64, context, 42, INK, True)
        d.text(510, y+64, bf16, 44, INK, True, 'middle')
        d.text(946, y+64, fp8, 44, BLUE, True, 'middle')
    d.footer('Длина истории = вход + генерация. 256K здесь — расчёт, не подтверждённый запуск.')
    d.save()


def gemma_formula():
    d = Diagram('11-gemma-kv', 'KV-кэш Gemma: формула памяти',
                'Архитектура Gemma: 10 полных слоёв внимания и 50 локальных с окном 1024.', 860)
    d.rect(48, 148, 1104, 130, PALE, BLUE)
    d.text(600, 229, 'KV(S) = 2 × b × [ G(S) + L(S) ]', 49, BLUE, True, 'middle')
    d.rect(48, 320, 532, 239, '#ffffff', BLUE)
    d.rect(620, 320, 532, 239, '#ffffff', TEAL)
    d.text(80, 363, 'G(S) — ПОЛНОЕ ВНИМАНИЕ', 23, BLUE, True)
    d.text(652, 363, 'L(S) — ЛОКАЛЬНОЕ ВНИМАНИЕ', 22, TEAL, True)
    d.text(314, 437, '10 × 4 × 512 × S', 39, INK, True, 'middle')
    d.text(886, 431, '50 × 16 × 256', 39, INK, True, 'middle')
    d.text(886, 480, '× min(S, 1024)', 37, INK, True, 'middle')
    d.text(80, 507, ['10 слоёв, 4 KV-головы,', '512 элементов в голове'], 23, MUTED)
    d.text(652, 530, '50 слоёв, 16 KV-голов, размер 256', 23, MUTED)
    d.rect(48, 601, 1104, 184, GRAY, LINE)
    for x, title, lines in [(80, '2', ['Два массива:', 'ключи K и значения V']),
                             (437, 'b', ['Байт на элемент:', 'BF16 — 2, FP8 — 1']),
                             (795, 'S', ['Длина истории:', 'вход и генерация'])]:
        d.text(x, 651, title, 38, BLUE, True)
        d.text(x, 701, lines, 25, INK)
    d.footer('Результат — в байтах. Для GiB делим на 2³⁰. Параметры и ограничения расчёта — в MEMORY_BUDGET.md.')
    d.save()


def gitops():
    d = Diagram('12-gitops', 'Helm через GitLab и Argo CD',
                'Один чарт, отдельные values; управление и GPU-нагрузка в разных кластерах.', 670)
    d.card(48, 151, 322, 150, 'GitHub', ['Общие примеры', 'Документация и схемы', 'Без секретов'], GRAY, MUTED)
    d.card(440, 151, 712, 150, 'GitLab / k8s-config', ['Чарт + профиль values + привязки площадки', 'Application: Helm source, без autosync', 'diff → подписанный commit → push'])
    d.path('M 372 225 H 438', BLUE, True)
    d.text(405, 207, 'копия', 18, MUTED, anchor='middle')
    d.card(48, 382, 485, 181, 'Управляющий кластер', ['Argo CD: Helm render → sync', 'source: GitLab + чарт + values', 'destination: GPU-кластер'], PALE, BLUE)
    d.card(642, 382, 510, 181, 'GPU-кластер', ['ConfigMap + Deployment', 'ResourceClaimTemplate + Service', 'NetworkPolicy'], MINT, TEAL)
    d.path('M 796 303 V 339 H 290 V 380', BLUE, True)
    d.text(519, 330, 'выбранный коммит', 20, MUTED, anchor='middle')
    d.path('M 535 472 H 640', TEAL, True)
    d.text(587, 453, 'sync', 20, TEAL, anchor='middle')
    d.text(48, 611, 'Изменение values → checksum от Helm → перезапуск Recreate', 25, INK, True)
    d.footer('Примеры выключены: replicas = 0, без autosync. Секреты передаются отдельно от Git.')
    d.save()


def ab():
    d = Diagram('04-ab', 'Меняем настройки, а не условия задачи',
                'Одна версия vLLM, одинаковые BF16-веса, разные H100.', 660)
    d.rect(48, 142, 1104, 68, GRAY, LINE)
    d.text(600, 185, 'Окно 65 536      Вход 32 768      Выход 2 048', 30, INK, True, 'middle')
    d.card(48, 244, 532, 282, 'Gemma A — Base', [], GRAY, MUTED)
    d.card(620, 244, 532, 282, 'Gemma B — Tune', [], PALE, BLUE)
    for x, lines in [(72, ['KV: BF16', 'Prefix cache: выключен', 'CUDA graphs / compile: выключены', 'Attention: FlashAttention 4']),
                      (644, ['KV: FP8', 'Prefix cache: включён', 'CUDA graphs / compile: включены', 'Attention: Triton'])]:
        for i, line in enumerate(lines):
            d.text(x, 339+i*47, line, 25)
    d.rect(48, 550, 1104, 58, MINT, TEAL)
    d.text(600, 588, 'У обоих: prefill по 4096, до 32 последовательностей, веса только в GPU', 24, TEAL, True, 'middle')
    d.footer('Чанкирование оставлено у A для вместимости. KV в RAM здесь выключен; его проверяем отдельно.')
    d.save()


def attention():
    d = Diagram('13-attention', 'Зачем сохранять ключи и значения',
                'Упрощённый шаг attention: запрос текущего токена обращается к истории.', 740)
    d.card(48, 162, 280, 143, 'Текущий токен', ['Q — запрос', 'Нужен сейчас'], PALE, BLUE)
    d.card(410, 162, 332, 143, 'Прошлые токены', ['K — ключи', 'V — значения'], MINT, TEAL)
    d.card(826, 162, 326, 143, 'KV-кэш', ['Сохраняет K и V', 'для следующих шагов'], MINT, TEAL)
    d.path('M 744 232 H 824', TEAL, True)
    d.rect(48, 365, 1104, 234, PALE, BLUE)
    d.text(600, 422, 'Attention(Q, K, V) =', 36, BLUE, True, 'middle')
    d.text(600, 506, 'softmax( QKᵀ / √d ) V', 57, INK, True, 'middle')
    d.text(600, 565, 'Сравнить Q с ключами → получить веса → смешать значения', 26, MUTED, False, 'middle')
    d.path('M 188 307 V 363', BLUE, True)
    d.path('M 576 307 V 363', TEAL, True)
    d.text(48, 657, 'Кэш хранит промежуточные тензоры, не готовые ответы.', 29, INK, True)
    d.footer('d — размерность головы. Маски и детали конкретной архитектуры здесь опущены.')
    d.save()


def prefixes():
    d = Diagram('14-prefix', 'Общий смысл не означает общий префикс',
                'Для переиспользования KV нужны совпадающие токены с начала запроса.', 640)
    for y, name, changed, question in [(172, 'Запрос 1', False, 'Вопрос 1'),
                                      (300, 'Запрос 2', False, 'Вопрос 2'),
                                      (428, 'Запрос 3', True, 'Вопрос 3')]:
        d.text(48, y+45, name, 25, INK, True)
        for x, width, text, fill, color in [(230, 295, 'Другая дата' if changed else 'Общий system', SAND if changed else MINT, AMBER if changed else TEAL),
                                          (545, 325, 'Документ X', GRAY if changed else MINT, MUTED if changed else TEAL),
                                          (890, 262, question, PALE, BLUE)]:
            d.rect(x, y, width, 75, fill, color)
            d.text(x+width/2, y+46, text, 25, color, True, 'middle')
    d.path('M 230 262 V 271 H 870 V 262', TEAL)
    d.text(550, 571, 'Новое начало разрывает совпадение длинного префикса.', 28, INK, True, 'middle')
    d.footer('Порядок сообщений, шаблон чата, даты и пробелы влияют на последовательность токенов.')
    d.save()


def gptoss_formula():
    d = Diagram('15-gptoss-kv', 'GPT-OSS-120B: расчёт из презентации',
                'Отдельный учебный пример. Эти числа не подставляются в профиль Gemma.', 870)
    d.rect(48, 150, 1104, 130, PALE, BLUE)
    d.text(600, 202, 'Один токен одного слоя: K/V × головы × размер × байты', 25, MUTED, False, 'middle')
    d.text(600, 253, '2 × 8 × 64 × 2 = 2048 байт = 2 KiB', 40, BLUE, True, 'middle')
    d.rect(48, 315, 1104, 179, '#ffffff', BLUE)
    d.text(600, 371, '18 полных слоёв + 18 локальных с окном 128', 27, MUTED, False, 'middle')
    d.text(600, 441, 'KV(S) = 2 KiB × [18S + 18 min(S, 128)]', 40, INK, True, 'middle')
    for x, title, value in [(48, 'Одна история 128K', '4,5044 GiB'), (424, 'Четыре истории', '18,02 GiB'), (800, 'Восемь историй', '36,04 GiB')]:
        d.rect(x, 536, 352, 145, MINT, TEAL)
        d.text(x+176, 580, title, 25, TEAL, True, 'middle')
        d.text(x+176, 642, value, 38, INK, True, 'middle')
    d.text(600, 742, 'Условный KV-пул: 96 × 0,90 − 64 − 6 = 16,4 GiB', 29, INK, True, 'middle')
    d.text(600, 789, 'Три полные истории помещаются, четыре — уже нет.', 28, TEAL, True, 'middle')
    d.footer('96 GiB — условие задачи, не замер GPU стенда. Учтены полезные KV без выравнивания и рабочих буферов.')
    d.save()


def rag():
    d = Diagram('16-rag', 'Поиск документов и генерация — разные сервисы',
                'Индексирование выполняется при загрузке документов; поиск — при вопросе пользователя.', 740)
    for x, title, body in [(48, 'Документы', ['Разбивка на фрагменты']),
                           (424, 'Эмбеддер', ['Фрагменты → векторы']),
                           (800, 'Индекс', ['Векторы и источники'])]:
        d.card(x, 164, 352, 123, title, body, MINT, TEAL)
        if x < 800:
            d.path(f'M {x+354} 228 H {x+374}', TEAL, True)
    for x, title, body in [(48, 'Вопрос', ['Эмбеддер вопроса']),
                           (334, 'Поиск', ['Кандидаты из индекса']),
                           (620, 'Реранкер', ['Лучшие фрагменты']),
                           (906, 'LLM', ['Вопрос + фрагменты'])]:
        d.rect(x, 421, 246, 135, PALE, BLUE)
        d.text(x+18, 465, title, 27, BLUE, True)
        d.text(x+18, 510, body, 20)
        if x < 906:
            d.path(f'M {x+248} 490 H {x+284}', BLUE, True)
    d.path('M 976 289 V 356 H 457 V 419', TEAL, True)
    d.text(695, 344, 'поиск похожих векторов', 22, TEAL, False, 'middle')
    d.path('M 1029 558 V 609', BLUE, True)
    d.text(1029, 650, 'Ответ + источники', 23, BLUE, True, 'middle')
    d.text(48, 617, ['Замена LLM не требует пересоздания индекса.',
                     'При смене эмбеддера совместимость индекса проверяется заново.'], 24, INK)
    d.footer('Схема RAG. У каждого API проверяются доступ, формат ответа и работа по отдельности.')
    d.save()


def offload():
    d = Diagram('05-kv-ram', 'RAM сохраняет KV для повторного обращения',
                'Пример X → другие документы → X. Это схема эксперимента, не результат замера.', 670)
    d.text(279, 156, 'GPU', 28, BLUE, True, 'middle')
    d.text(905, 156, 'RAM', 28, TEAL, True, 'middle')
    rows = [(185, '1. Обработать X', 'KV документа X', 'Копия KV документа X'),
            (326, '2. Вытеснить X', 'KV документов Y, Z', 'Копия X остаётся'),
            (467, '3. Повторить X', 'KV X снова в GPU', 'Найденные блоки X')]
    for y, stage, gpu, ram in rows:
        d.text(48, y-12, stage, 22, MUTED)
        d.card(48, y, 454, 94, gpu, [], PALE, BLUE)
        d.card(726, y, 426, 94, ram, [], MINT, TEAL)
        if y == 185:
            d.path(f'M 504 {y+45} H 724', TEAL, True)
            d.text(614, y+29, 'Запись', 22, TEAL, anchor='middle')
        elif y == 467:
            d.path(f'M 724 {y+45} H 504', BLUE, True)
            d.text(614, y+29, 'Загрузка', 22, BLUE, anchor='middle')
        else:
            d.text(614, y+45, 'Другие запросы', 21, MUTED, anchor='middle')
    d.text(48, 608, 'Доказательство: запись → вытеснение из GPU → чтение из RAM.', 27, INK, True)
    d.footer('Быстрый повтор сам по себе не доказывает offload. Во время вычисления рабочий KV нужен на GPU.')
    d.save()


def scheduler():
    d = Diagram('06-scheduler', 'Длинный вход не обязан занимать весь шаг',
                'Схема работы планировщика. Размеры блоков условные, не длительность измерений.', 630)
    d.text(48, 155, 'Без чанкирования', 26, INK, True)
    d.rect(48, 175, 760, 70, PALE, BLUE)
    d.text(428, 219, 'Весь длинный prefill', 27, BLUE, True, 'middle')
    d.rect(828, 175, 324, 70, SAND, AMBER)
    d.text(990, 219, 'Decode ждёт', 26, AMBER, True, 'middle')
    d.text(48, 297, 'С чанкированием', 26, INK, True)
    for i in range(4):
        x = 48+i*280
        d.rect(x, 320, 166, 70, PALE, BLUE)
        d.text(x+83, 364, f'Prefill {i+1}', 24, BLUE, True, 'middle')
        d.rect(x+174, 320, 90, 70, MINT, TEAL)
        d.text(x+219, 364, 'D', 28, TEAL, True, 'middle')
    d.text(48, 429, 'D — генерация токенов уже начатых ответов между порциями входа.', 24)
    for x, name, body in [(48, 'max-model-len', ['Вход + генерация', 'одной истории']),
                           (424, 'max-num-seqs', ['Последовательности', 'в работе']),
                           (800, 'max-num-batched-tokens', ['Токены одного шага', 'планировщика'])]:
        d.rect(x, 464, 352, 108, GRAY, LINE)
        d.text(x+16, 496, name, 21, BLUE, True)
        d.text(x+16, 526, body, 20)
    d.footer('CUDA graphs уменьшают накладные расходы запусков, но не заменяют планировщик и занимают HBM.')
    d.save()


def speculation():
    d = Diagram('07-speculation', 'Черновик предлагает, основная модель проверяет',
                'Условный цикл speculative decoding. Три принятых токена — пример, не измеренная доля.', 850)
    d.text(48, 178, 'Черновик', 26, PURPLE, True)
    for i, token in enumerate(['A', 'B', 'C', 'D', 'E']):
        x = 288+i*168
        d.rect(x, 141, 142, 66, LILAC, PURPLE)
        d.text(x+71, 184, token, 30, PURPLE, True, 'middle')
        d.path(f'M {x+71} 209 V 260', MUTED, True)
    d.text(48, 308, ['Проверка', 'основной моделью'], 24, BLUE, True)
    for i, token in enumerate(['A', 'B', 'C', 'D', 'E']):
        x = 288+i*168
        d.rect(x, 272, 142, 66, MINT if i < 3 else SAND, TEAL if i < 3 else AMBER)
        d.text(x+71, 315, token if i < 3 else '×', 30, TEAL if i < 3 else AMBER, True, 'middle')
    d.text(504, 377, 'Приняты', 23, TEAL, True, 'middle')
    d.text(961, 377, 'D и хвост отклонены', 22, AMBER, True, 'middle')
    d.rect(48, 414, 1104, 100, PALE, BLUE)
    d.text(72, 455, 'В ответ: A, B, C + замена от основной модели', 29, BLUE, True)
    d.text(72, 490, 'Следующий цикл начинается с исправленного продолжения.', 23)
    d.text(48, 564, 'Измеряем вместе: принятие, стоимость черновика и итоговую скорость.', 26)
    d.rect(48, 610, 1104, 160, LILAC, PURPLE)
    d.text(80, 696, 'Время на токен ≈', 31, PURPLE, True)
    d.text(782, 663, 'черновик + проверка + накладные расходы', 26, INK, False, 'middle')
    d.path('M 460 685 H 1105', PURPLE, width=2)
    d.text(782, 729, 'число выданных токенов', 29, INK, True, 'middle')
    d.footer('Высокий acceptance не гарантирует ускорение. Gemma assistant проверяется отдельно от CPU KV-offload.')
    d.save()


def mig():
    d = Diagram('08-mig-mps', 'MIG — геометрия карты, MPS — совместное выполнение',
                'Целевая схема A30. Режим MIG включён заранее, разделы создаёт драйвер по заявкам.', 700)
    for x, title, body in [(48, 'GPUClass / GPUPool', 'Выбор GPU'), (334, 'DeviceClass', 'Классы профилей'),
                           (620, 'ResourceClaim', 'Заявка Pod'), (906, 'DRA-драйвер', 'Подготовка MIG')]:
        d.rect(x, 143, 246, 106, GRAY, LINE)
        d.text(x+16, 181, title, 22, BLUE, True)
        d.text(x+16, 218, body, 21)
        if x < 906:
            d.path(f'M {x+248} 197 H {x+284}', arrow=True)
    d.text(48, 301, 'A30 24 GB', 29, INK, True)
    d.rect(48, 327, 266, 230, PALE, BLUE)
    d.text(70, 370, '1g.6gb', 30, BLUE, True)
    d.text(70, 414, ['Эмбеддер', 'Отдельный', 'MIG-раздел'], 26)
    d.rect(330, 327, 540, 230, LILAC, PURPLE)
    d.text(352, 370, '2g.12gb + MPS', 30, PURPLE, True)
    d.card(350, 395, 244, 112, 'Эмбеддер', ['Квота SM + RAM'], '#ffffff', PURPLE)
    d.card(608, 395, 242, 112, 'Реранкер', ['Квота SM + RAM'], '#ffffff', PURPLE)
    d.text(352, 539, 'Один MIG UUID у обоих клиентов', 24, PURPLE)
    d.rect(886, 327, 266, 230, MINT, TEAL, dash=True)
    d.text(908, 370, '1g из 4', 30, TEAL, True)
    d.text(908, 414, ['Свободная доля', 'для следующей', 'заявки'], 25)
    d.text(48, 604, 'Удаление workload → освобождение claim → проверка доступной геометрии', 25, INK, True)
    d.footer('25% квоты MPS не равны 25% производительности. Размеры профилей — не точный объём памяти приложения.')
    d.save()


def platform():
    d = Diagram('09-platform', 'AI Inference: запуск по рецепту',
                'Рецепт задаёт настройки модели, платформа формирует план и запускает сервис.', 700)
    d.card(48, 150, 322, 122, 'AI Models', ['Модель и ревизия', 'Проверенные файлы'])
    d.card(418, 150, 734, 122, 'Рецепт + оборудование + стратегия', ['Совместимый runtime, параметры, измеренный бюджет памяти', 'Latency / Throughput влияют на выбор плана'], MINT, TEAL)
    d.path('M 210 274 V 312 H 171 V 348', arrow=True)
    d.path('M 786 274 V 312 H 210', TEAL)
    for x, title, body in [(48, 'InferenceService', 'Желаемое состояние'), (334, 'План', 'Параметры и ресурсы'),
                           (620, 'Claim + Pod', 'DRA и движок'), (906, 'API', 'Проверка ответа')]:
        d.rect(x, 350, 246, 117, PALE, BLUE)
        d.text(x+16, 391, title, 25, BLUE, True)
        d.text(x+16, 432, body, 20)
        if x < 906:
            d.path(f'M {x+248} 408 H {x+284}', arrow=True)
    d.rect(48, 506, 1104, 128, MINT, TEAL)
    d.text(72, 545, 'Все настройки эксперимента — в рецепте', 29, TEAL, True)
    d.text(72, 581, ['FP8 KV, prefix cache, chunked prefill и CUDA graphs.',
                     'Расширенные рецепты: KV-кэш в RAM, Gemma assistant, Qwen TP2 + MTP.'], 22)
    d.footer('Параметры задаём в рецепте; размещением и запуском управляет AI Inference.')
    d.save()


def tp2():
    d = Diagram('10-tp2', 'Qwen TP2: две карты, один экземпляр модели',
                'Один запрос обрабатывается двумя GPU. Размещение и скорость проверяются отдельно.', 620)
    d.rect(48, 145, 1104, 306, '#ffffff', BLUE)
    d.text(72, 183, 'ОДНА НОДА / ОДИН POD / ОДИН API', 22, BLUE, True)
    d.card(72, 214, 390, 167, 'H100 №1', ['Часть весов', 'Локальные состояния', 'TP rank 0'])
    d.card(738, 214, 390, 167, 'H100 №2', ['Часть весов', 'Локальные состояния', 'TP rank 1'])
    d.text(600, 258, 'Обмен NCCL', 26, BLUE, True, 'middle')
    d.path('M 464 300 H 736', BLUE, True)
    d.path('M 736 344 H 464', BLUE, True)
    d.text(600, 422, 'Физическую связь GPU проверяем по топологии стенда.', 23, MUTED, anchor='middle')
    d.text(48, 507, 'Оба процесса участвуют в вычислении одного ответа.', 30, INK, True)
    d.text(48, 547, 'TP=2 не создаёт две реплики и не превращает HBM в единый прозрачный пул.', 24)
    d.footer('Qwen и Gemma — разные модели: результаты Qwen не продолжают сравнение настроек Gemma A/B.')
    d.save()


def qwen_transition():
    d = Diagram('17-qwen-transition', 'От двух Gemma к одному сервису Qwen TP2',
                'Сначала освобождаем обе H100, затем проверяем заявку, размещение и ответ модели.', 1040)
    d.rect(48, 146, 1104, 218, GRAY, LINE)
    d.text(72, 184, '1. ОСВОБОДИТЬ ОБЕ H100 ОТ ПРЕЖНИХ СЕРВИСОВ', 23, MUTED, True)
    for x, title, body in [(72, 'Gemma A', ['replicaCount: 0', 'GitOps: commit → sync']),
                            (432, 'Gemma B', ['replicaCount: 0', 'GitOps: commit → sync']),
                            (792, 'Gemma AI Inference', ['Если ещё запущена', 'Удалить InferenceService'])]:
        d.rect(x, 211, 336, 126, '#ffffff', LINE)
        d.text(x+18, 249, title, 26, INK, True)
        d.text(x+18, 284, body, 22)
    d.path('M 600 366 V 389', MUTED, True)
    d.rect(180, 392, 840, 55, MINT, TEAL)
    d.text(600, 427, 'Обе H100 доступны; прежние Pod и claims освобождены', 24, TEAL, True, 'middle')
    d.text(48, 490, '2. ЗАЯВКА ДОЛЖНА ВЫДЕЛИТЬ ДВЕ ПОЛНЫЕ GPU', 23, BLUE, True)
    d.card(48, 519, 316, 146, 'DeviceClass', ['Существующий класс', 'для полных GPU'])
    d.card(442, 519, 310, 146, 'ResourceClaim', ['count: 2', 'Одна заявка для Pod'])
    d.path('M 366 588 H 440', BLUE, True)
    d.path('M 754 588 H 832', BLUE, True)
    d.rect(834, 519, 318, 146, MINT, TEAL)
    d.text(856, 558, 'Одна нода', 27, TEAL, True)
    for x, label in [(856, 'H100 №1'), (1005, 'H100 №2')]:
        d.rect(x, 582, 125, 57, '#ffffff', TEAL)
        d.text(x+62.5, 618, label, 21, TEAL, True, 'middle')
    d.text(48, 718, '3. ПРОВЕРИТЬ API И ПОДКЛЮЧИТЬ ЕГО К ПРЕЖНЕМУ ШЛЮЗУ', 23, BLUE, True)
    d.card(48, 750, 316, 128, 'Тот же Open WebUI', ['Чат и базы знаний'])
    d.card(442, 750, 310, 128, 'Тот же Bifrost', ['Маршрут к Qwen'])
    d.card(834, 750, 318, 128, 'Qwen TP2', ['Один Pod, один сервис', 'Проверенный ответ API'], MINT, TEAL)
    d.path('M 366 810 H 440', BLUE, True)
    d.path('M 754 810 H 832', BLUE, True)
    d.path('M 993 667 V 748', TEAL, True)
    d.rect(48, 911, 1104, 75, LILAC, PURPLE)
    d.text(72, 942, 'WebUI и Bifrost сохраняются; меняется модель за маршрутом.', 27, PURPLE, True)
    d.text(72, 972, 'План с одной GPU не выполняет требование TP2, даже если параметр запуска равен 2.', 22)
    d.footer('Классы берём у контроллера GPU. Для перехода не удаляем PVC, веса моделей и GPUClass / GPUPool.')
    d.save()


def qwen_mtp():
    d = Diagram('18-qwen-mtp', 'MTP в Qwen: проверка предложенного продолжения',
                'Условный цикл с четырьмя предложениями. Число принятых токенов здесь не является замером.', 930)
    d.rect(48, 143, 1104, 76, LILAC, PURPLE)
    d.text(72, 176, 'Один сервис Qwen TP2', 27, PURPLE, True)
    d.text(72, 206, 'Встроенный MTP-блок предлагает токены; основная модель проверяет их пакетом.', 23)
    d.text(48, 283, ['MTP', 'предлагает'], 25, PURPLE, True)
    for i, token in enumerate(['t₁', 't₂', 't₃', 't₄']):
        x = 344+i*202
        d.rect(x, 263, 174, 68, LILAC, PURPLE)
        d.text(x+87, 309, token, 32, PURPLE, True, 'middle')
        d.path(f'M {x+87} 333 V 387', MUTED, True)
        if i < 3:
            d.path(f'M {x+176} 297 H {x+200}', PURPLE, True, width=2)
    d.text(48, 417, ['Основная модель', 'проверяет'], 25, BLUE, True)
    for i, (label, fill, color) in enumerate([('Принят', MINT, TEAL), ('Принят', MINT, TEAL),
                                             ('Отказ', SAND, AMBER), ('Отбросить', GRAY, MUTED)]):
        x = 344+i*202
        d.rect(x, 389, 174, 68, fill, color)
        d.text(x+87, 433, label, 25, color, True, 'middle')
    d.path('M 344 475 V 486 H 720 V 475', TEAL)
    d.text(532, 517, 'Принятый префикс', 23, TEAL, True, 'middle')
    d.text(936, 500, ['Первый отказ', 'отменяет остаток'], 22, AMBER, anchor='middle')
    d.rect(48, 550, 1104, 108, PALE, BLUE)
    d.text(72, 591, 'В ответ', 27, BLUE, True)
    d.text(72, 628, 'и в следующий цикл', 22, MUTED)
    for x, token, fill, color in [(344, 't₁', MINT, TEAL), (546, 't₂', MINT, TEAL),
                                  (748, 't*', PALE, BLUE)]:
        d.rect(x, 570, 174, 68, fill, color)
        d.text(x+87, 616, token, 32, color, True, 'middle')
    d.text(954, 597, ['Исправление', 'после отказа'], 21, BLUE)
    d.path('M 532 524 V 548', TEAL, True)
    d.path('M 835 459 V 548', BLUE, True)
    d.rect(48, 699, 416, 124, MINT, TEAL)
    d.text(72, 734, 'Доля принятия', 26, TEAL, True)
    d.text(256, 771, 'принятые токены', 23, INK, anchor='middle')
    d.path('M 104 782 H 408', TEAL, width=2)
    d.text(256, 810, 'предложенные токены', 23, INK, anchor='middle')
    d.rect(504, 699, 648, 124, LILAC, PURPLE)
    d.text(528, 734, 'Цена выданного токена', 26, PURPLE, True)
    d.text(828, 771, 'время предложения + проверки + обмена', 24, INK, anchor='middle')
    d.path('M 562 782 H 1094', PURPLE, width=2)
    d.text(828, 810, 'выданные токены', 23, INK, anchor='middle')
    d.text(48, 872, 'Acceptance ≠ ускорение: сравниваем TPOT и токены/с при той же нагрузке.', 26, INK, True)
    d.footer('Старт: num_speculative_tokens=1. Qwen ± MTP: те же вход, выход, TP2 и кэш.')
    d.save()


def qwen_capacity():
    d = Diagram('19-qwen-capacity', 'Qwen: сколько запросов выдерживает сервис',
                'Увеличиваем число одновременных запросов и проверяем пределы качества обслуживания.', 970)
    d.rect(48, 146, 1104, 77, PALE, BLUE)
    d.text(72, 179, 'Один профиль нагрузки на всю серию', 27, BLUE, True)
    d.text(72, 209, 'Фиксируем вход, выход, модель, TP2, настройки MTP и состояние кэша.', 24)
    d.text(48, 269, 'ОДНОВРЕМЕННЫЕ ЗАПРОСЫ C', 23, MUTED, True)
    for i, count in enumerate([1, 2, 4, 8, 16, 32, 50]):
        x = 48+i*162
        d.rect(x, 292, 132, 79, PALE, BLUE)
        d.text(x+66, 345, str(count), 41, BLUE, True, 'middle')
        if i < 6:
            d.path(f'M {x+134} 332 H {x+160}', BLUE, True, width=2)
    d.path('M 48 385 V 397 H 1152 V 385', MUTED, width=2)
    d.text(600, 433, 'На каждой ступени сохраняем результаты и проверяем четыре сигнала', 24, MUTED, anchor='middle')
    for x, title, body, fill, color in [
            (48, 'Очередь', ['Запросы в ожидании', 'Растёт или стабильна?'], SAND, AMBER),
            (329, 'TTFT', ['До первого токена', 'p50 / p95 / p99'], PALE, BLUE),
            (610, 'TPOT', ['Время на токен', 'p50 / p95 / p99'], LILAC, PURPLE),
            (891, 'Ошибки', ['HTTP / timeout / OOM', 'Учитываем все отказы'], SAND, AMBER)]:
        d.rect(x, 461, 261, 144, fill, color)
        d.text(x+20, 503, title, 28, color, True)
        d.text(x+20, 546, body, 21)
    d.path('M 600 610 V 635 H 314 V 657', TEAL, True)
    d.path('M 600 635 H 886 V 657', AMBER, True)
    d.card(48, 659, 532, 146, 'Пороги соблюдены', ['Переходим к следующей ступени.', 'Пределы задаём до начала серии.'], MINT, TEAL)
    d.card(620, 659, 532, 146, 'Нарушен хотя бы один порог', ['Останавливаем рост нагрузки.', 'Граница — последняя устойчивая ступень.'], SAND, AMBER)
    d.text(48, 854, '50 пользователей ≠ 50 полных окон контекста', 31, INK, True)
    d.text(48, 891, 'Длина контекста ограничивает одну историю; ёмкость сервиса подтверждает нагрузка.', 24)
    d.footer('Ступени — план проверки, не результаты. Успех на 50 одновременных запросах пока не заявлен.')
    d.save()


if __name__ == '__main__':
    for build in (topology, latency, memory, ab, offload, scheduler, speculation, mig, platform, tp2, gemma_formula, gitops, attention, prefixes, gptoss_formula, rag, qwen_transition, qwen_mtp, qwen_capacity):
        build()
    print('Built 19 workshop SVGs in assets/. No external images, fonts or services.')
