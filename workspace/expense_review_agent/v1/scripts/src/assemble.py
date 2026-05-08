"""ノード 8: assemble — rule_trace 統合 + unmask + 最終 JSON。"""
from __future__ import annotations

from .cost import snapshot
from .logger import logger
from .pii_mask import unmask_dict, unmask_text
from .state import ReviewState


def assemble_node(state: ReviewState) -> dict:
    logger.info("[8/8] assemble")
    pii_map = state.get("pii_map", {}) or {}

    feedback = unmask_text(state.get("feedback_message", "") or "", pii_map)
    memo = unmask_text(state.get("internal_memo", "") or "", pii_map)

    parsed = state.get("parsed_fields", {}) or {}
    cost = snapshot()

    final = {
        "application_id": state.get("application_id", ""),
        "schema_version": "v1.0",
        "decision": state.get("decision", "needs_review"),
        "risk_score": round(float(state.get("risk_score", 0.0) or 0.0), 3),
        "needs_human_review": state.get("decision") in ("needs_review", "reject")
                              or len(state.get("duplicate_candidates", []) or []) > 0,
        "reasons": [unmask_text(r, pii_map) for r in (state.get("reasons", []) or [])],
        "suggested_fixes": [unmask_text(s, pii_map) for s in (state.get("suggested_fixes", []) or [])],
        "feedback_message": feedback,
        "internal_memo": memo,
        "rule_trace": state.get("rule_trace", []) or [],
        "meta": {
            "category": parsed.get("category"),
            "amount_jpy": parsed.get("amount_jpy"),
            "participants_count": parsed.get("participants_count"),
            "store_name": parsed.get("store_name"),
            "receipt_date": parsed.get("receipt_date"),
            "applicant_id": unmask_text(parsed.get("applicant_id_masked", "") or "", pii_map),
            "duplicate_count": len(state.get("duplicate_candidates", []) or []),
            "used_models": state.get("used_models", []) or [],
            "cost_usd": round(cost.get("cost_usd", 0.0), 6),
            "lang": "ja",
        },
        "flags": {
            "duplicate_suspected": len(state.get("duplicate_candidates", []) or []) > 0,
            "ai_classify_failed": False,    # TODO: classify_gray の失敗フラグ反映
            "ai_draft_failed": False,
            "pii_warning": bool(pii_map.get("__warning__")),
            "reflect_warning": (not state.get("reflect_pass", True)) and (state.get("reflect_iter", 0) >= 1),
        },
    }
    logger.info("  => assembled decision=%s feedback=%d chars cost=$%.4f",
                final["decision"], len(feedback), cost.get("cost_usd", 0.0))
    return {"final_output": final}
