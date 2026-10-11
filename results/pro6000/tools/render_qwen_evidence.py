"""Render measured data, never illustrations of invented monitoring values."""
import argparse
import datetime
import html
import json
import math
from pathlib import Path
from qwen_evidence_summary import summarize, source_hash

parser = argparse.ArgumentParser()
for name in ('client', 'monitor', 'output'):
    parser.add_argument('--' + name, type=Path, required=True)
args = parser.parse_args()
monitor = json.loads(args.monitor.read_text())
raw = json.loads(args.client.read_text())
summary = summarize(raw, monitor)
run = raw['passes'][0]
series = monitor['series']
start = min(r['submitted_at'] for r in run['results'])
end = max(r['completed_at'] for r in run['results'])
plot_start = monitor['start_unix']
plot_end = monitor['end_unix']


def number(value, decimals=1):
    return f'{value:,.{decimals}f}'.replace(',', ' ').replace('.', ',')

def duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f'{minutes} мин {seconds:02d} с'

svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1100" viewBox="0 0 1400 1100" role="img" aria-labelledby="title desc">',
       '<title id="title">Qwen: 10 запросов с окном 262144 токена</title>',
       '<desc id="desc">Реальные Prometheus и клиентские измерения 11 октября 2026. Все запросы завершены, первый токен до 20 минут.</desc>',
       '<rect width="1400" height="1100" fill="#f5f7fb"/>',
       '<style>text{font-family:Arial,sans-serif;fill:#172b4d}.small{font-size:16px}.label{font-size:18px}.title{font-size:26px;font-weight:700}.number{font-size:30px;font-weight:700}</style>']

def text(x, y, value, cls='label', anchor=None):
    svg.append(f'<text x="{x}" y="{y}" class="{cls}"' + (f' text-anchor="{anchor}"' if anchor else '') + f'>{html.escape(str(value))}</text>')

text(40, 48, f'Qwen: полное окно выдержано. Первый токен — до {duration(summary["client_ttft_max_seconds"])}.', 'title')
text(40, 80, '2 × RTX PRO 6000 · TP2 · 160 GiB RAM-offload · 10 одновременно поданных запросов')
text(40, 108, 'Каждый запрос: 229 376 входных + 32 768 выходных токенов. Синтетический тест, ignore_eos=true.', 'small')
cards = [(f'{summary["completed"]} / {summary["submitted_concurrently"]}', 'успешно завершены'),
         (duration(summary['wall_seconds']), 'весь прогон'),
         (number(summary['engine_mean_tpot_seconds'] * 1000) + ' мс', 'средний TPOT движка'),
         (duration(summary['client_ttft_max_seconds']), 'максимум до первого токена')]
for i, (display_value, caption) in enumerate(cards):
    x = 40 + i * 335
    svg.append(f'<rect x="{x}" y="130" width="315" height="95" rx="10" fill="white" stroke="#d8dfeb"/>')
    text(x + 18, 169, display_value, 'number'); text(x + 18, 200, caption, 'small')

palette = ['#2563eb', '#d97706', '#059669']

def chart(x, y, title, datasets, unit, ymax=None):
    w, h = 640, 320
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="white" stroke="#d8dfeb"/>')
    text(x + 18, y + 31, title, 'label')
    left, top, pw, ph = x + 60, y + 65, 555, 175
    values = [v for _, vals in datasets for t, v in vals if v is not None and plot_start <= t <= plot_end]
    ymax = ymax or math.ceil(max(values, default=1) / 100) * 100 or 1
    steps = 5 if ymax == 10 else 4
    for i in range(steps + 1):
        yy = top + ph - ph * i / steps
        svg.append(f'<path d="M{left},{yy}h{pw}" stroke="#e2e8f0"/>')
        text(left - 10, yy + 5, f'{ymax*i/steps:.0f}', 'small', 'end')
    for minutes in (0, 5, 10, 15, 20, 25):
        xx = left + minutes * 60 / (plot_end - plot_start) * pw
        text(xx, top + ph + 24, minutes, 'small', 'middle')
    text(left, top - 12, unit, 'small'); text(left + pw, top + ph + 45, 'мин от начала графика', 'small', 'end')
    for i, (label, vals) in enumerate(datasets):
        chunks, current = [], []
        for t, v in vals:
            if not plot_start <= t <= plot_end:
                continue
            if v is None:
                if current: chunks.append(current); current = []
            else:
                current.append(f'{left+(t-plot_start)/(plot_end-plot_start)*pw:.2f},{top+ph-v/ymax*ph:.2f}')
        if current: chunks.append(current)
        for points in chunks:
            svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{palette[i]}" stroke-width="2.5"/>')
        lx = x + 22 + i * 295
        svg.append(f'<path d="M{lx},{y+291}h22" stroke="{palette[i]}" stroke-width="3"/>')
        text(lx + 30, y + 296, label, 'small')

def line(name):
    return series[name]['data'][0]['values']

chart(40, 250, 'Не все 10 запросов выполняются одновременно', [('Активные', line('running')), ('В очереди', line('waiting'))], 'запросы', 10)
chart(720, 250, 'Суммарная скорость, среднее за 1 минуту', [('Выходные токены', line('output_rate'))], 'токенов/с')
chart(40, 595, 'GPU utilization — вся нода, только этот тест', [(f'GPU {s["labels"]["gpu"]}', s['values']) for s in series['gpu_utilization']['data']], '%', 100)
chart(720, 595, 'Занятый KV-кеш на GPU', [('KV-кеш', [[t, None if v is None else v*100] for t, v in line('gpu_kv_fraction')])], '%', 100)
text(40, 958, 'Как читать результат', 'title')
text(40, 991, f'{number(summary["aggregate_output_tokens_per_second"])} выходных токена/с суммарно не означают мгновенный ответ каждому участнику.')
text(40, 1020, f'Средняя очередь — {number(summary["engine_mean_queue_seconds"])} с. Средний prefill — {number(summary["engine_mean_prefill_seconds"])} с. Это проверка предела, не режим показа.')
text(40, 1042, f'Синтетика: принято {number(summary["speculative_acceptance_fraction"]*100, 0)}% draft-токенов. Обычные ответы и путь через шлюз проверяются отдельно.', 'small')
date = datetime.datetime.fromtimestamp(start, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
text(40, 1063, f'Источник: Prometheus + 10 клиентских отчётов · Начало {date} · Сырые данные сохранены отдельно.', 'small')
svg.append('</svg>')
args.output.write_text('\n'.join(svg))
print('Rendered validated Qwen evidence:', args.output)
