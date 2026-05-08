"""PII マスキング — 申請者氏名・社員 ID。LLM API 送信前に必ず通す。"""
from __future__ import annotations

import re
from typing import Any


def mask_pii(payload: dict) -> tuple[dict, dict]:
    """raw_payload の PII フィールドを placeholder に置換。

    対象:
        applicant_name → [NAME_001]
        employee_id    → [EMP_001]
        bank_account_* → [BANK_xxx]（あれば）
    """
    pii_map: dict = {}
    counts = {"NAME": 0, "EMP": 0, "BANK": 0}

    def _store(label: str, original: str) -> str:
        for k, v in pii_map.items():
            if v == original:
                return k
        counts[label] += 1
        key = f"[{label}_{counts[label]:03d}]"
        pii_map[key] = original
        return key

    masked: dict = {}
    for k, v in payload.items():
        if v is None:
            masked[k] = None
            continue
        if k in ("applicant_name",) and isinstance(v, str) and v.strip():
            masked[k] = _store("NAME", v)
        elif k in ("employee_id",) and isinstance(v, str) and v.strip():
            masked[k] = _store("EMP", v)
        elif k.startswith("bank_account") and isinstance(v, str) and v.strip():
            masked[k] = _store("BANK", v)
        elif k == "manager_employee_id" and isinstance(v, str) and v.strip():
            masked[k] = _store("EMP", v)
        else:
            masked[k] = v

    return masked, pii_map


def unmask_text(text: str, pii_map: dict) -> str:
    """テキスト中の placeholder を原文に復元。assemble の最終段で使う。"""
    for k, v in pii_map.items():
        if k.startswith("__"):
            continue
        text = text.replace(k, v)
    return text


def unmask_dict(d: Any, pii_map: dict) -> Any:
    """dict / list / str を再帰的に unmask。"""
    if isinstance(d, str):
        return unmask_text(d, pii_map)
    if isinstance(d, dict):
        return {k: unmask_dict(v, pii_map) for k, v in d.items()}
    if isinstance(d, list):
        return [unmask_dict(v, pii_map) for v in d]
    return d
