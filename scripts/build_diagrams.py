#!/usr/bin/env python3
"""Build self-contained workshop SVGs using a shared layout and colour system."""
from html import escape
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
# Blue: request/compute; teal: data/cache/reuse; purple: draft or shared execution.
# Amber: waiting or a stop condition; gray: inactive/baseline/context.
# Labels always carry the meaning too: colour is never the only distinction.
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
    def __init__(self, name, title, subtitle, height=760):
        self.name = name
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" '
            f'viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc" data-design="hardfest-v2">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>',
            '<defs>' + ''.join(
                f'<marker id="arrow-{color[1:]}" viewBox="0 0 10 10" refX="10" refY="5" '
                f'markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto">'
                f'<path d="M0 0 L10 5 L0 10 Z" fill="{color}"/></marker>'
                for color in (BLUE, TEAL, PURPLE, MUTED)) + '</defs>',
            '<style>text{font-family:Arial,Helvetica,sans-serif;font-variant-numeric:tabular-nums}'
            '.formula{font-family:"DejaVu Sans Mono",Menlo,Consolas,monospace}</style>',
        ]
        self.rect(1, 1, 1198, height-2, "#ffffff", LINE, 20)
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

    def text(self, x, y, lines, size=24, color=INK, bold=False, anchor="start", width=None,
             formula=False):
        lines = [lines] if isinstance(lines, str) else lines
        weight = ' font-weight="700"' if bold else ''
        bound = f' data-max-width="{width}"' if width is not None else ''
        style = ' class="formula"' if formula else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                          f'text-anchor="{anchor}"{weight}{bound}{style}>')
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
        self.path(f"M48 {self.height-62} H1152", LINE, width=1)
        self.text(48, self.height-29, note, 20, MUTED, width=1104)

    def save(self):
        (ROOT / "assets" / f"{self.name}.svg").write_text("\n".join(self.parts + ['</svg>']) + '\n')


def topology():
    d = Diagram('01-topology', 'Весь стенд: от чата до GPU',
                'Три разных пути: запрос пользователя, запуск движка и доставка данных.', height=1040)
    d.text(48, 173, '1. ЗАПРОСЫ', 20, MUTED, True)
    webui = d.card(48, 200, 228, 140, 'Open WebUI',
                   ['Чат и база знаний', 'Голосовой ввод'], compact=True)
    adapter = d.card(324, 200, 228, 140, 'Адаптер',
                     ['Пользователь', '→ личный VK'], compact=True)
    gateway = d.card(600, 200, 240, 140, 'Bifrost',
                     ['ai-mcp-gateway', 'Права и маршруты'], compact=True)
    runtime = d.card(888, 200, 264, 140, 'vLLM',
                     ['Gemma A и B', 'или Qwen / 2 GPU'], compact=True)
    d.connect(webui, adapter)
    d.connect(adapter, gateway)
    d.connect(gateway, runtime)

    d.connector([webui.port('bottom'), (162, 396), (720, 396), gateway.port('bottom')], TEAL)
    d.text(304, 381, 'Поиск и голос: служебный VK', 20, TEAL, True, width=470)
    a30 = d.card(888, 408, 264, 130, 'A30 / 2 × 2g.12gb',
                 ['Эмбеддер + реранкер', 'Whisper large-v3'], MINT, TEAL, compact=True)
    d.connector([gateway.port('right', .8), (860, 312), (860, 473), a30.port('left')], TEAL)
    d.text(48, 450, ['База знаний хранится в WebUI.',
                     'Найденный текст дополняет запрос к vLLM;',
                     'Whisper возвращает текст голосового сообщения.'], 20, width=774)
    d.text(48, 573, 'WebUI + A30 — один кластер. Шлюз + 2 H100 или 2 RTX 5060 Ti — другой.',
           20, MUTED, width=1104)

    d.path('M48 598 H1152', LINE, width=1)
    d.text(48, 634, '2. ДВА СПОСОБА ЗАПУСТИТЬ ТОТ ЖЕ ДВИЖОК', 20, MUTED, True)
    git = d.card(48, 687, 228, 100, 'Git + Helm', ['Конфигурация'], compact=True)
    argo = d.card(324, 687, 228, 100, 'Argo CD', ['Применяет из Git'], compact=True)
    manual = d.card(624, 655, 240, 78, 'Deployment', ['Ручные параметры'], compact=True)
    platform = d.card(624, 765, 240, 78, 'AI Inference', ['Подбирает запуск'], LILAC, PURPLE, compact=True)
    engine = d.card(912, 687, 240, 100, 'vLLM', ['Исполняет модель'], compact=True)
    d.connect(git, argo)
    d.connector([argo.port('right', .25), (588, 712), (588, 694), manual.port('left')])
    d.connector([argo.port('right', .75), (588, 762), (588, 804), platform.port('left')], PURPLE)
    d.connector([manual.port('right'), (884, 694), (884, 712), engine.port('left', .25)])
    d.connector([platform.port('right'), (884, 804), (884, 762), engine.port('left', .75)], PURPLE)

    d.text(48, 891, '3. ФАЙЛЫ И ГРАФИКИ — НЕ ЧАСТЬ МАРШРУТА ЧАТА', 20, MUTED, True)
    d.text(48, 932, 'ai-models → NodeCache → веса для vLLM', 22, TEAL, True, width=535)
    d.text(624, 932, 'vLLM / GPU → Prometheus → Console', 22, PURPLE, True, width=528)
    d.text(48, 961, 'Каталог выбирает файлы; кеш доставляет на узел.', 19, width=535)
    d.text(624, 961, 'Графики очереди, памяти и вычислений.', 19, width=528)
    d.footer('Gemma A и B занимают по GPU. Финальный Qwen использует обе карты вместо них.')
    d.save()


