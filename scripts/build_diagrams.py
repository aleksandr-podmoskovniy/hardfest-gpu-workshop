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
    d = Diagram('01-topology', 'Один чат, разные способы запуска модели',
                'Схема подключения сервисов. Проверенные этапы перечислены в STATUS.md.', 710)
    d.card(48, 160, 250, 130, 'Open WebUI', ['Чаты и базы знаний', 'Пароль или OIDC'])
    d.card(378, 160, 254, 130, 'HA Bifrost', ['Маршруты к моделям', 'Учёт и контроль API'])
    d.path('M 298 222 H 376', arrow=True)
    d.text(337, 204, 'API', 20, MUTED, anchor='middle')
    d.card(728, 146, 424, 114, 'H100 №1 — Gemma A', ['Ручной vLLM → сервис платформы'])
    d.card(728, 294, 424, 114, 'H100 №2 — Gemma B', ['Та же модель, другие настройки'])
    d.path('M 632 205 H 726', arrow=True)
    d.path('M 674 205 V 351 H 726', arrow=True)
    d.card(728, 442, 424, 126, 'A30 — сервисы поиска', ['Эмбеддер и реранкер', 'Динамический MIG + MPS'], MINT, TEAL)
    d.path('M 674 351 V 502 H 726', TEAL, True)
    d.card(48, 380, 250, 144, 'База знаний', ['Документы и индекс', 'сохраняются при', 'смене сервера LLM'], GRAY, MUTED)
    d.path('M 174 290 V 378', MUTED, True)
    d.rect(378, 442, 254, 126, LILAC, PURPLE)
    d.text(400, 479, 'OIDC / MCP', 26, PURPLE, True)
    d.text(400, 516, ['Персональные права', 'на инструменты'], 21)
    d.path('M 505 290 V 440', PURPLE, True, True)
    d.rect(48, 606, 1104, 50, GRAY, LINE)
    d.text(70, 638, 'Бонус после A/B: обе H100 освобождаются под один Qwen TP2.', 24)
    d.footer('Внешний пользователь не подключается к vLLM напрямую. Стрелки показывают обращения к сервисам.')
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
    d = Diagram('03-memory', 'Карта занята памятью, даже когда вычисления простаивают',
                'Учебный расчёт из презентации: 96 GiB, бюджет движка 90%. Не профиль Gemma.', 630)
    x = 48
    for gib, fill, color in [(64, PALE, BLUE), (6, LILAC, PURPLE), (16.4, MINT, TEAL), (9.6, GRAY, MUTED)]:
        w = 1104*gib/96
        d.rect(x, 158, w, 75, fill, color, 0)
        x += w
    for x, title, sub, color in [(48, '64 GiB', 'Веса', BLUE), (366, '6 GiB', 'Буферы', PURPLE),
                                  (646, '16,4 GiB', 'KV-пул', TEAL), (958, '9,6 GiB', 'Вне бюджета', MUTED)]:
        d.text(x, 279, title, 30, color, True)
        d.text(x, 312, sub, 23, MUTED)
    d.text(48, 370, '96 × 0,90 − 64 − 6 = 16,4 GiB для историй запросов', 29, INK, True)
    for i in range(4):
        x = 48+i*280
        d.rect(x, 405, 264, 112, MINT if i < 3 else SAND, TEAL if i < 3 else AMBER, dash=i == 3)
        d.text(x+22, 447, f'История {i+1}', 26, TEAL if i < 3 else AMBER, True)
        d.text(x+22, 482, '≈ 4,50 GiB' if i < 3 else 'Не помещается', 25)
    d.text(48, 563, 'Три истории ≈ 13,51 GiB. Четыре ≈ 18,02 GiB — больше пула.', 25)
    d.footer('4,5044 GiB — полезные KV-данные GPT-OSS-120B при 128K в BF16. Округления учитываются отдельно.')
    d.save()


def ab():
    d = Diagram('04-ab', 'Меняем настройки, а не условия задачи',
                'Одна версия vLLM, одинаковые BF16-веса, разные H100.', 660)
    d.rect(48, 142, 1104, 68, GRAY, LINE)
    d.text(600, 185, 'Окно 65 536      Вход 32 768      Выход 2 048', 30, INK, True, 'middle')
    d.card(48, 244, 532, 282, 'A — a-chunked', [], GRAY, MUTED)
    d.card(620, 244, 532, 282, 'B — b-tuned', [], PALE, BLUE)
    for x, lines in [(72, ['KV: BF16', 'Prefix cache: выключен', 'CUDA graphs / compile: выключены', 'Attention: FlashAttention 4']),
                      (644, ['KV: FP8', 'Prefix cache: включён', 'CUDA graphs / compile: включены', 'Attention: Triton'])]:
        for i, line in enumerate(lines):
            d.text(x, 339+i*47, line, 25)
    d.rect(48, 550, 1104, 58, MINT, TEAL)
    d.text(600, 588, 'У обоих: prefill по 4096, до 32 последовательностей, веса только в GPU', 24, TEAL, True, 'middle')
    d.footer('Чанкирование оставлено у A для вместимости. KV в RAM здесь выключен; его проверяем отдельно.')
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
                'Условный цикл speculative decoding. Три принятых токена — пример, не измеренная доля.', 620)
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
    d = Diagram('09-platform', 'Платформа воспроизводит проверенную конфигурацию',
                'Схема управления: от модели и рецепта до ответа API. Не подтверждение готовности конкретного модуля.', 660)
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
    d.rect(48, 506, 1104, 94, SAND, AMBER)
    d.text(72, 545, 'Throughput — не переключатель всех оптимизаций.', 29, AMBER, True)
    d.text(72, 581, 'KV-offload и MTP должны поддерживаться рецептом и реально появиться в аргументах движка.', 22)
    d.footer('Не патчим Deployment контроллера вручную. Проверяем цепочку до API и подключаем её к тому же Bifrost.')
    d.save()


def tp2():
    d = Diagram('10-tp2', 'Две карты — один экземпляр большой модели',
                'Финальный эксперимент после освобождения обеих H100. Запуск и скорость проверяются отдельно.', 500)
    d.card(48, 164, 410, 174, 'H100 №1', ['Часть весов и KV', 'Процесс TP rank 0'])
    d.card(742, 164, 410, 174, 'H100 №2', ['Часть весов и KV', 'Процесс TP rank 1'])
    d.path('M 460 245 H 740', BLUE, True)
    d.path('M 740 280 H 460', BLUE, True)
    d.text(600, 215, 'NVLink / NCCL', 26, BLUE, True, 'middle')
    d.text(600, 316, 'Обмен на шагах модели', 20, MUTED, anchor='middle')
    d.text(48, 393, 'TP=2 не означает две независимые реплики.', 30, INK, True)
    d.text(48, 433, 'Проверяем архитектуру, квантование, память и связь карт до нагрузки на 50 сессий.', 23)
    d.footer('NVLink ускоряет обмен между GPU. CPU KV-offload использует другую иерархию памяти.')
    d.save()


if __name__ == '__main__':
    for build in (topology, latency, memory, ab, offload, scheduler, speculation, mig, platform, tp2):
        build()
    print('Built 10 workshop SVGs in assets/. No external images, fonts or services.')
