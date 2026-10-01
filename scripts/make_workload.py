#!/usr/bin/env python3
"""Run inside the pinned runtime with its local tokenizer; emits synthetic JSONL."""
import argparse
import json
import math
import sys


def chat_token_count(tokenizer, messages):
    # Transformers releases differ in their default return type. Counting a
    # BatchEncoding measures its keys, not tokens. Request the flat token list.
    tokens = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True, return_dict=False)
    if not isinstance(tokens, list) or not all(isinstance(t, int) for t in tokens):
        raise ValueError("Tokenizer did not return a flat list of token IDs")
    return len(tokens)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tokenizer", required=True)
    p.add_argument("--input-tokens", type=int, required=True)
    p.add_argument("--output-tokens", type=int, default=2048)
    p.add_argument("--context", type=int, required=True)
    p.add_argument("--documents", type=int, default=8)
    args = p.parse_args()
    if args.input_tokens < 128 or args.output_tokens < 1 or args.documents < 1:
        p.error("positive lengths/documents and input >= 128 required")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    for i in range(args.documents):
        # Unique document ID is at the START: the cold series does not share the whole prefix.
        prefix = f"Документ {i:08d}. Прочитай журнал эксперимента и составь подробный разбор.\n"
        unit = "Проверяем задержку очереди, время обработки документа и скорость генерации. Данные синтетические.\n"
        unit_tokens = max(1, len(tok.encode(unit, add_special_tokens=False)))
        repeats = math.ceil(args.input_tokens / unit_tokens * 1.2) + 32
        ids = tok.encode(prefix + unit * repeats, add_special_tokens=False)
        limit = args.input_tokens
        for _ in range(20):
            messages = [{"role": "user", "content": tok.decode(ids[:limit])}]
            actual = chat_token_count(tok, messages)
            delta = actual - args.input_tokens
            if abs(delta) <= 8:
                break
            limit = max(1, limit - delta)
        if abs(actual - args.input_tokens) > 8 or actual + args.output_tokens > args.context:
            raise ValueError("Could not meet the requested context budget")
        print(json.dumps({"messages": messages, "max_tokens": args.output_tokens,
                          "metadata": {"synthetic": True, "document": i, "input_tokens_local_template": actual,
                                       "requested_input_tokens": args.input_tokens, "tokenizer": args.tokenizer}},
                         ensure_ascii=False))
    print("Validate prompt_tokens returned by the SERVER before benchmarking.", file=sys.stderr)


if __name__ == "__main__":
    main()
