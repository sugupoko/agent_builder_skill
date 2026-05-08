"""ノード 5: classify_gray — グレーゾーンの妥当性判断 + リスクスコア。

LLM (Sonnet) で「単価超だが重要顧客の例外か」「店名と科目の不整合か」等を判定。
"""
from __future__ import annotations

import json
import re

from .cost import accumulate_usage
from .logger import logger
from .state import ReviewState


def _rule_based_gray(state: ReviewState) -> dict:
    """LLM 不在時のフォールバック。"""
    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []
    has_review_severity = any(v.get("severity") == "review" for v in violations)
    has_reject_severity = any(v.get("severity") == "reject" for v in violations)
    risk = 0.2
    if duplicates:
        risk = 0.7
    if has_review_severity:
        risk = max(risk, 0.6)
    if has_reject_severity:
        risk = 0.95
    return {"gray_judgments": [], "risk_score": risk}


def classify_gray_node(state: ReviewState) -> dict:
    logger.info("[5/8] classify_gray")
    if state.get("dry_run"):
        out = _rule_based_gray(state)
        logger.info("  => (dry-run) risk=%.2f", out["risk_score"])
        return out

    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []
    parsed = state.get("parsed_fields", {}) or {}
    cfg = state.get("cfg", {}) or {}

    # 完全クリーン（違反 0、重複 0）なら LLM をスキップして低リスク確定
    if not violations and not duplicates:
        logger.info("  => skip LLM (clean) risk=0.10")
        return {"gray_judgments": [], "risk_score": 0.10}

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    lite = state.get("lite_mode", False)
    model_key = "draft_decision_lite" if lite else "classify_gray"
    model = (cfg.get("models", {}) or {}).get(model_key, "claude-sonnet-4-6")

    system = (
        "あなたは中堅 SaaS 企業のベテラン経理担当（勤続 9 年）です。"
        "申請のグレーゾーンを判断し、リスクスコア (0.0〜1.0) と論点を JSON で返してください。"
        "単価超でも重要顧客との会食なら例外を認める、店名と科目の不整合は警戒、等の判断を行います。"
    )

    user = f"""以下の経費申請のグレーゾーンを判断してください。

## 申請内容
- カテゴリ: {parsed.get('category')}
- 金額: {parsed.get('amount_jpy'):,} 円
- 領収書日付: {parsed.get('receipt_date')}
- 店名: {parsed.get('store_name')}
- 相手先: {parsed.get('counterparty')}
- 参加人数: {parsed.get('participants_count')}
- 摘要: {parsed.get('description')}
- OCR: {parsed.get('ocr_text', '')[:300]}

## ハードルール検査結果
{json.dumps(violations, ensure_ascii=False, indent=2)}

## 過去 90 日の重複候補
{json.dumps(duplicates, ensure_ascii=False, indent=2) if duplicates else "なし"}

## 評価観点
1. 単価超や規程違反疑いに **正当な業務理由** がありうるか
2. **店名と科目の整合性**（高級店 × 会議費は怪しむ）
3. 重複候補がある場合、**正当な再申請**（毎月の定期接待等）か疑わしいか
4. 金額の丸め方が **不自然** か（10,000 / 5,000 円ぴったり等）

## 出力フォーマット (JSON のみ、説明文不要)
{{
  "gray_judgments": [
    {{"aspect": "<観点>", "verdict": "<allow|warn|deny>", "reasoning": "<50 文字以内>"}}
  ],
  "risk_score": <0.0〜1.0 の数値>
}}"""

    try:
        llm = ChatAnthropic(model=model, max_tokens=600)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON: {content[:200]}")
        data = json.loads(m.group(0))
        gj = list(data.get("gray_judgments", []) or [])
        risk = float(data.get("risk_score", 0.5))
        risk = max(0.0, min(1.0, risk))
        logger.info("  => gray_count=%d risk=%.2f", len(gj), risk)
        return {"gray_judgments": gj, "risk_score": risk}
    except Exception as e:
        logger.warning("classify_gray LLM failed: %s — fallback rule-based", e)
        return _rule_based_gray(state)
