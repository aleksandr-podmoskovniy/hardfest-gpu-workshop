"""Render matched client TTFT only after recomputing the validated summary."""
import html
import math

from summarize_matched_chat import summarize


def render(report, summary, hashes):
    checked = {key: value for key, value in summary.items()
               if key not in ("source_sha256", "public_source_sha256")}
    if summarize(report) != checked:
        raise ValueError("Matched summary does not match client responses")
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="650" viewBox="0 0 1400 650" role="img" aria-labelledby="title desc">',
        '<title id="title">Gemma A/B: время до первого видимого ответа при одинаковом входе</title>',
        '<desc id="desc">Три отдельные шкалы. Для короткой и длинной холодной волны шкала линейная; для повторной длинной волны логарифмическая. Точка обозначает медиану, отрезок — минимум и максимум десяти ответов.</desc>',
        '<rect width="1400" height="650" fill="#f5f7fb"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#172b4d}.title{font-size:27px;font-weight:700}.heading{font-size:22px;font-weight:700}.body{font-size:18px}.small{font-size:16px}</style>',
    ]

    def text(x, y, value, cls="body", anchor="start"):
        svg.append(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{html.escape(str(value))}</text>')

    def number(value, precision=3):
        return f"{value:.{precision}f}".replace(".", ",")

    text(32, 45, "Одинаковый вход: когда пользователь увидел первый ответ", "title")
    text(32, 79, "TTFT клиента включает очередь, prefill и возможное рассуждение. По 10 ответов A и B в каждой панели.")
    text(32, 113, "Точка — медиана; отрезок — min–max. Панели имеют разные шкалы: сравнивайте числа, а не длины.", "small")
    panels = (
        ("short", "cold", "Короткая волна", "98 входных токенов", "linear", 0, .5, (0, .1, .2, .3, .4, .5)),
        ("long", "cold", "Длинная, cold", "98 268 входных токенов", "linear", 0, 650, (0, 200, 400, 650)),
        ("long", "warm", "Длинная, повтор", "98 268 входных токенов", "log", .1, 1000, (.1, 1, 10, 100, 1000)),
    )
    for index, (kind, iteration, title, budget, scale, minimum, maximum, ticks) in enumerate(panels):
        x = 32 + index * 448
        svg.append(f'<g data-panel="{kind}-{iteration}" data-scale="{scale}">')
        svg.append(f'<rect x="{x}" y="140" width="440" height="320" rx="10" fill="white" stroke="#d8dfeb"/>')
        text(x + 18, 177, title, "heading")
        text(x + 18, 207, budget, "small")
        scale_name = "Линейная шкала 0–0,5 с" if kind == "short" else (
            "Линейная шкала 0–650 с" if scale == "linear" else "Логарифмическая шкала 0,1–1000 с")
        text(x + 18, 235, scale_name, "small")

        def coordinate(value):
            if not minimum <= value <= maximum:
                raise ValueError("TTFT outside explicit plot bounds")
            ratio = ((math.log10(value) - math.log10(minimum)) /
                     (math.log10(maximum) - math.log10(minimum)) if scale == "log"
                     else (value - minimum) / (maximum - minimum))
            return x + 70 + ratio * 348

        for tick in ticks:
            tx = coordinate(tick)
            svg.append(f'<path d="M{tx:.3f},260V392" stroke="#e2e8f0"/>')
            label = str(tick).replace(".", ",")
            text(f"{tx:.3f}", 420, label, "small", "middle")
        text(x + 418, 445, "секунды до первого видимого ответа", "small", "end")
        for row, (stage, color) in enumerate((("a", "#2563eb"), ("b", "#d97706"))):
            wave = next(wave for wave in summary["waves"] if
                        (wave["model"], wave["kind"], wave["iteration"]) ==
                        ("selectel-gemma-" + stage, kind, iteration))
            if wave["completed"] != 10:
                raise ValueError("Expected ten responses per plotted interval")
            stats = wave["client_ttft_visible_answer_s"]
            y = 280 + row * 72
            left, middle, right = (coordinate(stats[key]) for key in ("min", "median", "max"))
            text(x + 20, y + 6, stage.upper(), "heading")
            svg.append(f'<g data-series="{stage}" data-count="10" data-min="{stats["min"]}" data-median="{stats["median"]}" data-max="{stats["max"]}">')
            svg.append(f'<path d="M{left:.3f},{y}H{right:.3f}M{left:.3f},{y-7}V{y+7}M{right:.3f},{y-7}V{y+7}" stroke="{color}" stroke-width="3"/>')
            svg.append(f'<circle cx="{middle:.3f}" cy="{y}" r="6" fill="{color}" stroke="white" stroke-width="1.5"/>')
            text(x + 70, y + 27, f'{number(stats["median"])} с; min–max {number(stats["min"])}–{number(stats["max"])} с', "small")
            svg.append('</g>')
        svg.append('</g>')
    text(32, 500, "Все 60 ответов корректны и завершились естественно. Выход — 13–14 токенов, лимит — 32 768.")
    text(32, 531, "Один прогон, последовательные волны. Внутри каждой волны 10 запросов делят общий префикс.", "small")
    text(32, 556, "Cold: префикс отсутствовал перед волной. Это не десять независимых документов и не общий коэффициент ускорения.", "small")
    text(32, 581, "Прямой vLLM, без WebUI/Bifrost. Отличается весь профиль; выигрыш нельзя приписать только offload.", "small")
    text(32, 615, f'SHA256: public raw {hashes["raw"][:16]}; summary {hashes["summary"][:16]}; original raw {summary["source_sha256"][:16]}', "small")
    svg.append('</svg>')
    return "\n".join(svg)
