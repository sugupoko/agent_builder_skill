"""ノード 1: preprocess — PII マスキング + 添付検出。"""
from __future__ import annotations

from .logger import logger
from .pii_mask import mask_pii
from .state import ReviewState


def preprocess_node(state: ReviewState) -> dict:
    logger.info("[1/8] preprocess")
    raw = state.get("raw_payload", {}) or {}
    masked, pii_map = mask_pii(raw)
    has_attach = bool(raw.get("receipt_url")) or bool(raw.get("attachments"))
    pii_count = len([k for k in pii_map if not k.startswith("__")])
    logger.info("  => pii=%d attachment=%s", pii_count, has_attach)
    return {"masked_payload": masked, "pii_map": pii_map, "has_attachment": has_attach}
