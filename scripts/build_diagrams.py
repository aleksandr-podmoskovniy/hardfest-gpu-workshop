#!/usr/bin/env python3
"""Build self-contained workshop SVGs using a shared layout and colour system."""
from html import escape
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, LINE = "#17243b", "#536278", "#d9e2ee"
BLUE, PALE = "#1554dc", "#eef4ff"
TEAL, MINT = "#087d78", "#eaf7f3"
PURPLE, LILAC = "#7041b8", "#f3eefb"
AMBER, SAND = "#946017", "#fff5e4"
GRAY = "#f5f7fa"


class Box(NamedTuple):
    x: float
    y: float
    w: float
    h: float

    def port(self, side, fraction=0.5):
        """Keep each connector six pixels clear of the card outline."""
        return {
            'left': (self.x-6, self.y+self.h*fraction),
            'right': (self.x+self.w+6, self.y+self.h*fraction),
            'top': (self.x+self.w*fraction, self.y-6),
            'bottom': (self.x+self.w*fraction, self.y+self.h+6),
        }[side]


class Diagram:
    def __init__(self, name, title, subtitle):
        self.name = name
        self.parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" '
            'viewBox="0 0 1200 760" role="img" aria-labelledby="title desc" data-design="hardfest-v2">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>',
            '<defs>' + ''.join(
                f'<marker id="arrow-{color[1:]}" viewBox="0 0 10 10" refX="10" refY="5" '
                f'markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto">'
                f'<path d="M0 0 L10 5 L0 10 Z" fill="{color}"/></marker>'
                for color in (BLUE, TEAL, PURPLE, MUTED)) + '</defs>',
            '<style>text{font-family:Arial,Helvetica,sans-serif;font-variant-numeric:tabular-nums}</style>',
        ]
        self.rect(1, 1, 1198, 758, "#ffffff", LINE, 20)
        self.rect(48, 38, 5, 34, BLUE, BLUE, 2)
        self.text(70, 64, title, 32, bold=True, width=1082)
        self.text(48, 104, subtitle, 20, MUTED, width=1104)
        self.path("M48 128 H1152", LINE, width=1)

    def rect(self, x, y, w, h, fill=GRAY, stroke=LINE, radius=12, dash=False, node=False):
        dash = ' stroke-dasharray="7 5"' if dash else ''
        node = ' data-node="true"' if node else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}{node}/>')
        return Box(x, y, w, h)

    def text(self, x, y, lines, size=24, color=INK, bold=False, anchor="start", width=None):
        lines = [lines] if isinstance(lines, str) else lines
        weight = ' font-weight="700"' if bold else ''
        bound = f' data-max-width="{width}"' if width is not None else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                          f'text-anchor="{anchor}"{weight}{bound}>')
        for i, line in enumerate(lines):
            self.parts.append(f'<tspan x="{x}" dy="{0 if i == 0 else size * 1.4}">{escape(line)}</tspan>')
        self.parts.append('</text>')

    def path(self, path, color=BLUE, arrow=False, dash=False, width=2, connector=False):
        end = f' marker-end="url(#arrow-{color[1:]})"' if arrow else ''
        dashed = ' stroke-dasharray="6 5"' if dash else ''
        tag = ' data-connector="true"' if connector else ''
        self.parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}" '
                          f'stroke-linejoin="round" stroke-linecap="round"{end}{dashed}{tag}/>')

    def connector(self, points, color=BLUE, dash=False, arrow=True):
        """Orthogonal routing with small rounded elbows and a visible arrow shaft."""
        if len(points) < 2:
            raise ValueError('A connector needs at least two points')
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if (ax == bx) == (ay == by):
                raise ValueError('Connector segments must be nonzero and orthogonal')
        if arrow and sum(abs(a-b) for a, b in zip(points[-2], points[-1])) < 20:
            raise ValueError('Leave at least 20 pixels before an arrow tip')
        route = f'M{points[0][0]} {points[0][1]}'
        for previous, point, following in zip(points, points[1:], points[2:]):
            before = sum(abs(a-b) for a, b in zip(previous, point))
            after = sum(abs(a-b) for a, b in zip(following, point))
            radius = min(8, before/3, after/3)
            start = tuple(p+(a-p)*radius/before for p, a in zip(point, previous))
            end = tuple(p+(a-p)*radius/after for p, a in zip(point, following))
            route += f' L{start[0]} {start[1]} Q{point[0]} {point[1]} {end[0]} {end[1]}'
        route += f' L{points[-1][0]} {points[-1][1]}'
        self.path(route, color, arrow, dash, connector=True)

    def connect(self, source, target, sides=('right', 'left'), via=(), color=BLUE, dash=False):
        self.connector([source.port(sides[0]), *via, target.port(sides[1])], color, dash)

    def card(self, x, y, w, h, title, body=(), fill=PALE, color=BLUE, compact=False):
        box = self.rect(x, y, w, h, fill, node=True)
        self.text(x+24, y+(36 if compact else 42), title, 24 if compact else 25,
                  color, True, width=w-48)
        if body:
            self.text(x+24, y+(70 if compact else 82), body, 20 if compact else 22, width=w-48)
        return box

    def band(self, y, title, body, fill=PALE, color=BLUE):
        self.card(48, y, 1104, 100, title, [body], fill, color)

    def footer(self, note):
        self.path("M48 698 H1152", LINE, width=1)
        self.text(48, 731, note, 18, MUTED, width=1104)

    def save(self):
        (ROOT / "assets" / f"{self.name}.svg").write_text("\n".join(self.parts + ['</svg>']) + '\n')


