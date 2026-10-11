"""Plot real fixed-interval metrics after capacity and GPU-allocation checks."""
import argparse
import hashlib
import html
import json
import math
from pathlib import Path

from gemma_capacity_summary import finite, require, summarize


def datasets(raw, monitor, variant, mapping):
    summary = summarize(raw, monitor, variant)
    expected = summary['model']
    interval = mapping['prometheus']
    require(interval['range_start_unix'] <= monitor['start_unix'] and
            interval['range_end_unix'] >= monitor['end_unix'], 'GPU mapping interval incomplete')
    bindings = [b for b in mapping['bindings'] if b['model'] == expected]
    require(len(bindings) == 1, 'Wrong GPU binding')
    binding = bindings[0]

    def line(name, unit, ceiling=None, gpu=False):
        source = monitor['series'][name]
        require(source['unit'] == unit, 'Wrong plotted unit: ' + name)
        candidates = source['data']
        if gpu:
            candidates = [s for s in candidates if
                          s['labels'].get('gpu') == binding['prometheus_gpu'] and
                          s['labels'].get('device') == binding['prometheus_device']]
        require(len(candidates) == 1, 'Wrong plotted series count: ' + name)
        selected = candidates[0]
        if gpu:
            require(selected['labels'].get('__name__') == 'DCGM_FI_DEV_GPU_UTIL', 'Wrong GPU metric')
        else:
            require(selected['labels'].get('model_name') == expected and
                    selected['labels'].get('service') == expected, 'Wrong plotted model')
        values = selected['values']
        require(len(values) > 1 and values[0][0] <= monitor['start_unix'] and
                values[-1][0] >= monitor['end_unix'], 'Plot interval incomplete')
        require(all(finite(t) and (v is None or finite(v) and v >= 0 and
                    (ceiling is None or v <= ceiling)) for t, v in values), 'Invalid plot values')
        require(all(0 < b[0] - a[0] <= 15 for a, b in zip(values, values[1:])), 'Plot sampling gap')
        return values

    return summary, {
        'running': line('running', 'requests', 10),
        'waiting': line('waiting', 'requests', 10),
        'rate': line('output_rate', 'tokens/s'),
        'gpu': line('gpu_utilization', 'percent 0..100', 100, gpu=True),
        'kv': [[t, None if v is None else v * 100] for t, v in
               line('gpu_kv_fraction', 'ratio 0..1', 1)],
    }


