"""ノード 6: draft_decision — 4 判定 + 申請者向けフィードバック文言。

判定ロジック:
- reject 違反あり → reject 確定（LLM は文言整形のみ）
- needs_review 違反 or 重複 + 高 risk → needs_review
- needs_fix 違反のみ → needs_fix
- 違反 0 + 重複 0 + 低 risk → auto_approve
"""
from __future__ import annotations

import json
import re

from .cost import accumulate_usage
from .logger import logger
from .state import ReviewState


def _decide(state: ReviewState) -> str:
    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []
    risk = float(state.get("risk_score", 0.0) or 0.0)
    history_errors = state.get("history_lookup_errors", []) or []

    has_reject = any(v.get("severity") == "reject" for v in violations)
    has_review = any(v.get("severity") == "review" for v in violations)
    has_fix = any(v.get("severity") == "fix" for v in violations)

    if has_reject:
        return "reject"
    # 重複検出 DB 不通 or 重複候補あり → 安全側で needs_review
    if duplicates or any(e == "db_unavailable" for e in history_errors):
        return "needs_review"
    if has_review:
        return "needs_review"
    if has_fix:
        return "needs_fix"
    if risk >= 0.6:
        return "needs_review"
    return "auto_approve"


def _rule_based_draft(state: ReviewState, decision: str) -> dict:
    """LLM 不在時 / dry-run 時のテンプレ。"""
    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []

    reasons = [v.get("message", "") for v in violations if v.get("message")]
    suggested = []
    if decision in ("needs_fix", "needs_review", "reject"):
        for v in violations:
            rid = v.get("rule_id", "")
            if rid == "receipt_required":
                suggested.append("領収書の画像を添付して再申請してください")
            elif rid == "deadline_exceeded":
                suggested.append("精算期限超過のため、所属長宛に経緯説明書を提出のうえ別途協議してください")
            elif rid == "approval_missing":
                suggested.append("上長承認をいただいてから再申請してください")
            elif rid == "counterparty_required":
                suggested.append("接待相手先（社名・氏名・人数）を摘要欄に追記してください")
            elif rid == "participants_required":
                suggested.append("参加人数を入力してください")
            elif rid == "hospitality_per_person_exceeded":
                suggested.append("単価超過の業務理由（重要顧客との会食等）を申請理由に追記してください")
            elif rid == "single_person_hospitality_exceed":
                suggested.append("一人での接待は規程外のため、参加者を確認のうえ再申請してください")
            elif rid == "consumable_preapproval_required":
                suggested.append("事前申請番号を摘要欄に追記してください")
            elif rid == "training_manager_approval_required":
                suggested.append("研修費は上長承認が必要です。承認を取得してから再申請してください")

    if duplicates:
        d = duplicates[0]
        reasons.append(
            f"過去 90 日以内に同一店名・同一金額の申請が見つかりました（{d.get('claim_id')} / {d.get('submitted_at')}）"
        )
        suggested.append("重複申請の可能性があります。前回申請との関係を摘要欄に追記してください")

    # フィードバック文言（テンプレ）
    if decision == "auto_approve":
        body = "ご申請を確認のうえ、規程内のため自動承認といたしました。今後ともよろしくお願いいたします。"
        memo = "ルール違反なし、重複なし、低リスク、自動承認。"
    elif decision == "needs_fix":
        body_lines = ["お世話になっております。ご申請を確認させていただきました。",
                      "以下の点をご確認のうえ、修正後の再申請をお願いいたします。"]
        for r in reasons:
            body_lines.append(f"・{r}")
        if suggested:
            body_lines.append("\n【ご対応のお願い】")
            for s in suggested:
                body_lines.append(f"・{s}")
        body_lines.append("\nご不明点がございましたら経理担当までお問い合わせください。")
        body = "\n".join(body_lines)
        memo = f"軽微な不備 {len(violations)} 件、申請者修正で完了見込み。"
    elif decision == "needs_review":
        body = ("お世話になっております。ご申請を受領いたしました。"
                "規程に照らして要確認事項がございますため、経理担当より追ってご連絡いたします。"
                "今しばらくお待ちください。")
        memo_parts = ["要人手判断"]
        for v in violations:
            memo_parts.append(v.get("message", ""))
        if duplicates:
            memo_parts.append(f"重複候補 {len(duplicates)} 件あり")
        memo = " / ".join(memo_parts)[:300]
    else:  # reject
        body_lines = ["お世話になっております。誠に恐れ入りますが、本申請は規程に照らして受理が難しい状況です。"]
        for r in reasons:
            body_lines.append(f"・{r}")
        if suggested:
            body_lines.append("\n【ご対応】")
            for s in suggested:
                body_lines.append(f"・{s}")
        body_lines.append("\n詳細は経理担当までご相談ください。")
        body = "\n".join(body_lines)
        memo = f"規程違反により reject。理由: {reasons[0] if reasons else ''}"

    return {
        "decision": decision,
        "reasons": reasons,
        "suggested_fixes": suggested,
        "feedback_message": body,
        "internal_memo": memo,
    }


