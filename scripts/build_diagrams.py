#!/usr/bin/env python3
"""Build self-contained workshop SVGs using a shared layout and colour system."""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, LINE = "#17243b", "#536278", "#d9e2ee"
BLUE, PALE = "#1554dc", "#eef4ff"
TEAL, MINT = "#087d78", "#eaf7f3"
PURPLE, LILAC = "#7041b8", "#f3eefb"
AMBER, SAND = "#946017", "#fff5e4"
GRAY = "#f5f7fa"


class Diagram:
    def __init__(self, name, title, subtitle):
        self.name = name
        self.parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" '
            'viewBox="0 0 1200 760" role="img" aria-labelledby="title desc" data-design="hardfest-v2">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10Z" '
            'fill="context-stroke"/></marker></defs>',
            '<style>text{font-family:Arial,Helvetica,sans-serif;font-variant-numeric:tabular-nums}</style>',
        ]
        self.rect(1, 1, 1198, 758, "#ffffff", LINE, 20)
        self.rect(48, 38, 5, 34, BLUE, BLUE, 2)
        self.text(70, 64, title, 32, bold=True, width=1082)
        self.text(48, 104, subtitle, 20, MUTED, width=1104)
        self.path("M48 128 H1152", LINE, width=1)

    def rect(self, x, y, w, h, fill=GRAY, stroke=LINE, radius=12, dash=False):
        dash = ' stroke-dasharray="7 5"' if dash else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>')

    def text(self, x, y, lines, size=24, color=INK, bold=False, anchor="start", width=None):
        lines = [lines] if isinstance(lines, str) else lines
        weight = ' font-weight="700"' if bold else ''
        bound = f' data-max-width="{width}"' if width is not None else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                          f'text-anchor="{anchor}"{weight}{bound}>')
        for i, line in enumerate(lines):
            self.parts.append(f'<tspan x="{x}" dy="{0 if i == 0 else size * 1.4}">{escape(line)}</tspan>')
        self.parts.append('</text>')

    def path(self, path, color=BLUE, arrow=False, dash=False, width=2.5):
        end = ' marker-end="url(#arrow)"' if arrow else ''
        dashed = ' stroke-dasharray="6 5"' if dash else ''
        self.parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}" '
                          f'stroke-linejoin="round" stroke-linecap="round"{end}{dashed}/>')

    def card(self, x, y, w, h, title, body=(), fill=PALE, color=BLUE):
        self.rect(x, y, w, h, fill)
        self.text(x+24, y+42, title, 25, color, True, width=w-48)
        if body:
            self.text(x+24, y+82, body, 22, width=w-48)

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
    d.rect(48, 160, 310, 486)
    d.rect(410, 160, 742, 486, '#ffffff')
    d.text(72, 194, 'КЛАСТЕР WEBUI', 18, MUTED, True)
    d.text(434, 194, 'GPU-КЛАСТЕР', 18, MUTED, True)
    d.card(68, 220, 270, 138, 'Open WebUI', ['Чат и голос', 'Пароль / OIDC'])
    d.card(68, 422, 270, 138, 'Базы знаний', ['Документы', 'Индекс и источники'], MINT, TEAL)
    d.path('M203 360 V420', TEAL, True)
    d.card(434, 220, 694, 112, 'HA Bifrost', ['Маршруты, личные ключи, лимиты и учёт'])
    d.path('M340 278 H432', BLUE, True)
    d.card(434, 387, 334, 112, 'Gemma A — Base', ['H100 №1'])
    d.card(794, 387, 334, 112, 'Gemma B — Tune', ['H100 №2'])
    d.path('M780 334 V358 H601 V385', BLUE, True)
    d.path('M780 358 H961 V385', BLUE, True)
    d.card(434, 526, 334, 96, 'A30 / MIG + MPS', ['Эмбеддеры и реранкер'], MINT, TEAL)
    d.card(794, 526, 334, 96, 'Kubernetes MCP', ['Доступ администратора'], LILAC, PURPLE)
    d.path('M446 334 H422 V574 H432', TEAL, True)
    d.path('M1116 334 H1140 V574 H1130', PURPLE, True, True)
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
    d.text(600, 208, 'Контекст 65 536     Вход 32 768     Выход 2 048', 29, bold=True, anchor='middle')
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
    for y, title, gpu, ram in [(216, '1. Обработать X', 'KV документа X', 'Копия KV документа X'),
            (360, '2. Вытеснить X', 'KV документов Y, Z', 'Копия X остаётся'),
            (504, '3. Повторить X', 'KV X снова на GPU', 'Найденные блоки X')]:
        d.text(48, y-14, title, 19, MUTED)
        d.card(48, y, 432, 96, gpu)
        d.card(720, y, 432, 96, ram, fill=MINT, color=TEAL)
    d.path('M484 264 H716', TEAL, True)
    d.text(600, 245, 'Запись', 22, TEAL, anchor='middle')
    d.text(600, 415, 'Другие запросы', 21, MUTED, anchor='middle')
    d.path('M716 552 H484', BLUE, True)
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
            d.rect(x, y, 150, 66, fill)
            d.text(x+75, y+43, token if y == 215 or i < 3 else '×', 30, color, True, 'middle')
            if y == 215:
                d.path(f'M{x+75} 283 V336', MUTED, True)
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
    for x, title, body in [(48, 'GPUClass / GPUPool', 'Выбор GPU'), (334, 'DeviceClass', 'Профили'),
            (620, 'ResourceClaim', 'Запрос ресурсов'), (906, 'DRA-драйвер', 'Создание раздела')]:
        d.rect(x, 160, 246, 110)
        d.text(x+16, 203, title, 21, BLUE, True)
        d.text(x+16, 242, body, 21)
        if x < 906:
            d.path(f'M{x+250} 216 H{x+282}', arrow=True)
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
    d.card(48, 160, 322, 142, 'AI Models', ['Модель и ревизия', 'Проверенные файлы'])
    d.card(418, 160, 734, 142, 'Рецепт + оборудование + стратегия',
           ['Runtime, параметры движка и бюджет памяти', 'Latency / Throughput'], MINT, TEAL)
    d.path('M210 304 V338 H171 V370', arrow=True)
    d.path('M786 304 V338 H210', TEAL)
    for x, title, body in [(48, 'InferenceService', 'Заказ сервиса'), (334, 'План', 'Параметры и GPU'),
            (620, 'Claim + Pod', 'DRA и движок'), (906, 'API', 'Ответ модели')]:
        d.rect(x, 374, 246, 120, PALE)
        d.text(x+16, 418, title, 24, BLUE, True)
        d.text(x+16, 460, body, 21)
        if x < 906:
            d.path(f'M{x+250} 433 H{x+282}', arrow=True)
    d.band(550, 'Все настройки эксперимента — в рецепте',
           'FP8 KV, prefix cache, chunked prefill, CUDA graphs, CPU KV, assistant, TP2 и MTP.', MINT, TEAL)
    d.footer('Проверяем цепочку: рецепт → план → выделенные устройства → параметры движка → ответ API.')
    d.save()