def render(raw, monitor, variant, mapping, hashes):
    summary, values = datasets(raw, monitor, variant, mapping)
    stage = variant.upper()
    start, end = monitor['start_unix'], monitor['end_unix']
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1100" viewBox="0 0 1400 1100" role="img" aria-labelledby="title desc">',
           f'<title id="title">Gemma {stage}: мониторинг полного прогона</title>',
           '<desc id="desc">Сохранённые временные ряды Prometheus: активные запросы и очередь, скорость выхода, загрузка выделенной карты и использование GPU KV-кеша. Это предельный синтетический тест, не обещание скорости обычного чата.</desc>',
           '<rect width="1400" height="1100" fill="#f5f7fb"/>',
           '<style>text{font-family:Arial,sans-serif;fill:#172b4d}.body{font-size:18px}.small{font-size:16px}.title{font-size:27px;font-weight:700}.number{font-size:30px;font-weight:700}</style>']

    def text(x, y, value, cls='body', anchor='start'):
        svg.append(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{html.escape(str(value))}</text>')

    def num(v, decimals=0):
        return f'{v:,.{decimals}f}'.replace(',', ' ').replace('.', ',')

    text(40, 46, f'Gemma {stage}: что происходило на GPU и в очереди', 'title')
    text(40, 79, f'1 × RTX PRO 6000 · 10 запросов поданы одновременно · окно {num(summary["window_tokens"])} токенов')
    text(40, 109, f'На запрос: {num(summary["input_tokens_each"])} входных + 32 768 выходных · ignore_eos=true · прямой vLLM', 'small')
    cards = [('10 / 10', 'завершены без ошибки'),
             (num(summary['sampled_max_running']), 'наблюдавшийся максимум active'),
             (num(summary['aggregate_output_tokens_per_second'], 1), 'выходных токенов/с за прогон'),
             (num(summary['engine_mean_tpot_seconds'] * 1000, 1) + ' мс', 'средний TPOT движка')]
    for i, (value, caption) in enumerate(cards):
        x = 40 + 335 * i
        svg.append(f'<rect x="{x}" y="130" width="315" height="95" rx="10" fill="white" stroke="#d8dfeb"/>')
        text(x + 18, 169, value, 'number')
        text(x + 18, 200, caption, 'small')

    def chart(x, y, title, rows, unit, ymax):
        svg.append(f'<rect x="{x}" y="{y}" width="640" height="320" rx="10" fill="white" stroke="#d8dfeb"/>')
        text(x + 18, y + 31, title)
        left, top, width, height = x + 60, y + 65, 555, 175
        for i in range(5):
            yy = top + height - height * i / 4
            svg.append(f'<path d="M{left},{yy}h{width}" stroke="#e2e8f0"/>')
            text(left - 10, yy + 5, num(ymax * i / 4, 1 if ymax == 10 else 0), 'small', 'end')
        for i in range(6):
            text(left + width * i / 5, top + height + 24,
                 num((end - start) * i / 300, 1), 'small', 'middle')
        text(left, top - 12, unit, 'small')
        text(left + width, top + height + 45, 'мин от начала графика', 'small', 'end')
        for i, (label, points) in enumerate(rows):
            color = ('#2563eb', '#d97706')[i]
            chunks, current = [], []
            for t, v in points:
                if v is None:
                    if current:
                        chunks.append(current)
                        current = []
                else:
                    current.append(f'{left + (t-start)/(end-start)*width:.2f},{top+height-v/ymax*height:.2f}')
            if current:
                chunks.append(current)
            for chunk in chunks:
                svg.append(f'<polyline data-metric="{label}" points="{" ".join(chunk)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
            lx = x + 22 + i * 295
            svg.append(f'<path d="M{lx},{y+291}h22" stroke="{color}" stroke-width="3"/>')
            text(lx + 30, y + 296, label, 'small')

    peak_rate = max((v for _, v in values['rate'] if v is not None), default=0)
    chart(40, 250, 'Запросы: сколько работает и сколько ждёт',
          [('Активные', values['running']), ('В очереди', values['waiting'])], 'запросы', 10)
    chart(720, 250, 'Скорость генерации: скользящее окно 1 мин',
          [('Выходные токены', values['rate'])], 'токенов/с', max(5, math.ceil(peak_rate * 1.05 / 5) * 5))
    chart(40, 595, f'GPU utilization: карта, выделенная Gemma {stage}', [('Загрузка карты', values['gpu'])], '%', 100)
    chart(720, 595, 'GPU KV-кеш: доля занятого пула', [('KV-кеш', values['kv'])], '%', 100)
    text(40, 958, 'Что видно на графиках', 'title')
    text(40, 991, f'Приняты 10 запросов. Наблюдавшийся максимум активных — {num(summary["sampled_max_running"])}; остальные ожидали освобождения ресурсов.')
    text(40, 1020, '100% GPU не означает, что каждому хватает скорости. KV-пул — не вся видеопамять и не веса модели.')
    text(40, 1043, 'Это измерение предела. Окна A/B различаются; выигрыш тюнинга проверяется отдельным одинаковым запросом.', 'small')
    text(40, 1065, f'Prometheus, шаг запроса 15 с · SHA256 metrics {hashes["monitor"][:16]} · mapping {hashes["mapping"][:16]}', 'small')
    svg.append('</svg>')
    return '\n'.join(svg)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--variant', choices=('a', 'b'), required=True)
    for name in ('client', 'monitor', 'mapping', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    contents = {name: getattr(args, name).read_bytes() for name in ('client', 'monitor', 'mapping')}
    result = render(*(json.loads(contents[name]) for name in ('client', 'monitor')),
                    args.variant, json.loads(contents['mapping']),
                    {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()})
    with args.output.open('x') as output:
        output.write(result)
    print('Rendered measured monitoring:', args.output)
