#!/usr/bin/env python3
"""Theoretical GPT-OSS example from the slides, NOT a Gemma memory planner."""
import argparse
import json
import math


def calculate(tokens=131072, sessions=1, element_bytes=2, gpu_gib=96, utilization=.9, weights_gib=64, reserve_gib=6):
    per_layer_token = 2 * 8 * 64 * element_bytes
    full = per_layer_token * 18 * tokens
    local = per_layer_token * 18 * min(tokens, 128)
    per_session = (full + local) / 1024**3
    pool = gpu_gib * utilization - weights_gib - reserve_gib
    return {"kind": "theoretical, GPT-OSS full+sliding layers, ideal payload only",
            "tokens": tokens, "sessions": sessions, "kv_bytes_per_element": element_bytes,
            "per_layer_token_bytes": per_layer_token, "one_session_gib": per_session,
            "all_sessions_gib": sessions * per_session, "assumed_pool_gib": pool,
            "ideal_full_sessions": max(0, math.floor(pool / per_session)),
            "ideal_one_way_transfer_s_at_25_GiBps": per_session / 25}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tokens", type=int, default=131072)
    p.add_argument("--sessions", type=int, default=1)
    p.add_argument("--element-bytes", type=int, choices=(1, 2), default=2)
    a = p.parse_args()
    if a.tokens < 1 or a.sessions < 1:
        p.error("Positive tokens/sessions required")
    print(json.dumps(calculate(a.tokens, a.sessions, a.element_bytes), indent=2))
