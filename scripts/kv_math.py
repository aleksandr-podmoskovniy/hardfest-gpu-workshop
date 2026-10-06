#!/usr/bin/env python3
"""Ideal KV payload for the workshop models, not a vLLM allocation prediction."""
import argparse
import json


def calculate_gemma_e2b(tokens=4096, sessions=1, element_bytes=2):
    # google/gemma-4-E2B-it revision 3e22461f65e89153144f8adb70e3b8c2cc9845a7.
    # Of 35 layers, the last 20 reuse earlier KV; only 3 full + 12 local own it.
    # vLLM v0.31.0: Gemma4Attention.kv_sharing_target_layer_name.
    full = 2 * element_bytes * 3 * 1 * 512 * tokens
    local = 2 * element_bytes * 12 * 1 * 256 * min(tokens, 512)
    return {"kind": "theoretical, Gemma 4 E2B shared KV, ideal active payload only",
            "tokens": tokens, "sessions": sessions, "kv_bytes_per_element": element_bytes,
            "full_attention_gib": full / 1024**3,
            "sliding_attention_gib": local / 1024**3,
            "one_session_gib": (full + local) / 1024**3,
            "all_sessions_gib": sessions * (full + local) / 1024**3}


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
    p.add_argument("--model", choices=("gemma-31b", "gemma-e2b"), default="gemma-31b")
    p.add_argument("--tokens", type=int, default=131072)
    p.add_argument("--sessions", type=int, default=1)
    p.add_argument("--element-bytes", type=int, choices=(1, 2), default=2)
    a = p.parse_args()
    if a.tokens < 1 or a.sessions < 1:
        p.error("Positive tokens/sessions required")
    calculate = calculate_gemma_e2b if a.model == "gemma-e2b" else calculate_gemma
    print(json.dumps(calculate(a.tokens, a.sessions, a.element_bytes), indent=2))
