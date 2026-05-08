"""社内 DB のモック実装。v1 は YAML フィクスチャ。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import load_yaml

_CACHE: dict = {}
_FIXTURES: dict = {}


def load_fixtures(mock_db_dir: Path | str) -> None:
    p = Path(mock_db_dir)
    _FIXTURES["employees"] = load_yaml(p / "employees.yaml")
    _FIXTURES["past_claims"] = load_yaml(p / "past_claims.yaml")


def clear_tool_cache() -> None:
    _CACHE.clear()


def _cached(key: tuple, fn):
    if key in _CACHE:
        return _CACHE[key]
    r = fn()
    _CACHE[key] = r
    return r


def lookup_employee(employee_id: str) -> dict:
    """社員マスタ参照。employee_id は unmask 後の値で渡す前提。"""
    def _fn():
        rec = _FIXTURES.get("employees", {}).get(employee_id)
        if rec is None:
            return {"ok": False, "reason": "employee_not_found", "employee_id": employee_id}
        return {"ok": True, "employee_id": employee_id, **rec}
    return _cached(("emp", employee_id), _fn)


def lookup_policy(category: str, cfg: dict) -> dict:
    """expense_policy.yaml から取得。cfg は load_config 済の dict。"""
    pols = cfg.get("policies", {}) or {}
    if category in pols:
        return {"category": category, **pols[category]}
    # fallback: misc
    if "misc" in pols:
        return {"category": "misc", **pols["misc"]}
    return {"category": category, "ok": False, "reason": "unknown_category"}


def lookup_past_claims(applicant_id: str, store_name: str, amount_jpy: int,
                       reference_date: str, days: int = 90) -> dict:
    """過去 N 日以内の同申請者・同店名・同金額の重複検索。

    reference_date: 'YYYY-MM-DD' 形式の領収書日付（基準日）
    """
    def _fn():
        all_claims = _FIXTURES.get("past_claims", []) or []
        try:
            ref = date.fromisoformat(reference_date)
        except ValueError:
            return {"ok": False, "reason": "invalid_reference_date"}
        cutoff = ref - timedelta(days=days)

        matches = []
        for c in all_claims:
            if c.get("applicant_id") != applicant_id:
                continue
            try:
                d = date.fromisoformat(c.get("submitted_at", ""))
            except ValueError:
                continue
            if d < cutoff or d > ref:
                continue
            store_match = (
                c.get("store_name") == store_name
                or (store_name and store_name in (c.get("store_name") or ""))
            )
            amount_match = abs(int(c.get("amount_jpy", 0)) - int(amount_jpy)) < 100  # 100 円以内
            if store_match and amount_match:
                matches.append(c)

        return {"ok": True, "matches": matches, "match_count": len(matches)}

    return _cached(("past", applicant_id, store_name, amount_jpy, reference_date, days), _fn)
