"""ノード 4: lookup_history — 過去 N 日の重複検出。"""
from __future__ import annotations

from .db_client import lookup_past_claims
from .logger import logger
from .state import ReviewState


def lookup_history_node(state: ReviewState) -> dict:
    logger.info("[4/8] lookup_history")
    parsed = state.get("parsed_fields", {}) or {}
    cfg = state.get("cfg", {}) or {}
    days = int(cfg.get("duplicate_lookup_days", 90))

    applicant_id = parsed.get("applicant_id_masked") or ""
    # masked → unmask して DB へ。テストでは masked のままで OK（モックは masked を扱う設計）
    pii_map = state.get("pii_map", {}) or {}
    real_id = pii_map.get(applicant_id, applicant_id)

    store = parsed.get("store_name") or ""
    amount = int(parsed.get("amount_jpy", 0) or 0)
    ref_date = parsed.get("receipt_date") or ""

    if not (real_id and store and amount and ref_date):
        logger.info("  => skip (missing fields)")
        return {"duplicate_candidates": [], "history_lookup_errors": ["missing_required_fields"]}

    result = lookup_past_claims(real_id, store, amount, ref_date, days=days)
    if not result.get("ok"):
        logger.info("  => DB error: %s", result.get("reason"))
        return {"duplicate_candidates": [], "history_lookup_errors": [result.get("reason", "unknown")]}

    matches = result.get("matches", [])
    logger.info("  => duplicate_count=%d", len(matches))
    return {"duplicate_candidates": matches, "history_lookup_errors": []}
