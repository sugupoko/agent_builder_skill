"""ノード 7: assemble — メタ JSON + 本文 + 内部メモを統合し、PII を unmask する。

ここで初めて PII が復元される。出力先（ファイル / Salesforce API）に渡す JSON を組み立てる。
"""
from __future__ import annotations

from .cost import snapshot
from .logger import logger
from .pii_mask import unmask
from .state import TriageState


def assemble_node(state: TriageState) -> dict:
    logger.info("[7/7] assemble")
    pii_map = state.get("pii_map", {}) or {}
    body_unmasked = unmask(state.get("draft_body", ""), pii_map)
    memo_unmasked = unmask(state.get("internal_memo", ""), pii_map)

    cost = snapshot()
    pii_warning = bool(pii_map.get("__warning__"))
    reflect_pass = state.get("reflect_pass", True)
    reflect_iter = state.get("reflect_iter", 0)

    final = {
        "case_id": state.get("case_id", ""),
        "schema_version": "v1.0",
        "meta": {
            "category": state.get("category", "other"),
            "urgency": state.get("urgency", "normal"),
            "skus": state.get("skus", []) or [],
            "order_nos": state.get("order_nos", []) or [],
            "complaint_smell": state.get("complaint_smell", False),
            "classify_confidence": state.get("classify_confidence", 0.0),
            "extract_confidence": state.get("extract_confidence", 0.0),
            "used_models": state.get("used_models", []) or [],
            "cost_usd": round(cost.get("cost_usd", 0.0), 6),
            "lang": state.get("lang", "ja"),
            "has_attachment": state.get("has_attachment", False),
            "reflect_pass": reflect_pass,
            "reflect_iter": reflect_iter,
        },
        "customer_body": body_unmasked,
        "internal_memo": memo_unmasked,
        "missing_info": state.get("missing_info", []) or [],
        "flags": {
            "needs_supervisor": bool(state.get("complaint_smell")),
            "ai_generation_failed": False,
            "pii_warning": pii_warning,
            "reflect_warning": (not reflect_pass) and reflect_iter >= 1,
        },
    }
    logger.info("  => assembled (body=%d chars, cost=$%.4f)",
                len(body_unmasked), cost.get("cost_usd", 0.0))
    return {"final_output": final}
