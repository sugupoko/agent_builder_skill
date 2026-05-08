"""ノード 2: extract — 楽楽精算 JSON のフィールドを正規化 + OCR 補完。

v1 では payload にすでに構造化フィールドが入っている前提。
ocr_text からの補完が必要なときだけ Haiku を呼ぶ（簡易、v1 ではほぼ不要）。
"""
from __future__ import annotations

from .logger import logger
from .state import ReviewState


def extract_node(state: ReviewState) -> dict:
    logger.info("[2/8] extract")
    payload = state.get("masked_payload", {}) or {}
    parsed = {
        "category": payload.get("category", "misc"),
        "amount_jpy": int(payload.get("amount_jpy", 0) or 0),
        "receipt_date": payload.get("receipt_date") or "",
        "submitted_date": payload.get("submitted_date") or "",
        "store_name": payload.get("store_name") or payload.get("merchant", ""),
        "counterparty": payload.get("counterparty"),
        "participants_count": payload.get("participants_count"),
        "participants_names": payload.get("participants_names") or [],
        "applicant_id_masked": payload.get("employee_id"),
        "manager_id_masked": payload.get("manager_employee_id"),
        "description": payload.get("description", ""),
        "ocr_text": payload.get("ocr_text", ""),
    }
    confidence = 1.0 if parsed["category"] and parsed["amount_jpy"] > 0 else 0.5
    logger.info(
        "  => category=%s amount=%s store=%s parts=%s",
        parsed["category"], parsed["amount_jpy"], parsed["store_name"],
        parsed.get("participants_count"),
    )
    return {"parsed_fields": parsed, "extract_confidence": confidence}