def topology():
    d = Diagram('01-topology', 'Один чат — несколько моделей',
                'Open WebUI и GPU-сервисы могут работать в разных кластерах.')
    d.rect(48, 160, 320, 518)
    d.rect(404, 160, 748, 518, '#ffffff')
    d.text(72, 194, 'КЛАСТЕР WEBUI', 18, MUTED, True)
    d.text(428, 194, 'GPU-КЛАСТЕР', 18, MUTED, True)
    webui = d.card(72, 328, 272, 156, 'Open WebUI', ['Чат и голос', 'Пароль / OIDC'])
    knowledge = d.card(72, 542, 272, 112, 'Базы знаний', ['Документы и индекс'], MINT, TEAL)
    gateway = d.card(428, 328, 280, 156, 'HA Bifrost', ['Ключи, квоты и учёт', 'Маршруты моделей'])
    d.connect(webui, knowledge, ('bottom', 'top'), color=TEAL)
    d.connect(webui, gateway)
    services = [
        d.card(800, 214, 328, 92, 'Gemma A — Base', ['H100 №1'], compact=True),
        d.card(800, 330, 328, 92, 'Gemma B — Tune', ['H100 №2'], compact=True),
        d.card(800, 446, 328, 92, 'A30 / MIG + MPS', ['Эмбеддер и реранкер'], MINT, TEAL, compact=True),
        d.card(800, 562, 328, 92, 'Kubernetes MCP', ['Только администратор'], LILAC, PURPLE, compact=True),
    ]
    d.connector([gateway.port('right'), (752, 406)], arrow=False)
    d.connector([(752, 260), (752, 608)], arrow=False)
    for service, color, dashed in zip(services, (BLUE, BLUE, TEAL, PURPLE), (False, False, False, True)):
        y = service.port('left')[1]
        d.connector([(752, y), service.port('left')], color, dashed)
    d.footer('При переходе к Qwen меняется модель за шлюзом; чат, пользователи и базы знаний сохраняются.')
    d.save()


def latency():
    d = Diagram('02-latency', 'Из чего складывается время ответа',
                'Очередь, обработка входа и генерация — разные части задержки.')
    for x, w, label, fill, color in [(48, 220, 'Очередь', SAND, AMBER),
            (268, 340, 'Prefill', PALE, BLUE), (608, 240, 'Рассуждение', LILAC, PURPLE),
            (848, 304, 'Текст ответа', MINT, TEAL)]:
        d.rect(x, 220, w, 88, fill, '#ffffff', 0)
        d.text(x+w/2, 274, label, 25, color, True, 'middle')
    for x, label in [(48, 'Отправка'), (608, 'Первый токен'), (848, 'Первое слово ответа')]:
        d.path(f'M{x} 188 V330', MUTED, dash=True, width=1)
        d.text(x, 359, label, 20, MUTED)
    for y, end, label, color in [(434, 608, 'TTFT', BLUE),
            (503, 848, 'До первого текста ответа', PURPLE), (572, 1152, 'Полное время запроса', TEAL)]:
        d.path(f'M48 {y-10} V{y} H{end} V{y-10}', color)
        d.text((48+end)/2, y-18, label, 25, color, True, 'middle')
    d.text(48, 654, 'p95(TTFT) − p95(очереди) ≠ p95(prefill)', 30, bold=True)
    d.footer('Шкала условная. Рассуждение может отсутствовать; сеть и движок добавляют накладные расходы.')
    d.save()


