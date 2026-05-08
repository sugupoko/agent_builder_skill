"""ReviewState — LangGraph に流す全フィールドを宣言。silent drop 罠回避。"""
from __future__ import annotations

from typing import Literal, TypedDict


class ReviewState(TypedDict, total=False):
    # Input
    raw_payload: dict
    application_id: str

    # preprocess
    masked_payload: dict
    pii_map: dict
    has_attachment: bool

    # extract
    parsed_fields: dict
    extract_confidence: float

    # validate_rules
    rule_violations: list
    rule_trace: list

    # lookup_history
    duplicate_candidates: list
    history_lookup_errors: list

    # classify_gray
    gray_judgments: list
    risk_score: float

    # draft_decision
    decision: Literal["auto_approve", "needs_fix", "needs_review", "reject"]
    reasons: list
    suggested_fixes: list
    feedback_message: str
    internal_memo: str

    # reflect
    reflect_pass: bool
    reflect_issues: list
    reflect_iter: int

    # assemble
    final_output: dict

    # control
    cfg: dict
    dry_run: bool
    lite_mode: bool
    used_models: list
    cost_usd: float
