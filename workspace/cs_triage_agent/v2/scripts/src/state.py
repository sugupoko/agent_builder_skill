"""TriageState (TypedDict) — LangGraph に流す全フィールドをここに宣言する。

未宣言のフィールドは silent drop されるため、ノード追加時は必ずここに追記する。
"""
from __future__ import annotations

from typing import Literal, TypedDict


class TriageState(TypedDict, total=False):
    # Input
    raw_text: str
    case_id: str
    customer_id: str

    # preprocess output
    masked_text: str
    pii_map: dict
    lang: Literal["ja", "en", "other"]
    has_attachment: bool

    # extract output
    skus: list
    order_nos: list
    extract_confidence: float

    # classify output
    category: Literal["inventory", "tech", "alternative", "shipment",
                      "cad", "billing", "complaint", "other"]
    urgency: Literal["low", "normal", "high"]
    complaint_smell: bool
    classify_confidence: float

    # retrieve output
    retrieved_data: dict
    retrieve_errors: list

    # draft output
    draft_body: str
    internal_memo: str
    missing_info: list

    # reflect output
    reflect_pass: bool
    reflect_issues: list
    reflect_iter: int

    # assemble output
    final_output: dict

    # control
    cfg: dict
    dry_run: bool
    lite_mode: bool
    used_models: list
    cost_usd: float