def memory():
    d = Diagram('03-memory', 'Память длинного контекста',
                'Gemma 4 31B: полезные KV одной истории, без округления блоков и рабочих буферов.')
    d.rect(48, 160, 1104, 126, PALE)
    d.text(600, 213, 'HBM × бюджет − веса − runtime = KV-пул', 36, BLUE, True, 'middle')
    d.text(600, 256, 'Измеренные веса Gemma A и B: 57,91 GiB', 24, anchor='middle')
    for x, label in [(76, 'КОНТЕКСТ'), (480, 'BF16 / 2 БАЙТА'), (940, 'FP8 / 1 БАЙТ')]:
        d.text(x, 338, label, 20, MUTED, True, 'start' if x == 76 else 'middle')
    for y, context, bf16, fp8 in [(365, '64K', '5,78 GiB', '2,89 GiB'),
            (465, '128K', '10,78 GiB', '5,39 GiB'), (565, '256K', '20,78 GiB', '10,39 GiB')]:
        d.rect(48, y, 1104, 82)
        d.rect(744, y, 408, 82, MINT, MINT)
        d.text(76, y+54, context, 34, bold=True)
        d.text(480, y+54, bf16, 36, bold=True, anchor='middle')
        d.text(940, y+54, fp8, 36, TEAL, True, 'middle')
    d.footer('64K — сравнение A/B; 128K — опыт вместимости. 256K здесь только расчёт, не результат запуска.')
    d.save()


def ab():
    d = Diagram('04-ab', 'Gemma A — Base / Gemma B — Tune',
                'Одинаковые веса, версия движка и нагрузка. Меняется конфигурация.')
    d.rect(48, 160, 1104, 76)
    for x, label in [(232, 'Контекст 65 536'), (600, 'Вход 32 768'), (968, 'Выход 2 048')]:
        d.text(x, 208, label, 27, bold=True, anchor='middle')
    for x, title, fill, color, lines in [
            (48, 'A / Base', GRAY, MUTED, ['KV: BF16', 'Prefix cache: выключен', 'CUDA graphs: выключены', 'Attention: FlashAttention 4']),
            (620, 'B / Tune', PALE, BLUE, ['KV: FP8', 'Prefix cache: включён', 'CUDA graphs: включены', 'Attention: Triton'])]:
        d.card(x, 270, 532, 278, title, (), fill, color)
        for i, line in enumerate(lines):
            d.text(x+24, 365+i*46, line, 26)
    d.band(579, 'У обоих: prefill по 4096 и до 32 последовательностей',
           'Веса остаются на GPU; KV-offload на этом этапе выключен.', MINT, TEAL)
    d.footer('A намеренно отключает оптимизации. Это не настройки по умолчанию vLLM 0.30.')
    d.save()


def offload():
    d = Diagram('05-kv-ram', 'Сохранить KV в RAM и вернуть на GPU',
                'Повтор документа после вытеснения: X → другие документы → X.')
    d.text(260, 180, 'GPU', 22, BLUE, True, 'middle')
    d.text(920, 180, 'RAM', 22, TEAL, True, 'middle')
    pairs = []
    for y, title, gpu, ram in [(216, '1. Обработать X', 'KV документа X', 'Копия KV документа X'),
            (360, '2. Вытеснить X', 'KV документов Y, Z', 'Копия X остаётся'),
            (504, '3. Повторить X', 'KV X снова на GPU', 'Найденные блоки X')]:
        d.text(48, y-14, title, 19, MUTED)
        pairs.append((d.card(48, y, 432, 96, gpu),
                      d.card(720, y, 432, 96, ram, fill=MINT, color=TEAL)))
    d.connect(*pairs[0], color=TEAL)
    d.text(600, 245, 'Запись', 22, TEAL, anchor='middle')
    d.text(600, 415, 'Другие запросы', 21, MUTED, anchor='middle')
    d.connect(pairs[2][1], pairs[2][0], ('left', 'right'))
    d.text(600, 533, 'Чтение', 22, BLUE, anchor='middle')
    d.text(48, 660, 'Проверяем CPU → GPU байты и отсутствие локального cache hit.', 26, bold=True)
    d.footer('RAM хранит блоки между обращениями. Во время вычисления рабочий KV должен находиться на GPU.')
    d.save()


