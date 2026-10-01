#!/usr/bin/env python3
"""Ideal KV payload for the workshop models, not a vLLM allocation prediction."""
import argparse
import json


def calculate_gemma(tokens=131072, sessions=1, element_bytes=2):
    # google/gemma-4-31B-it revision 842da3794eaa0b77d5f08bae87a17459d91ff475.
    # vLLM stores both K and V even when checkpoint attention_k_eq_v is true.
    full = 2 * element_bytes * 10 * 4 * 512 * tokens
    local = 2 * element_bytes * 50 * 16 * 256 * min(tokens, 1024)
    per_session = (full + local) / 1024**3
    return {"kind": "theoretical, Gemma 4 31B full+sliding layers, ideal active payload only",
            "tokens": tokens, "sessions": sessions, "kv_bytes_per_element": element_bytes,
            "full_attention_gib": full / 1024**3,
            "sliding_attention_gib": local / 1024**3,
            "one_session_gib": per_session, "all_sessions_gib": sessions * per_session}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tokens", type=int, default=131072)
    p.add_argument("--sessions", type=int, default=1)
    p.add_argument("--element-bytes", type=int, choices=(1, 2), default=2)
    a = p.parse_args()
    if a.tokens < 1 or a.sessions < 1:
        p.error("Positive tokens/sessions required")
    print(json.dumps(calculate_gemma(a.tokens, a.sessions, a.element_bytes), indent=2))