def latency():
    d = Diagram('02-latency', 'Из чего складывается время ответа',
                'Очередь, обработка входа и генерация — разные части задержки.')
    d.text(880, 179, 'DECODE — ГЕНЕРАЦИЯ ТОКЕНОВ', 20, PURPLE, True, 'middle')
    for x, w, label, fill, color in [(48, 220, 'Очередь', SAND, AMBER),
            (268, 340, 'Prefill', PALE, BLUE), (608, 240, 'Рассуждение', LILAC, PURPLE),
            (848, 304, 'Текст ответа', MINT, TEAL)]:
        d.rect(x, 220, w, 88, fill, '#ffffff', 0)
        d.text(x+w/2, 274, label, 25, color, True, 'middle')
    for x, label in [(48, 'Отправка'), (608, 'Первый токен'), (848, 'Первое слово ответа')]:
        d.path(f'M{x} 188 V330', MUTED, dash=True, width=1)
        d.text(x, 359, label, 20, MUTED)
    for y, end, label, color in [(434, 608, 'TTFT — до первого токена', BLUE),
            (503, 848, 'До первого текста ответа', PURPLE), (572, 1152, 'Полное время запроса', TEAL)]:
        d.path(f'M48 {y-10} V{y} H{end} V{y-10}', color)
        d.text((48+end)/2, y-18, label, 25, color, True, 'middle')
    d.text(48, 654, 'Длинный вход увеличивает prefill; длинный ответ — decode.', 27, bold=True)
    d.footer('Шкала условная. Рассуждение есть не у всех моделей; сеть и движок тоже добавляют время.')
    d.save()


