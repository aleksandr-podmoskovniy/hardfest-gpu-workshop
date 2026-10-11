"""Render client timelines only after strict final-run validation; no live I/O."""
import argparse
import hashlib
import html
import json
from pathlib import Path

from gemma_capacity_summary import summarize


def number(value, decimals=0):
    return f'{value:,.{decimals}f}'.replace(',', ' ').replace('.', ',')


def duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f'{minutes} мин {seconds:02d} с'


def render(raw, monitor, variant, source_hashes):
    summary = summarize(raw, monitor, variant)
    label = 'A · без тюнинга' if variant == 'a' else 'B · с тюнингом'
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1100" viewBox="0 0 1400 1100" role="img" aria-labelledby="title desc">',
        f'<title id="title">Gemma {label}: предел ёмкости, 10 запросов</title>',
        '<desc id="desc">Проверка полных контекстных окон. Ожидание первого токена и продолжительность генерации показаны отдельно для каждого запроса. Это не тест обычного чата.</desc>',
        '<rect width="1400" height="1100" fill="#f5f7fb"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#172b4d}.body{font-size:18px}.small{font-size:16px}.title{font-size:28px;font-weight:700}.number{font-size:30px;font-weight:700}</style>',
    ]

    def text(x, y, value, cls='body', anchor='start'):
        svg.append(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{html.escape(str(value))}</text>')

    text(40, 47, f'Gemma {label}: 10 / 10 запросов завершены', 'title')
    text(40, 79, '1 × RTX PRO 6000 на модель · Все 10 запросов поданы одновременно, не все выполнялись сразу.')
    text(40, 109, f'Каждый запрос: {number(summary["input_tokens_each"])} входных + 32 768 выходных = {number(summary["window_tokens"])} токенов.')
    cards = [
        (duration(summary['wall_seconds']), 'весь прогон'),
        (duration(summary['client_ttft_max_seconds']), 'максимум до первого токена'),
        (number(summary['engine_mean_tpot_seconds'] * 1000, 1) + ' мс', 'средний TPOT движка'),
        (number(summary['sampled_max_running']) + ' из 10', 'пик активных, по метрикам'),
    ]
    for i, (value, caption) in enumerate(cards):
        x = 40 + i * 335
        svg.append(f'<rect x="{x}" y="134" width="315" height="96" rx="10" fill="white" stroke="#d8dfeb"/>')
        text(x + 15, 173, value, 'number')
        text(x + 15, 208, caption, 'small')
    text(40, 278, 'Когда каждый запрос дождался ответа', 'title')
    text(40, 312, 'TTFT: от отправки до первого токена, включая очередь и prefill. Это не только время очереди.', 'small')
    svg.append('<rect x="40" y="336" width="20" height="16" fill="#2563eb"/>')
    text(70, 350, 'До первого токена', 'small')
    svg.append('<rect x="350" y="336" width="20" height="16" fill="#d97706"/>')
    text(380, 350, 'После первого токена, до завершения', 'small')
    start = min(r['submitted_at'] for r in summary['requests'])
    end = max(r['completed_at'] for r in summary['requests'])
    width, left, top, bottom = 1110, 155, 385, 810
    scale = width / (end - start)
    for tick in range(6):
        delta = (end - start) * tick / 5
        x = left + delta * scale
        svg.append(f'<path d="M{x:.2f},{top}V{bottom}" stroke="#d8dfeb"/>')
        text(x, bottom + 27, number(delta / 60, 1), 'small', 'middle')
    text(left + width, bottom + 53, 'минут от одновременной подачи', 'small', 'end')
    for row in summary['requests']:
        y = top + row['session'] * 41
        x = left + (row['submitted_at'] - start) * scale
        first = row['ttft_s'] * scale
        rest = (row['elapsed_s'] - row['ttft_s']) * scale
        text(left - 18, y + 24, f'Запрос {row["session"] + 1}', 'small', 'end')
        svg.append(f'<rect data-phase="first-token" x="{x:.3f}" y="{y}" width="{first:.3f}" height="31" fill="#2563eb"/>')
        svg.append(f'<rect data-phase="continuation" x="{x + first:.3f}" y="{y}" width="{rest:.3f}" height="31" fill="#d97706"/>')
    text(40, 906, 'Что этот тест доказывает — и чего не доказывает', 'title')
    text(40, 941, 'Полные окна и длинный выход выдержаны. Приемлемую задержку обычного чата этот тест не подтверждает.')
    text(40, 972, 'Синтетика: ignore_eos=true, прямой vLLM API. Персональный путь WebUI → Bifrost проверяется отдельно.', 'small')
    text(40, 1000, 'A и B получили разный объём входа: по этим двум прогонам нельзя вычислять выигрыш от тюнинга.', 'small')
    text(40, 1028, 'TPOT — среднее время выходного токена после первого; одинаковые 32 768 токенов в каждом запросе.', 'small')
    text(40, 1064, f'Источник: клиентские отчёты + Prometheus · SHA256 client {source_hashes["client"][:16]} · metrics {source_hashes["monitor"][:16]}', 'small')
    svg.append('</svg>')
    return '\n'.join(svg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', choices=('a', 'b'), required=True)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--monitor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    client, monitor = args.client.read_bytes(), args.monitor.read_bytes()
    result = render(json.loads(client), json.loads(monitor), args.variant, {
        'client': hashlib.sha256(client).hexdigest(), 'monitor': hashlib.sha256(monitor).hexdigest()})
    with args.output.open('x') as output:
        output.write(result)
    print('Rendered validated final evidence:', args.output)


if __name__ == '__main__':
    main()
