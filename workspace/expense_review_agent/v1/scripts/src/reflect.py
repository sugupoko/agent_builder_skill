"""ノード 7: reflect — 自己レビュー。

検査:
1. needs_fix / reject なら suggested_fixes が 1 件以上ある
2. feedback_message に「申し訳」「断定否定」が含まれていない
3. 内部用語禁止辞書ヒットなし
4. ルール違反 message が feedback_message に何らかの形で言及されている

ループは最大 1 回。
"""
from __future__ import annotations

import json
import re

from .cost import accumulate_usage
from .logger import logger
from .state import ReviewState

_NG_PHRASES = ["絶対に承認できません", "ルール違反です", "規定違反です"]
_INTERNAL_PHRASES = ["経理判断", "上の判断", "上司に聞いて", "うちのシステム"]


def _rule_based_reflect(state: ReviewState) -> dict:
    decision = state.get("decision", "auto_approve")
    feedback = state.get("feedback_message", "") or ""
    suggested = state.get("suggested_fixes", []) or []
    violations = state.get("rule_violations", []) or []
    iter_n = (state.get("reflect_iter", 0) or 0) + 1

    issues: list = []

    if decision in ("needs_fix", "reject") and not suggested:
        issues.append("missing_suggested_fixes")

    for ng in _NG_PHRASES:
        if ng in feedback:
            issues.append(f"forbidden_assertive:{ng}")

    for internal in _INTERNAL_PHRASES:
        if internal in feedback:
            issues.append(f"forbidden_internal:{internal}")

    if decision == "auto_approve" and violations:
        # 違反があるのに auto_approve は致命
        issues.append("critical_auto_approve_with_violations")

    return {
        "reflect_pass": len(issues) == 0,
        "reflect_issues": issues,
        "reflect_iter": iter_n,
    }


def reflect_node(state: ReviewState) -> dict:
    logger.info("[7/8] reflect")
    rule_result = _rule_based_reflect(state)

    if state.get("dry_run") or rule_result["reflect_issues"]:
        # rule で issue があれば LLM スキップ（コスト節約 + 早期検知）
        logger.info("  => pass=%s issues=%s iter=%d",
                    rule_result["reflect_pass"], rule_result["reflect_issues"],
                    rule_result["reflect_iter"])
        return rule_result

    # rule OK なら LLM で文言の質をチェック
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    cfg = state.get("cfg", {}) or {}
    model = (cfg.get("models", {}) or {}).get("reflect", "claude-sonnet-4-6")

    system = ("あなたは経費申請レビュー結果の品質チェック係です。"
              "フィードバック文言が申請者にとって分かりやすく具体的か、敬体トーンが適切か、を 5 段階で評価します。")

    user = f"""以下の判定結果をレビューしてください。

## 判定
{state.get('decision')}

## ルール違反
{json.dumps(state.get('rule_violations', []), ensure_ascii=False, indent=2)}

## 申請者向け本文
\"\"\"
{state.get('feedback_message', '')}
\"\"\"

## 修正例
{json.dumps(state.get('suggested_fixes', []), ensure_ascii=False, indent=2)}

## 検査項目
1. 違反内容が本文で **具体的に説明** されているか
2. 修正例が **行動可能（具体的）** か
3. 敬体・建設的なトーン
4. 「ルール違反です」「絶対に承認できません」のような断定否定がないか
5. 内部用語が混入していないか

## 出力 (JSON のみ)
{{
  "reflect_pass": <true|false>,
  "reflect_issues": ["<問題があれば短く列挙>"]
}}"""

    try:
        llm = ChatAnthropic(model=model, max_tokens=400)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ValueError("no JSON")
        data = json.loads(m.group(0))
        out = {
            "reflect_pass": bool(data.get("reflect_pass", True)),
            "reflect_issues": list(data.get("reflect_issues", []) or []),
            "reflect_iter": rule_result["reflect_iter"],
        }
        logger.info("  => pass=%s issues=%s iter=%d", out["reflect_pass"], out["reflect_issues"], out["reflect_iter"])
        return out
    except Exception as e:
        logger.warning("reflect LLM failed: %s — using rule-based", e)
        return rule_result