def draft_decision_node(state: ReviewState) -> dict:
    logger.info("[6/8] draft_decision")
    decision = _decide(state)

    if state.get("dry_run"):
        out = _rule_based_draft(state, decision)
        logger.info("  => (dry-run) decision=%s reasons=%d", decision, len(out["reasons"]))
        return out

    # LLM で文言を整形（4 判定はコードで決定済、LLM は body / suggested_fixes の質を上げるだけ）
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    cfg = state.get("cfg", {}) or {}
    parsed = state.get("parsed_fields", {}) or {}
    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []
    gray = state.get("gray_judgments", []) or []

    lite = state.get("lite_mode", False)
    model_key = "draft_decision_lite" if lite else "draft_decision"
    model = (cfg.get("models", {}) or {}).get(model_key, "claude-sonnet-4-6")

    system = (
        "あなたは中堅 SaaS 企業のベテラン経理担当です。"
        "申請者向けのフィードバック文言を敬体で起草します。"
        "判定（auto_approve/needs_fix/needs_review/reject）は外部システムが既に決定済みのため、"
        "あなたは『その判定理由を申請者に分かりやすく伝える文言』と『具体的な修正例』を作る役割です。"
    )

    fallback = _rule_based_draft(state, decision)

    user = f"""以下の経費申請の判定が決まりました。申請者向けフィードバック文言を起草してください。

## 判定（決定済、これを覆さない）
{decision}

## 申請内容
- カテゴリ: {parsed.get('category')}
- 金額: {parsed.get('amount_jpy'):,} 円
- 店名: {parsed.get('store_name')}
- 相手先: {parsed.get('counterparty')}
- 参加人数: {parsed.get('participants_count')}

## ハードルール違反
{json.dumps(violations, ensure_ascii=False, indent=2)}

## 重複候補
{json.dumps(duplicates, ensure_ascii=False, indent=2) if duplicates else "なし"}

## グレーゾーン判断
{json.dumps(gray, ensure_ascii=False, indent=2) if gray else "なし"}

## 制約
- 敬体、建設的なトーン（「絶対に承認できません」のような断定否定は禁止）
- 内部用語禁止（「経理判断」「上の判断」等）
- needs_fix / reject の場合、**具体的な修正例を 1〜3 件**必ず添える
- 文字数: 申請者向け本文 100〜400 文字
- needs_review の場合: 「経理担当より追って連絡」と書き、申請者に手戻りを発生させない

## 出力 (JSON のみ)
{{
  "feedback_message": "<申請者向け本文>",
  "suggested_fixes": ["<修正例1>", "<修正例2>"],
  "internal_memo": "<経理向け 100 字以内、グレー判断や引き継ぎ事項>"
}}"""

    try:
        llm = ChatAnthropic(model=model, max_tokens=900)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON: {content[:200]}")
        data = json.loads(m.group(0))
        out = {
            "decision": decision,  # 必ずコード判定を採用
            "reasons": fallback["reasons"],
            "suggested_fixes": list(data.get("suggested_fixes", []) or []) or fallback["suggested_fixes"],
            "feedback_message": str(data.get("feedback_message", "")).strip() or fallback["feedback_message"],
            "internal_memo": str(data.get("internal_memo", "")).strip()[:300] or fallback["internal_memo"],
        }
        logger.info("  => decision=%s feedback_len=%d", decision, len(out["feedback_message"]))
        return out
    except Exception as e:
        logger.warning("draft_decision LLM failed: %s — fallback template", e)
        return fallback