def scheduler():
    d = Diagram('06-scheduler', 'Чанкирование длинного входа',
                'Порции prefill чередуются с генерацией уже начатых ответов.')
    d.text(48, 181, 'БЕЗ ЧАНКИРОВАНИЯ', 19, MUTED, True)
    d.rect(48, 206, 760, 76, PALE)
    d.text(428, 254, 'Весь длинный prefill', 27, BLUE, True, 'middle')
    d.rect(832, 206, 320, 76, SAND)
    d.text(992, 254, 'Decode ждёт', 26, AMBER, True, 'middle')
    d.text(48, 343, 'С ЧАНКИРОВАНИЕМ', 19, MUTED, True)
    for i in range(4):
        x = 48+i*280
        d.rect(x, 368, 164, 76, PALE)
        d.text(x+82, 416, f'Prefill {i+1}', 24, BLUE, True, 'middle')
        d.rect(x+176, 368, 88, 76, MINT)
        d.text(x+220, 416, 'D', 27, TEAL, True, 'middle')
    d.text(48, 485, 'D — шаг decode. Размеры блоков условные, не длительность измерений.', 22, MUTED)
    for x, name, body in [(48, 'max-model-len', ['Вход + ответ', 'одной истории']),
            (424, 'max-num-seqs', ['Последовательности', 'в работе']),
            (800, 'max-num-batched-tokens', ['Бюджет токенов', 'одного шага'])]:
        d.rect(x, 528, 352, 130)
        d.text(x+20, 564, name, 21, BLUE, True)
        d.text(x+20, 602, body, 22)
    d.footer('Меньше порция — короче отдельный шаг, но больше шагов для всего документа. Проверяем TTFT и ITL.')
    d.save()


def speculation():
    d = Diagram('07-speculation', 'Черновик предлагает — модель проверяет',
                'Принимается непрерывный префикс; после первого отказа хвост отбрасывается.')
    for y, title in [(215, 'Черновик'), (340, 'Проверка')]:
        d.text(48, y+41, title, 25, PURPLE if y == 215 else BLUE, True)
        for i, token in enumerate(['A', 'B', 'C', 'D', 'E']):
            x = 312+i*170
            fill, color = (LILAC, PURPLE) if y == 215 else ((MINT, TEAL) if i < 3 else (SAND, AMBER))
            d.rect(x, y, 150, 66, fill, node=True)
            d.text(x+75, y+43, token if y == 215 or i < 3 else '×', 30, color, True, 'middle')
            if y == 215:
                d.connect(Box(x, y, 150, 66), Box(x, 340, 150, 66), ('bottom', 'top'), color=MUTED)
    d.band(448, 'В ответ: A, B, C + исправление основной модели',
           'Следующий цикл начинается с принятого и исправленного продолжения.')
    d.rect(48, 580, 1104, 94, LILAC)
    d.text(72, 635, 'Время на токен ≈', 29, PURPLE, True)
    d.text(798, 613, 'черновик + проверка + накладные расходы', 24, anchor='middle')
    d.path('M456 628 H1128', PURPLE, width=1.5)
    d.text(798, 660, 'число выданных токенов', 25, bold=True, anchor='middle')
    d.footer('Принятие токенов на схеме условное. Стоимость черновика тоже входит во время ответа.')
    d.save()


