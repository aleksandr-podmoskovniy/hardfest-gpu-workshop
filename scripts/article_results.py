#!/usr/bin/env python3
"""Build article tables and figures from public measurements; no cluster access."""
import argparse
import json
from pathlib import Path
import re

from build_diagrams import Diagram, BLUE, TEAL, MUTED, GRAY, MINT, PALE
import pro6000_capacity_results as pro6000

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("all", "h100-rtx", "pro", "pro-capacity-only")
CAPACITY_ONLY_CHAT_NOTICE = (
    "Сопоставимая серия обычных ответов Base, Cache и Tune ещё не завершена; "
    "её численные результаты здесь не публикуются.\n\n"
    "Проверки вместимости используют разные окна, а нагрузки на узел пересекались. "
    "По ним нельзя вычислять ускорение настроек или судить о задержках обычного чата."
)


def read(name):
    return json.loads((ROOT / name).read_text())


def number(value, places=2):
    return f"{value:,.{places}f}".replace(",", " ").replace(".", ",")


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| --- |" + " ---: |" * (len(headers) - 1),
                      *("| " + " | ".join(row) + " |" for row in rows)])


def rtx_results(report):
    """Reject partial or mismatched workloads instead of rendering a speedup."""
    labels = ("base-4k", "cache-4k", "tune-4k", "tune-128k-short", "dp-128k-short")
    result = {}
    for label in labels:
        for series in (0, 1):
            entries = [r for r in report["results"] if r["label"] == label and r["series"] == series]
            if len(entries) != 1:
                raise ValueError(f"Missing or duplicate result: {label}/{series}")
            row = entries[0]["result"]
            expected = {"num_prompts": 8, "max_concurrency": 2, "completed": 8,
                        "failed": 0, "total_input_tokens": 16384, "total_output_tokens": 1024}
            if any(row.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Incomparable workload: {label}/{series}")
            result[label, series] = row
    for label in labels[:3]:
        profile = report["profiles"][label]
        if (profile["max_model_len"], profile["gpu_memory_utilization"], profile["max_num_seqs"]) != (4096, .9, 2):
            raise ValueError("Changed control profile")
    return result


def score_figure(name, title, subtitle, columns, rows, notes, footer):
    """Aligned score rows, not misleading bars with incompatible time scales."""
    d = Diagram(name, title, subtitle)
    xs = (48, 466, 706, 946)
    for x, label in zip(xs, columns):
        d.text(x + 20, 173, label, 20, MUTED, True, width=370 if x == 48 else 185)
    for i, (label, values) in enumerate(rows):
        y = 197 + 130 * i
        d.rect(48, y, 1104, 108, GRAY)
        d.text(68, y + 43, label, 23, bold=True, width=370)
        for j, value in enumerate(values):
            d.text(xs[j + 1] + 20, y + 58, value, 31,
                   (MUTED, BLUE, TEAL)[j], True, width=185)
    d.text(48, 627, notes, 20, MUTED, width=1104)
    d.footer(footer)
    return "\n".join(d.parts + ["</svg>"]) + "\n"


def build_h100_rtx():
    """Independent retained H100/RTX sections; never reads PRO measurements."""
    blocks, figures = {}, {}
    # H100 is one first-use/replay pair with offload ON in both requests, not A/B.
    h = read("results/kv-ram/observations.json")
    first, repeat = h["raw_request_observations"]
    expected = {"prompt_tokens": 65536, "completion_tokens": 128, "total_tokens": 65664}
    if (any(r["usage"] != expected or r["error"] or r["finish_reason"] != "length"
            for r in (first, repeat)) or h["workload"]["concurrency"] != 1
            or first["document"] != repeat["document"]
            or (first["occurrence"], repeat["occurrence"]) != (1, 2)
            or h["engine_version"] != "0.30.0"):
        raise ValueError("H100 replay does not match the published workload")
    counters = h["return_request_counter_samples"]
    restored = next(r["after"] - r["before"] for r in counters
                    if r["name"] == "vllm:kv_offload_total_bytes_total"
                    and r["labels"]["transfer_type"] == "CPU_to_GPU")
    external = next(r["after"] - r["before"] for r in counters
                    if r["name"] == "vllm:external_prefix_cache_hits_total")
    local = next(r["after"] - r["before"] for r in counters
                 if r["name"] == "vllm:prompt_tokens_by_source_total"
                 and r["labels"]["source"] == "local_compute")
    gpu_hits = next(r["after"] - r["before"] for r in counters
                    if r["name"] == "vllm:prefix_cache_hits_total")
    if external + local != 65536 or restored <= 0 or gpu_hits != 0:
        raise ValueError("H100 replay counters do not reconcile")
    blocks["h100-replay"] = table(
        ["Один и тот же документ", "Первое обращение", "Повтор после вытеснения"],
        [["До первого токена, с", number(first["ttft_s"]), number(repeat["ttft_s"])],
         ["Полный ответ, с", number(first["e2e_s"]), number(repeat["e2e_s"])],
         ["Фактический вход / выход", "65 536 / 128", "65 536 / 128"]])
    d = Diagram("h100-offload-result", "H100: повтор документа после вытеснения",
                "Один клиент, 65 536 токенов входа + 128 выхода. Offload включён в обоих запросах.")
    for x, label, fill, color, row in (
        (48, "Первое обращение", PALE, BLUE, first),
        (612, "Повтор из RAM", MINT, TEAL, repeat),
    ):
        d.rect(x, 164, 540, 274, fill)
        d.text(x + 24, 205, label, 25, color, True, width=492)
        d.text(x + 24, 246, "До первого токена", 20, MUTED)
        d.text(x + 24, 309, number(row["ttft_s"]) + " с", 48, color, True)
        d.text(x + 24, 388, "Полный ответ: " + number(row["e2e_s"]) + " с", 26, width=492)
    d.card(48, 462, 1104, 129, "Возврат из RAM подтверждён счётчиками",
           [f"{number(restored / 1024**3)} GiB передано на GPU; {number(external, 0)} токена восстановлены.",
            f"{number(local, 0)} токена вычислены заново; локальных GPU cache hits в повторе — 0."], MINT, TEAL)
    d.text(48, 635, ["Один повтор на vLLM 0.30. Не сравнение Base / Tune.",
                    "Контроль без CPU KV не завершён; общего коэффициента ускорения здесь нет."],
           20, MUTED, width=1104)
    d.footer("Источник: results/kv-ram/observations.json. Это проверка механизма, не ёмкости.")
    figures[d.name] = "\n".join(d.parts + ["</svg>"]) + "\n"

    r = rtx_results(read("results/rtx5060-rehearsal-20261008.json"))
    stages = (("base-4k", "Base"), ("cache-4k", "Cache"), ("tune-4k", "Tune"))
    blocks["rtx-stages"] = table(
        ["Профиль / проход", "TTFT, мс", "TPOT, мс", "Выход, ток/с", "Вся серия, с"],
        [[f"{name} — {'первый' if series == 0 else 'повтор'}",
          *(number(r[label, series][key]) for key in
            ("mean_ttft_ms", "mean_tpot_ms", "output_throughput", "duration"))]
         for label, name in stages for series in (0, 1)])
    figures["rtx5060-tuning-result"] = score_figure(
        "rtx5060-tuning-result", "RTX 5060 Ti: что меняется на каждом шаге",
        "Повтор серии: 8 запросов по 2048 + 128 токенов; до двух одновременно. Все профили 4K.",
        ["Метрика", "Base", "Cache", "Tune"],
        [(label, [number(r[stage, 1][key], places) for stage, _ in stages]) for label, key, places in
         [("До первого токена, мс", "mean_ttft_ms", 1),
          ("На выходной токен, мс", "mean_tpot_ms", 2),
          ("Общий выход, ток/с", "output_throughput", 1)]],
        ["TTFT и TPOT — средние по запросам; ток/с — вся серия. Все 8/8 ответов завершены.",
         "Повтор использует те же входы. Результат 4K не измеряет текущий Tune с окном 128K."],
        "Источник: results/rtx5060-rehearsal-20261008.json. Первые проходы — в таблице статьи.")
    blocks["rtx-platform"] = table(
        ["Окно 128K / проход", "Ручная Tune, ток/с", "AI Inference, ток/с"],
        [[name, number(r["tune-128k-short", series]["output_throughput"]),
          number(r["dp-128k-short", series]["output_throughput"])]
         for series, name in ((0, "Первый проход"), (1, "Повтор"))])

    return blocks, figures


def build(scope="all", pro_folder=pro6000.DEFAULT_FOLDER, source_root=ROOT):
    """Full PRO requires capacity and matched; omission is an explicit scope only."""
    if scope not in SCOPES:
        raise ValueError("Unknown article results scope")
    blocks, figures = {}, {}
    if scope in ("all", "pro", "pro-capacity-only"):
        blocks, figures = pro6000.build(pro_folder)
        if scope == "pro-capacity-only":
            # Never hide a completed or malformed export behind a missing-results notice.
            if (Path(pro_folder) / "gemma-matched-chat.json").exists():
                raise ValueError("Matched evidence exists; use --scope pro to validate it")
            blocks["pro-chat"] = CAPACITY_ONLY_CHAT_NOTICE
        else:
            chat_blocks, chat_figures = pro6000.build_matched(pro_folder, source_root)
            blocks.update(chat_blocks)
            figures.update(chat_figures)
    if scope in ("all", "h100-rtx"):
        retained_blocks, retained_figures = build_h100_rtx()
        blocks.update(retained_blocks)
        figures.update(retained_figures)
    return blocks, figures


def outputs(scope="all", pro_folder=pro6000.DEFAULT_FOLDER, source_root=ROOT):
    # Validate the entire selected scope before preparing any output file.
    blocks, figures = build(scope, pro_folder, source_root)
    documents = {}
    if scope in ("all", "h100-rtx"):
        documents.update({"README.md": ["h100-replay"], "RTX5060.md": ["rtx-stages", "rtx-platform"]})
    if scope in ("all", "pro", "pro-capacity-only"):
        documents["RTXPRO6000.md"] = ["pro-chat", "pro-capacity", "pro-qwen"]
    output = {ROOT / f"assets/{name}.svg": content for name, content in figures.items()}
    for name, keys in documents.items():
        path = ROOT / name
        content = path.read_text()
        for key in keys:
            start, end = f"<!-- result:{key} -->", f"<!-- /result:{key} -->"
            content, count = re.subn(re.escape(start) + r".*?" + re.escape(end),
                                     start + "\n" + blocks[key] + "\n" + end, content, flags=re.S)
            if count != 1:
                raise ValueError(f"Expected exactly one results block: {name}/{key}")
        output[path] = content
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without modifying files")
    parser.add_argument("--scope", choices=SCOPES, default="all",
                        help="all/pro require four fresh capacity bundles and matched chat; "
                             "h100-rtx excludes PRO; pro-capacity-only requires all four capacity "
                             "bundles and explicitly replaces missing matched chat with a notice")
    parser.add_argument("--pro-folder", type=Path, default=pro6000.DEFAULT_FOLDER)
    args = parser.parse_args()
    changed = []
    try:
        prepared = outputs(args.scope, args.pro_folder)
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit("Article results blocked: " + str(error)) from error
    for path, expected in prepared.items():
        if not path.exists() or path.read_text() != expected:
            changed.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.write_text(expected)
    if args.check and changed:
        raise SystemExit("Stale article results: " + ", ".join(changed))
    print("Article results match public measurements (scope: " + args.scope + ")." if args.check
          else "Updated (scope: " + args.scope + "): " + ", ".join(changed))
