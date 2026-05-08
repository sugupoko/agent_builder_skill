"""ノード 3: validate_rules — YAML ルールでハードチェック。

致命リスク（不正 auto_approve）の防御線。LLM の前段で必ず通す。
ここで reject / fix が出たら LLM での覆しは不可（rule_trace に必ず残る）。
"""
from __future__ import annotations

from datetime import date

from .db_client import lookup_policy
from .logger import logger
from .state import ReviewState


def _today() -> date:
    return date.today()


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def validate_rules_node(state: ReviewState) -> dict:
    logger.info("[3/8] validate_rules")
    cfg = state.get("cfg", {}) or {}
    parsed = state.get("parsed_fields", {}) or {}
    cat = parsed.get("category") or "misc"
    pol = lookup_policy(cat, cfg)

    violations: list = []
    trace: list = []

    # ---- 共通: 領収書必須 ----
    has_receipt = bool(state.get("has_attachment"))
    if not has_receipt:
        violations.append({"rule_id": "receipt_required", "severity": "fix",
                           "message": "領収書の添付がありません"})
    trace.append({"rule_id": "receipt_required", "result": "pass" if has_receipt else "fail"})

    # ---- 共通: 精算期限 ----
    receipt_d = _parse_date(parsed.get("receipt_date", ""))
    deadline_days = int(cfg.get("deadline_days", 60))
    today = _today()
    if receipt_d:
        days_old = (today - receipt_d).days
        if days_old > deadline_days:
            violations.append({
                "rule_id": "deadline_exceeded", "severity": "reject",
                "message": f"領収書日付が {days_old} 日前で精算期限（{deadline_days} 日）を超過しています",
            })
        trace.append({"rule_id": "deadline_exceeded", "result": "pass" if days_old <= deadline_days else "fail",
                      "days_old": days_old, "deadline_days": deadline_days})
    else:
        violations.append({"rule_id": "receipt_date_missing", "severity": "fix",
                           "message": "領収書日付が読み取れません"})
        trace.append({"rule_id": "receipt_date_missing", "result": "fail"})

    # ---- 共通: 金額上限超 → 承認権限チェック（簡易、申請者の grade を見ずに金額閾値のみ）----
    amount = int(parsed.get("amount_jpy", 0) or 0)
    approval_thresholds = cfg.get("approval_required_above_jpy", {}) or {}
    threshold = int(approval_thresholds.get(cat, approval_thresholds.get("general", 30000)))
    has_manager_approval = bool(parsed.get("manager_id_masked"))
    if amount > threshold and not has_manager_approval:
        violations.append({
            "rule_id": "approval_missing", "severity": "fix",
            "message": f"金額 {amount:,} 円が上限 {threshold:,} 円を超過、上長承認が記録されていません",
        })
    trace.append({
        "rule_id": "approval_required", "result": "pass" if amount <= threshold or has_manager_approval else "fail",
        "amount": amount, "threshold": threshold,
    })

    # ---- カテゴリ別: hospitality（接待） ----
    if cat == "hospitality":
        per_person_limit = int(pol.get("per_person_limit_jpy", 5000))
        n_participants = parsed.get("participants_count") or 1
        try:
            n_participants = int(n_participants)
        except (ValueError, TypeError):
            n_participants = 1
        per_person = amount / max(1, n_participants)
        if per_person > per_person_limit:
            severity = "review"  # グレーゾーンとして人手判断（重要顧客の例外あり）
            violations.append({
                "rule_id": "hospitality_per_person_exceeded", "severity": severity,
                "message": (f"接待単価 1 人あたり {per_person:,.0f} 円が上限 "
                            f"{per_person_limit:,} 円を超過しています（参加者 {n_participants} 名）"),
            })
        trace.append({"rule_id": "hospitality_per_person",
                      "result": "pass" if per_person <= per_person_limit else "fail",
                      "per_person": int(per_person), "limit": per_person_limit})

        if pol.get("counterparty_required") and not (parsed.get("counterparty") or "").strip():
            violations.append({
                "rule_id": "counterparty_required", "severity": "fix",
                "message": "接待相手先（社名・氏名）の記入が必要です",
            })
        trace.append({"rule_id": "counterparty_required",
                      "result": "pass" if (parsed.get("counterparty") or "").strip() else "fail"})

        if pol.get("participants_required") and not parsed.get("participants_count"):
            violations.append({
                "rule_id": "participants_required", "severity": "fix",
                "message": "接待参加者人数の記入が必要です",
            })
        trace.append({"rule_id": "participants_required",
                      "result": "pass" if parsed.get("participants_count") else "fail"})

        # 一人出張で接待単価超 → severity を review → reject に格上げ
        if n_participants == 1 and per_person > per_person_limit:
            violations.append({
                "rule_id": "single_person_hospitality_exceed", "severity": "reject",
                "message": "一人での接待単価超過は規程違反の可能性が高いため受理できません",
            })
        trace.append({"rule_id": "single_person_hospitality",
                      "result": "fail" if (n_participants == 1 and per_person > per_person_limit) else "pass"})

    # ---- カテゴリ別: meeting ----
    if cat == "meeting":
        per_person_limit = int(pol.get("per_person_limit_jpy", 1500))
        n_participants = parsed.get("participants_count") or 1
        try:
            n_participants = int(n_participants)
        except (ValueError, TypeError):
            n_participants = 1
        per_person = amount / max(1, n_participants)
        if per_person > per_person_limit:
            violations.append({
                "rule_id": "meeting_per_person_exceeded", "severity": "review",
                "message": (f"会議費単価 1 人 {per_person:,.0f} 円が上限 "
                            f"{per_person_limit:,} 円を超過しています"),
            })
        trace.append({"rule_id": "meeting_per_person",
                      "result": "pass" if per_person <= per_person_limit else "fail"})

    # ---- カテゴリ別: consumable（消耗品）---- 5000 円超は事前申請必要
    if cat == "consumable":
        pre_approval_above = int(pol.get("pre_approval_above_jpy", 5000))
        if amount > pre_approval_above and not parsed.get("pre_approval_id"):
            violations.append({
                "rule_id": "consumable_preapproval_required", "severity": "fix",
                "message": (f"消耗品費が {pre_approval_above:,} 円を超過、"
                            f"事前申請番号の記入が必要です"),
            })
        trace.append({"rule_id": "consumable_preapproval",
                      "result": "pass" if amount <= pre_approval_above or parsed.get("pre_approval_id") else "fail"})

    # ---- カテゴリ別: training ---- 上長承認必須
    if cat == "training":
        if not has_manager_approval:
            violations.append({
                "rule_id": "training_manager_approval_required", "severity": "fix",
                "message": "研修費は上長承認が必要です",
            })
        trace.append({"rule_id": "training_manager_approval",
                      "result": "pass" if has_manager_approval else "fail"})

    logger.info("  => violations=%d trace=%d", len(violations), len(trace))
    return {"rule_violations": violations, "rule_trace": trace}