def mig():
    d = Diagram('08-mig-mps', 'MIG делит карту, MPS делит её раздел',
                'A30: режим MIG включён заранее; геометрию создаёт DRA-драйвер по заявкам.')
    for x, title, body in [(48, 'GPUClass / GPUPool', 'Выбор GPU'), (336, 'DeviceClass', 'Профили'),
            (624, 'ResourceClaim', 'Запрос ресурсов'), (912, 'DRA-драйвер', 'Создание раздела')]:
        node = d.rect(x, 160, 240, 110, node=True)
        d.text(x+16, 203, title, 20, BLUE, True)
        d.text(x+16, 242, body, 21)
        if x < 912:
            d.connect(node, Box(x+288, 160, 240, 110))
    d.text(48, 324, 'A30 / 24 GB', 26, bold=True)
    d.card(48, 352, 264, 230, '1g.6gb', ['Эмбеддер', 'Отдельный MIG'])
    d.card(332, 352, 536, 230, '2g.12gb + MPS', (), LILAC, PURPLE)
    d.card(352, 429, 238, 100, 'Клиент 1', ['Эмбеддер'], '#ffffff', PURPLE)
    d.card(610, 429, 238, 100, 'Клиент 2', ['Реранкер'], '#ffffff', PURPLE)
    d.text(600, 561, 'Один MIG UUID у обоих', 23, PURPLE, anchor='middle')
    d.rect(888, 352, 264, 230, MINT, TEAL, dash=True)
    d.text(912, 394, 'Свободно', 25, TEAL, True)
    d.text(912, 439, ['1g из 4', 'Для следующей', 'заявки'], 23)
    d.text(48, 643, 'Завершение Pod → освобождение claim → проверка геометрии', 27, bold=True)
    d.footer('Целевая схема размещения. Квота MPS не гарантирует долю скорости; аппаратную изоляцию даёт MIG.')
    d.save()


def platform():
    d = Diagram('09-platform', 'AI Inference: запуск по рецепту',
                'Параметры модели и ресурсы описаны один раз; размещением управляет платформа.')
    models = d.card(48, 160, 322, 142, 'AI Models', ['Модель и ревизия', 'Проверенные файлы'])
    recipe = d.card(418, 160, 734, 142, 'Рецепт + оборудование + стратегия',
           ['Runtime, параметры движка и бюджет памяти', 'Latency / Throughput'], MINT, TEAL)
    d.connect(models, Box(48, 374, 240, 120), ('bottom', 'top'), via=((209, 338), (168, 338)))
    d.connect(recipe, Box(336, 374, 240, 120), ('bottom', 'top'),
              via=((785, 338), (456, 338)), color=TEAL)
    for x, title, body in [(48, 'InferenceService', 'Заказ сервиса'), (336, 'План', 'Параметры и GPU'),
            (624, 'Claim + Pod', 'DRA и движок'), (912, 'API', 'Ответ модели')]:
        node = d.rect(x, 374, 240, 120, PALE, node=True)
        d.text(x+16, 418, title, 24, BLUE, True)
        d.text(x+16, 460, body, 21)
        if x < 912:
            d.connect(node, Box(x+288, 374, 240, 120))
    d.band(550, 'Все настройки эксперимента — в рецепте',
           'FP8 KV, prefix cache, chunked prefill, CUDA graphs, CPU KV, assistant, TP2 и MTP.', MINT, TEAL)
    d.footer('Проверяем цепочку: рецепт → план → выделенные устройства → параметры движка → ответ API.')
    d.save()