def tp2():
    d = Diagram('10-tp2', 'TP2: две карты, один экземпляр модели',
                'Два процесса совместно вычисляют один ответ и обмениваются промежуточными результатами.')
    d.rect(48, 170, 1104, 350, '#ffffff', BLUE)
    d.text(72, 212, 'ОДНА НОДА / ОДИН POD / ОДИН API', 20, BLUE, True)
    d.card(72, 254, 384, 208, 'H100 / rank 0', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    d.card(744, 254, 384, 208, 'H100 / rank 1', ['Часть весов', 'Локальные состояния', 'Вычисления'])
    d.text(600, 315, 'NVLink / NCCL', 24, BLUE, True, 'middle')
    d.path('M460 353 H740', BLUE, True)
    d.path('M740 405 H460', BLUE, True)
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
    d.card(48, 160, 330, 158, 'GitHub', ['Чарт и примеры', 'Профили экспериментов', 'Без секретов'], GRAY, MUTED)
    d.card(446, 160, 706, 158, 'GitLab / k8s-config',
           ['Чарт + values + site-values', 'Проверка diff → подписанный commit → push', 'Application указывает на этот репозиторий'])
    d.path('M382 236 H442', BLUE, True)
    d.card(48, 411, 504, 163, 'Управляющий кластер', ['Argo CD', 'Helm render → sync выбранного SHA'])
    d.card(648, 411, 504, 163, 'GPU-кластер',
           ['ConfigMap, Deployment, Service', 'ResourceClaimTemplate, NetworkPolicy'], MINT, TEAL)
    d.path('M798 320 V365 H300 V407', BLUE, True)
    d.path('M556 490 H644', TEAL, True)
    d.text(48, 645, 'Изменение values → новый checksum Pod → перезапуск Recreate', 27, bold=True)
    d.footer('Примеры выключены: replicaCount = 0, autosync отсутствует. Секреты передаются отдельно от Git.')
    d.save()


def attention():
    d = Diagram('13-attention', 'Зачем сохранять K и V',
                'Запрос текущего токена обращается к ключам и значениям истории.')
    d.card(48, 170, 330, 134, 'Текущий токен', ['Q — запрос'])
    d.card(426, 170, 330, 134, 'История', ['K — ключи, V — значения'], MINT, TEAL)
    d.card(804, 170, 348, 134, 'KV-кэш', ['Сохраняет K и V'], MINT, TEAL)
    d.path('M760 237 H800', TEAL, True)
    d.rect(48, 364, 1104, 232, PALE)
    d.text(600, 424, 'Attention(Q, K, V) =', 34, BLUE, True, 'middle')
    d.text(600, 502, 'softmax(QKᵀ / √d) V', 52, bold=True, anchor='middle')
    d.text(600, 553, 'Сравнить Q с ключами → получить веса → смешать значения', 25, anchor='middle')
    d.path('M213 306 V360', BLUE, True)
    d.path('M591 306 V360', TEAL, True)
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
            (424, 'Эмбеддер', 'Фрагменты → векторы'), (800, 'Индекс', 'Векторы и источники')]:
        d.card(x, 170, 352, 118, title, [body], MINT, TEAL)
        if x < 800:
            d.path(f'M{x+356} 232 H{x+372}', TEAL, True)
    for x, title, body in [(48, 'Вопрос', 'Эмбеддер вопроса'), (334, 'Поиск', 'Кандидаты'),
            (620, 'Реранкер', 'Отбор фрагментов'), (906, 'LLM', 'Вопрос + контекст')]:
        d.rect(x, 394, 246, 126, PALE)
        d.text(x+20, 438, title, 25, BLUE, True)
        d.text(x+20, 484, body, 21)
        if x < 906:
            d.path(f'M{x+250} 456 H{x+282}', BLUE, True)
    d.path('M976 290 V336 H457 V390', TEAL, True)
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
            (424, 'DRA-заявка', ['count: 2', 'Полные H100 одной ноды']),
            (800, 'Qwen API', ['Один Pod', 'Ответ через прежний чат'])]:
        d.card(x, 447, 352, 143, title, body)
        if x < 800:
            d.path(f'M{x+356} 519 H{x+372}', BLUE, True)
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
            d.rect(x, y, 174, 70, fill)
            d.text(x+87, y+45, label, 25, color, True, 'middle')
            if y == 183:
                d.path(f'M{x+87} 255 V304', MUTED, True)
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
        d.rect(x, 207, 132, 78, PALE)
        d.text(x+66, 260, str(count), 39, BLUE, True, 'middle')
        if i < 6:
            d.path(f'M{x+136} 246 H{x+158}', BLUE, True)
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