def scheduler():
    d = Diagram('06-scheduler', 'Chunked prefill: вход по частям',
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
    d.footer('Меньше порция — короче шаг, но больше шагов. Эффект проверяем по TTFT и ITL.')
    d.save()


def speculation():
    d = Diagram('07-speculation', 'Speculative decoding: сначала черновик',
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
    d.footer('Пример условный. Работа черновика тоже входит во время ответа.')
    d.save()


def mig():
    d = Diagram('08-mig-mps', 'MIG + MPS: три сервиса на одной A30',
                'MIG отделяет память и вычисления; MPS запускает несколько процессов внутри одного раздела.')
    d.text(48, 185, 'A30 / 24 GB', 28, bold=True)
    d.card(48, 218, 536, 322, 'Раздел 1: 2g.12gb', (), LILAC, PURPLE)
    d.card(72, 290, 232, 112, 'Эмбеддер 4B',
           ['Текст → вектор'], '#ffffff', PURPLE, compact=True)
    d.card(328, 290, 232, 112, 'Реранкер 4B',
           ['Отбор фрагментов'], '#ffffff', PURPLE, compact=True)
    d.text(316, 454, 'Работают совместно через MPS', 25, PURPLE, True, 'middle')
    d.text(316, 495, 'Один MIG UUID у обоих', 23, anchor='middle')
    d.card(616, 218, 536, 322, 'Раздел 2: 2g.12gb',
           ['Whisper large-v3', 'Речь → текст'], MINT, TEAL)
    d.text(884, 454, 'Свой раздел памяти и вычислений', 25, TEAL, True, 'middle')
    d.text(884, 495, 'Другой MIG UUID', 23, anchor='middle')
    d.text(48, 595, 'MIG — Multi-Instance GPU: аппаратные разделы', 26, bold=True)
    d.text(48, 646, 'MPS — Multi-Process Service: совместное выполнение процессов', 26, bold=True)
    d.footer('Для новой геометрии нужны поддержка драйвера, включённый MIG mode и свободная карта.')
    d.save()


def platform():
    d = Diagram('09-platform', 'AI Inference: запуск по рецепту',
                'Каталог хранит веса. Рецепт задаёт запуск. Платформа выделяет ресурсы.')
    models = d.card(48, 160, 322, 142, 'ai-models', ['Модель и ревизия', 'Проверенные файлы'])
    recipe = d.card(418, 160, 734, 142, 'Рецепт, стратегия и оборудование',
           ['Движок, оптимизации, бюджет памяти', 'Latency / Throughput'], MINT, TEAL)
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
    d.card(48, 542, 528, 126, 'Gemma: целевой профиль',
           ['128K, FP8 KV, prefix cache', 'RAM offload, chunked prefill, MTP'], MINT, TEAL)
    d.card(624, 542, 528, 126, 'Рецепт Qwen',
           ['Две H100, TP2 и MTP', 'Кэши и параметры движка'], LILAC, PURPLE)
    d.footer('Все настройки эксперимента — в рецепте. Проверяем план, GPU и ответ API.')
    d.save()


def tp2(name='10-tp2', gpu='H100', interconnect='NCCL', memory='HBM'):
    d = Diagram(name, 'TP2: две карты, один экземпляр модели',
                'Два процесса совместно вычисляют один ответ и обмениваются промежуточными результатами.')
    d.rect(48, 170, 1104, 350, '#ffffff', BLUE)
    d.text(72, 212, 'ОДНА НОДА / ОДИН POD / ОДИН API', 20, BLUE, True)
    first = d.card(72, 254, 384, 208, f'{gpu} / rank 0', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    second = d.card(744, 254, 384, 208, f'{gpu} / rank 1', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    d.text(600, 297, interconnect, 26, BLUE, True, 'middle')
    d.text(600, 327, 'Обмен между GPU', 20, MUTED, anchor='middle')
    d.connector([first.port('right', .5), second.port('left', .5)])
    d.connector([second.port('left', .75), first.port('right', .75)])
    d.text(600, 491, 'DRA выделяет два устройства; vLLM распределяет модель.', 23, anchor='middle')
    d.band(565, 'TP2 не равно двум репликам',
           f'Обе GPU участвуют в одном запросе. {memory} не становится прозрачным общим пулом.', MINT, TEAL)
    d.footer('NCCL (NVIDIA Collective Communications Library) — библиотека обмена между GPU.')
    d.save()


def kv_history():
    d = Diagram('03-kv-history', 'KV-кеш: память уже прочитанной истории',
                'KV — Key–Value: ключи и значения токенов, которые нужны для продолжения ответа.')
    past = d.card(48, 174, 320, 146, 'История',
                  ['Документ + вопрос', 'Уже обработанные токены'])
    cache = d.card(440, 174, 320, 146, 'Сохранённые K и V',
                   ['Не вычисляем заново', 'при каждом продолжении'], MINT, TEAL)
    token = d.card(832, 174, 320, 146, 'Следующий токен',
                   ['Использует прошлые K/V', 'Добавляет свои K/V'])
    d.connect(past, cache)
    d.connect(cache, token, color=TEAL)
    d.text(48, 386, 'Что меняется при удлинении истории', 28, bold=True)
    d.text(48, 436, 'Full attention', 25, BLUE, True)
    for x in range(360, 1060, 100):
        d.rect(x, 403, 88, 50, PALE, BLUE, 8)
    d.text(360, 486, 'KV всей истории растёт вместе с ней', 24, width=792)
    d.text(48, 550, 'Sliding attention', 25, TEAL, True)
    for x in range(360, 1060, 100):
        d.rect(x, 517, 88, 50, MINT if x >= 860 else GRAY,
               TEAL if x >= 860 else LINE, 8, dash=x < 860)
    d.text(360, 600, 'Хранится только последнее локальное окно', 24, width=792)
    d.text(48, 660, 'Веса не растут от длины чата. KV — растёт, но по правилам архитектуры.', 25, bold=True)
    d.footer('Это кеш состояния, не готовых ответов. Один прямоугольник условно обозначает группу токенов.')
    d.save()


def kv_reuse():
    d = Diagram('05-kv-reuse', 'Два способа повторно использовать KV',
                'Prefix caching — не пересчитывать начало. RAM offload — сохранить блоки вне GPU.')
    prefix = d.card(48, 182, 440, 176, 'Одинаковое начало двух чатов',
                    ['Те же начальные токены', '→ общие блоки KV'], PALE, BLUE)
    a = d.card(680, 158, 472, 102, 'Вопрос A → своё продолжение', (), MINT, TEAL, compact=True)
    b = d.card(680, 294, 472, 102, 'Вопрос B → своё продолжение', (), MINT, TEAL, compact=True)
    d.connect(prefix, a, ('right', 'left'), via=((570, 270), (570, 209)))
    d.connect(prefix, b, ('right', 'left'), via=((606, 270), (606, 345)))
    d.text(48, 443, 'Если блоки больше не удерживаются в GPU', 27, bold=True)
    gpu = d.card(48, 491, 264, 154, 'GPU', ['Веса модели', 'Вычисления и KV'], PALE, BLUE)
    ram = d.card(888, 491, 264, 154, 'RAM узла', ['Сохранённые', 'блоки KV'], MINT, TEAL)
    d.connector([gpu.port('right', .3), ram.port('left', .3)], TEAL)
    d.text(600, 518, 'Сохранить для повторного запроса', 23, TEAL, anchor='middle', width=548)
    d.connector([ram.port('left', .8), gpu.port('right', .8)])
    d.text(600, 596, 'Вернуть перед вычислением на GPU', 23, BLUE, anchor='middle', width=548)
    d.footer('RAM не увеличивает активный KV-пул GPU. Перенос имеет цену; ускорение проверяется замером.')
    d.save()


def kv_capacity(name, title, groups, maximum, unit, footer):
    d = Diagram(name, title, 'Полезные данные KV основной модели; общий кеш префикса здесь не учитывается.')
    for y, (label, bf16, fp8) in zip((190, 436), groups):
        d.text(48, y, label, 29, bold=True, width=1104)
        for offset, kind, value, color, fill in ((38, 'BF16', bf16, BLUE, PALE),
                                                (102, 'FP8', fp8, TEAL, MINT)):
            d.text(48, y+offset+31, kind, 25, color, True)
            width = 680 * value / maximum
            d.rect(166, y+offset, width, 44, fill, color, 6)
            number = str(value).rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
            d.text(188+width, y+offset+31, number.replace('.', ',') + ' ' + unit,
                   26, color, True, width=278)
    d.text(48, 652, 'FP8: 1 байт на элемент вместо 2 в BF16 → вдвое меньше KV.', 26, bold=True)
    d.footer(footer)
    d.save()


def h100_kv_capacity():
    kv_capacity('25-h100-kv-capacity', 'Gemma 31B: цена восьми длинных историй',
                [('8 независимых историй × 128K токенов', 86.25, 43.125),
                 ('8 независимых историй × 256K токенов', 166.25, 83.125)], 166.25, 'GiB',
                'Расчёт памяти, не обещание вместимости. Веса, assistant и рабочие буферы считаются отдельно.')


def rtx_kv_capacity():
    kv_capacity('26-rtx-kv-capacity', 'Gemma E2B: длинная история — 128K токенов',
                [('1 история × 128K токенов', 774, 387),
                 ('8 независимых историй × 128K токенов', 6192, 3096)], 6192, 'MiB',
                '128K — максимум этой E2B. 8 историй — расчёт; Tune допускает 2 активные последовательности.')


def gemma_formula():
    d = Diagram('11-gemma-kv', 'KV-кэш Gemma: считаем память',
                'Контекст S — 128K или 256K; 1024 — окно только локальных слоёв.')
    d.rect(48, 160, 1104, 120, PALE)
    d.text(600, 237, 'KV(S) = 2 × b × [ G(S) + L(S) ]', 43, BLUE, True, 'middle',
           width=1056, formula=True)
    for x, title, lines, color, fill in [
            (48, 'G(S) / полное внимание', ['10 × 4 × 512 × S'], BLUE, PALE),
            (620, 'L(S) / локальное внимание', ['50 × 16 × 256', '× min(S, 1024)'], TEAL, MINT)]:
        d.card(x, 314, 532, 204, title, (), fill, color)
        d.text(x+266, 417, lines, 32, bold=True, anchor='middle', width=484, formula=True)
    for x, title, body in [(48, '2', ['Ключи K', 'и значения V']),
            (424, 'b', ['Байт на элемент', 'BF16: 2 / FP8: 1']),
            (800, 'S / полный контекст', ['Вход + ответ', '128K или 256K'])]:
        d.card(x, 554, 352, 126, title, body, GRAY, BLUE)
    d.footer('Слои × KV-головы × размерность × токены. Для перевода байтов в GiB делим на 2³⁰.')
    d.save()


def gitops():
    d = Diagram('12-gitops', 'Helm в Git, доставка через Argo CD',
                'Редактируем values, отправляем коммит и применяем его через Argo CD.')
    github = d.card(48, 160, 528, 168, 'GitHub / исходники',
           ['Helm-чарты', 'Профили экспериментов', 'Примеры настройки'], GRAY, MUTED)
    gitlab = d.card(624, 160, 528, 168, 'GitLab / k8s-config',
           ['Чарт + values + настройки площадки', 'Проверка diff → commit → push', 'Источник для Argo CD'])
    argo = d.card(48, 424, 528, 168, 'Argo CD / управляющий кластер',
           ['Читает выбранный коммит', 'Рендерит Helm-чарт', 'Применяет манифесты'])
    gpu = d.card(624, 424, 528, 168, 'GPU-кластер',
           ['Ручной runtime или InferenceService', 'DRA-заявка → Pod', 'Service → ответ модели'], MINT, TEAL)
    d.connect(github, gitlab)
    d.connect(gitlab, argo, ('bottom', 'top'), via=((888, 376), (312, 376)))
    d.connect(argo, gpu, color=TEAL)
    d.text(48, 651, 'Новые параметры vLLM → новый Pod → проверка готовности', 28, bold=True)
    d.footer('Публичные workloads выключены, autosync нет. Секреты хранятся вне Git.')
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
    d.footer('Проверка RAG: нужный документ попал в контекст, ссылка ведёт к источнику.')
    d.save()


def rtx_tp2():
    tp2('24-rtx-tp2', 'RTX 5060 Ti', 'NCCL', 'VRAM')


def rtx_platform():
    d = Diagram('23-rtx-platform', 'Каталог и сервис — одна цепочка',
                'ai-models доставляет веса; AI Inference выбирает запуск; шлюз открывает доступ.')
    model = d.card(48, 175, 320, 136, '1. Model',
                   ['Источник + revision', 'Артефакт + digest'], compact=True)
    delivery = d.card(440, 175, 320, 136, '2. Доставка',
                      ['На нужную RTX-ноду', 'Проверенный mount'], MINT, TEAL, compact=True)
    runtime = d.card(832, 175, 320, 136, '3. Runtime',
                     ['vLLM + локальные веса', 'Ответ API и MTP'], compact=True)
    d.connect(model, delivery)
    d.connect(delivery, runtime)
    order = d.card(48, 394, 320, 136, 'InferenceService',
                   ['model.src: ai-models', 'ref: Model'], compact=True)
    plan = d.card(440, 394, 320, 136, 'Рецепт + DRA',
                  ['Gemma: 1 карта', 'Qwen TP2: 2 карты'], LILAC, PURPLE, compact=True)
    ui = d.card(832, 394, 320, 136, 'ai-mcp-gateway',
                ['Личный VK → WebUI', 'Метрики и учёт'], compact=True)
    d.connect(order, plan)
    d.connector([plan.port('right'), (796, 462), (796, 283.8), runtime.port('left', .8)], PURPLE)
    d.connect(runtime, ui, ('bottom', 'top'))
    d.rect(48, 577, 1104, 100, SAND)
    d.text(72, 610, 'Проверяем три разных результата', 24, AMBER, True, width=1056)
    d.text(72, 646, 'Модель в каталоге → файлы на RTX → ответ сервиса через личный ключ.', 22, width=1056)
    d.footer('Gemma с MTP получает два артефакта: основную модель и совместимый assistant.')
    d.save()


BUILDERS = (topology, latency, kv_history, kv_reuse, scheduler, speculation, mig, platform, tp2,
            gemma_formula, h100_kv_capacity, rtx_kv_capacity, gitops, rag, rtx_platform, rtx_tp2)

if __name__ == '__main__':
    for build in BUILDERS:
        build()
    print(f'Built {len(BUILDERS)} self-contained SVGs, width 1200; topology height 1040, others 760.')
