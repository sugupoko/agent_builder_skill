"""LLM 利用コストの計測。各ノードで accumulate_usage(messages) を呼ぶ。"""
from __future__ import annotations

# 公開レート（2026-01 時点）。最新は API コンソールで確認のこと。
RATES = {
    "claude-sonnet-4-6": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
    },
}

USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def reset_usage() -> None:
    USAGE["input_tokens"] = 0
    USAGE["output_tokens"] = 0
    USAGE["cost_usd"] = 0.0


def accumulate_usage(messages, model: str) -> None:
    rate = RATES.get(model, RATES["claude-sonnet-4-6"])
    for m in messages:
        meta = getattr(m, "usage_metadata", None) or {}
        in_t = int(meta.get("input_tokens", 0) or 0)
        out_t = int(meta.get("output_tokens", 0) or 0)
        USAGE["input_tokens"] += in_t
        USAGE["output_tokens"] += out_t
        USAGE["cost_usd"] += in_t * rate["input"] + out_t * rate["output"]


def snapshot() -> dict:
    return dict(USAGE)
