"""ノード 2: extract — 型番 / 注文番号を regex で抽出。

YAML から regex を読む（テストとプロダクションのドリフト防止 / tools_pattern.md）。
"""
from __future__ import annotations

import re

from .logger import logger
from .state import TriageState


def _compile_patterns(patterns: list) -> list:
    return [re.compile(p["regex"]) for p in patterns or []]


def extract_skus(text: str, sku_patterns: list) -> list:
    out: list = []
    for pat in _compile_patterns(sku_patterns):
        for m in pat.findall(text):
            if m and m not in out:
                out.append(m)
    return out


def extract_order_nos(text: str, order_patterns: list) -> list:
    out: list = []
    for pat in _compile_patterns(order_patterns):
        for m in pat.findall(text):
            if m and m not in out:
                out.append(m)
    return out


def extract_node(state: TriageState) -> dict:
    logger.info("[2/7] extract")
    cfg = state.get("cfg", {})
    text = state.get("masked_text", "")
    skus = extract_skus(text, cfg.get("sku_patterns", []))
    order_nos = extract_order_nos(text, cfg.get("order_no_patterns", []))
    confidence = 1.0 if (skus or order_nos) else 0.3
    logger.info("  => skus=%s order_nos=%s confidence=%.2f", skus, order_nos, confidence)
    return {
        "skus": skus,
        "order_nos": order_nos,
        "extract_confidence": confidence,
    }