def tp2():
    d = Diagram('10-tp2', 'TP2: две карты, один экземпляр модели',
                'Два процесса совместно вычисляют один ответ и обмениваются промежуточными результатами.')
    d.rect(48, 170, 1104, 350, '#ffffff', BLUE)
    d.text(72, 212, 'ОДНА НОДА / ОДИН POD / ОДИН API', 20, BLUE, True)
    first = d.card(72, 254, 384, 208, 'H100 / rank 0', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    second = d.card(744, 254, 384, 208, 'H100 / rank 1', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    d.text(600, 315, 'NVLink / NCCL', 24, BLUE, True, 'middle')
    d.connector([first.port('right', .5), second.port('left', .5)])
    d.connector([second.port('left', .75), first.port('right', .75)])
    d.text(600, 491, 'DRA выделяет два устройства; vLLM распределяет модель.', 23, anchor='middle')
    d.band(565, 'TP2 не равно двум репликам',
           'Обе GPU участвуют в одном запросе. HBM не становится прозрачным общим пулом.', MINT, TEAL)
    d.footer('PP делит слои по стадиям; TP делит тензоры внутри слоёв. Режим выбирают под архитектуру и топологию.')
    d.save()


def gemma_formula():
    d = Diagram('11-gemma-kv', 'KV-кэш Gemma: считаем память',
                '10 слоёв полного внимания + 50 локальных слоёв с окном 1024 токена.')
    d.rect(48, 160, 1104, 120, PALE)
    d.text(600, 237, 'KV(S) = 2 × b × [ G(S) + L(S) ]', 46, BLUE, True, 'middle')
    for x, title, lines, color, fill in [
            (48, 'G(S) / полное внимание', ['10 × 4 × 512 × S'], BLUE, PALE),
            (620, 'L(S) / локальное внимание', ['50 × 16 × 256', '× min(S, 1024)'], TEAL, MINT)]:
        d.card(x, 314, 532, 204, title, (), fill, color)
        d.text(x+266, 417, lines, 34, bold=True, anchor='middle')
    for x, title, body in [(48, '2', ['Ключи K', 'и значения V']),
            (424, 'b', ['Байт на элемент', 'BF16: 2 / FP8: 1']),
            (800, 'S', ['Длина истории', 'Вход + ответ'])]:
        d.card(x, 554, 352, 126, title, body, GRAY, BLUE)
    d.footer('Число слоёв × KV-головы × размерность × токены. Результат в байтах; для GiB делим на 2³⁰.')
    d.save()


def gitops():
    d = Diagram('12-gitops', 'Helm в Git, доставка через Argo CD',
                'Один чарт, отдельные профили и привязки площадки. Без прямого изменения Deployment.')
    github = d.card(48, 160, 330, 158, 'GitHub', ['Чарт и примеры', 'Профили экспериментов', 'Без секретов'], GRAY, MUTED)
    gitlab = d.card(446, 160, 706, 158, 'GitLab / k8s-config',
           ['Чарт + values + site-values', 'Проверка diff → подписанный commit → push', 'Application указывает на этот репозиторий'])
    argo = d.card(48, 411, 504, 163, 'Управляющий кластер', ['Argo CD', 'Helm render → sync выбранного SHA'])
    gpu = d.card(648, 411, 504, 163, 'GPU-кластер',
           ['ConfigMap, Deployment, Service', 'ResourceClaimTemplate, NetworkPolicy'], MINT, TEAL)
    d.connect(github, gitlab)
    d.connect(gitlab, argo, ('bottom', 'top'), via=((799, 365), (300, 365)))
    d.connect(argo, gpu, color=TEAL)
    d.text(48, 645, 'Изменение values → новый checksum Pod → перезапуск Recreate', 27, bold=True)
    d.footer('Примеры выключены: replicaCount = 0, autosync отсутствует. Секреты передаются отдельно от Git.')
    d.save()


def attention():
    d = Diagram('13-attention', 'Зачем сохранять K и V',
                'Запрос текущего токена обращается к ключам и значениям истории.')
    query = d.card(48, 170, 336, 134, 'Текущий токен', ['Q — запрос'])
    history = d.card(432, 170, 336, 134, 'История', ['K — ключи, V — значения'], MINT, TEAL)
    cache = d.card(816, 170, 336, 134, 'KV-кэш', ['Сохраняет K и V'], MINT, TEAL)
    d.connect(history, cache, color=TEAL)
    d.rect(48, 364, 1104, 232, PALE, node=True)
    d.text(600, 424, 'Attention(Q, K, V) =', 34, BLUE, True, 'middle')
    d.text(600, 502, 'softmax(QKᵀ / √d) V', 52, bold=True, anchor='middle')
    d.text(600, 553, 'Сравнить Q с ключами → получить веса → смешать значения', 25, anchor='middle')
    d.connector([query.port('bottom'), (216, 358)])
    d.connector([history.port('bottom'), (600, 358)], TEAL)
    d.text(48, 653, 'Кэш хранит тензоры, а не готовые ответы.', 29, bold=True)
    d.footer('d — размерность головы. Маски опущены; при GQA память KV считают по KV-головам, не по Q-головам.')
    d.save()


def prefixes():
    d = Diagram('14-prefix', 'Общий смысл не равен общему префиксу',
                'Повторно используются KV-блоки совпадающей последовательности токенов с начала запроса.')
    for y, name, changed, question in [(182, 'Запрос 1', False, 'Вопрос 1'),
            (318, 'Запрос 2', False, 'Вопрос 2'), (454, 'Запрос 3', True, 'Вопрос 3')]:
        d.text(48, y+47, name, 25, bold=True)
        for x, w, label, fill, color in [
                (230, 294, 'Другая дата' if changed else 'Общий system', SAND if changed else MINT, AMBER if changed else TEAL),
                (546, 324, 'Документ X', GRAY if changed else MINT, MUTED if changed else TEAL),
                (892, 260, question, PALE, BLUE)]:
            d.rect(x, y, w, 80, fill)
            d.text(x+w/2, y+49, label, 25, color, True, 'middle')
    d.path('M230 282 V293 H870 V282', TEAL)
    d.band(579, 'Изменение начала разрывает совпадение префикса',
           'Даже если сам документ в третьем запросе не изменился.', SAND, AMBER)
    d.footer('На токены влияют шаблон чата, порядок сообщений, даты и пробелы.')
    d.save()


def rag():
    d = Diagram('16-rag', 'Поиск и генерация — разные сервисы',
                'Индексирование выполняется при загрузке документов; поиск — при вопросе пользователя.')
    for x, title, body in [(48, 'Документы', 'Разбивка на фрагменты'),
            (432, 'Эмбеддер', 'Фрагменты → векторы'), (816, 'Индекс', 'Векторы и источники')]:
        node = d.card(x, 170, 336, 118, title, [body], MINT, TEAL)
        if x < 816:
            d.connect(node, Box(x+384, 170, 336, 118), color=TEAL)
    for x, title, body in [(48, 'Вопрос', 'Эмбеддер вопроса'), (336, 'Поиск', 'Кандидаты'),
            (624, 'Реранкер', 'Отбор фрагментов'), (912, 'LLM', 'Вопрос + контекст')]:
        node = d.rect(x, 394, 240, 126, PALE, node=True)
        d.text(x+20, 438, title, 25, BLUE, True)
        d.text(x+20, 484, body, 21)
        if x < 912:
            d.connect(node, Box(x+288, 394, 240, 126))
    d.connect(Box(816, 170, 336, 118), Box(336, 394, 240, 126), ('bottom', 'top'),
              via=((984, 336), (456, 336)), color=TEAL)
    d.text(694, 363, 'Поиск похожих векторов', 21, TEAL, anchor='middle')
    d.band(566, 'LLM можно заменить без пересоздания индекса',
           'При смене эмбеддера документы нужно переиндексировать.', MINT, TEAL)
    d.footer('Проверяем не только ответ: нужный документ должен попасть в контекст, а ссылка — вести к источнику.')
    d.save()


def qwen_transition():
    d = Diagram('17-qwen-transition', 'От двух Gemma к одному Qwen',
                'WebUI, Bifrost и базы знаний остаются; обе H100 переходят одному сервису.')
    d.text(48, 178, '1 / ОСВОБОДИТЬ GPU', 19, MUTED, True)
    d.card(48, 204, 532, 114, 'Gemma A', ['replicaCount: 0 → commit → sync'], GRAY, MUTED)
    d.card(620, 204, 532, 114, 'Gemma B', ['replicaCount: 0 → commit → sync'], GRAY, MUTED)
    d.text(48, 355, 'Платформенную Gemma, если запущена, тоже остановить.', 22, MUTED)
    d.text(48, 419, '2 / СОЗДАТЬ СЕРВИС ЧЕРЕЗ AI INFERENCE', 19, BLUE, True)
    for x, title, body in [(48, 'Рецепт Qwen', ['Throughput', 'TP2 + MTP + CPU KV']),
            (432, 'DRA-заявка', ['count: 2', 'Полные H100 одной ноды']),
            (816, 'Qwen API', ['Один Pod', 'Ответ через прежний чат'])]:
        node = d.card(x, 447, 336, 143, title, body)
        if x < 816:
            d.connect(node, Box(x+384, 447, 336, 143))
    d.text(48, 656, 'Перед запуском: прежние Pod завершены, обе GPU свободны.', 27, bold=True)
    d.footer('PVC, веса, GPUClass/GPUPool и поисковые сервисы на A30 при переключении не удаляются.')
    d.save()


def qwen_mtp():
    d = Diagram('18-qwen-mtp', 'Qwen MTP: предложить и проверить',
                'Черновой блок входит в checkpoint. Условный пример с четырьмя предложениями.')
    for y, title in [(183, 'MTP'), (308, 'Проверка')]:
        d.text(48, y+43, title, 25, PURPLE if y == 183 else BLUE, True)
        for i in range(4):
            x = 352+i*202
            fill, color = (LILAC, PURPLE) if y == 183 else ((MINT, TEAL) if i < 2 else (SAND, AMBER))
            label = ['t₁', 't₂', 't₃', 't₄'][i] if y == 183 else ['Принят', 'Принят', 'Отказ', 'Отбросить'][i]
            d.rect(x, y, 174, 70, fill, node=True)
            d.text(x+87, y+45, label, 25, color, True, 'middle')
            if y == 183:
                d.connect(Box(x, y, 174, 70), Box(x, 308, 174, 70), ('bottom', 'top'), color=MUTED)
    d.band(423, 'В ответ: t₁ + t₂ + исправление основной модели',
           'Первый отказ отменяет остаток; следующий цикл продолжает принятый текст.')
    for x, w, title, top, bottom, color, fill in [
            (48, 432, 'Доля принятия', 'принятые токены', 'предложенные токены', TEAL, MINT),
            (520, 632, 'Время на выданный токен', 'предложение + проверка + обмен', 'выданные токены', PURPLE, LILAC)]:
        d.rect(x, 549, w, 132, fill)
        d.text(x+24, 584, title, 24, color, True)
        d.text(x+w/2, 620, top, 23, anchor='middle')
        d.path(f'M{x+40} 631 H{x+w-40}', color, width=1.5)
        d.text(x+w/2, 662, bottom, 23, anchor='middle')
    d.footer('В профиле стартуем с num_speculative_tokens=1. Сравниваем MTP on/off при одинаковой нагрузке.')
    d.save()


def qwen_capacity():
    d = Diagram('19-qwen-capacity', 'Сколько запросов выдерживает Qwen',
                'Одинаковые вход, выход, TP2, MTP и состояние кэша на каждой ступени нагрузки.')
    d.text(48, 180, 'ОДНОВРЕМЕННЫЕ ЗАПРОСЫ', 19, MUTED, True)
    for i, count in enumerate([1, 2, 4, 8, 16, 32, 50]):
        x = 48+i*162
        node = d.rect(x, 207, 124, 78, PALE, node=True)
        d.text(x+62, 260, str(count), 39, BLUE, True, 'middle')
        if i < 6:
            d.connect(node, Box(x+162, 207, 124, 78))
    for x, title, body, fill, color in [
            (48, 'Очередь', ['Стабильна', 'или растёт?'], SAND, AMBER),
            (330, 'TTFT', ['До первого токена', 'p50 / p95 / p99'], PALE, BLUE),
            (612, 'TPOT', ['Время на токен', 'p50 / p95 / p99'], LILAC, PURPLE),
            (894, 'Ошибки', ['HTTP, timeout, OOM', 'Считаем все отказы'], SAND, AMBER)]:
        d.card(x, 331, 258, 140, title, body, fill, color)
    d.card(48, 515, 532, 123, 'Пороги соблюдены', ['Следующая ступень нагрузки'], MINT, TEAL)
    d.card(620, 515, 532, 123, 'Хотя бы один порог нарушен', ['Остановить рост нагрузки'], SAND, AMBER)
    d.footer('256K — окно одной истории. 50 пользователей не означают 50 одновременно заполненных окон.')
    d.save()


BUILDERS = (topology, latency, memory, ab, offload, scheduler, speculation, mig,
            platform, tp2, gemma_formula, gitops, attention, prefixes, rag,
            qwen_transition, qwen_mtp, qwen_capacity)

if __name__ == '__main__':
    for build in BUILDERS:
        build()
    print(f'Built {len(BUILDERS)} self-contained SVGs, all 1200 × 760.')
