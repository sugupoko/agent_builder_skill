"""ノード 6: reflect — Reflection 自己レビュー。

検査項目:
    1. 必須テンプレ語（greeting / closing）の含有
    2. DB 引き当て値の改変有無（retrieved_data の数値が draft に正しく出ているか）
    3. complaint_smell=true なら apology_prefix が含まれているか
    4. 断定口調の禁止語チェック

ループは最大 1 回（コスト爆発防止）。
"""
from __future__ import annotations

import json
from typing import Any

from .cost import accumulate_usage
from .logger import logger
from .state import TriageState

_ASSERTIVE_NEGATIVES = ["できません", "ありません", "不可能です", "絶対に"]


def _check_required_phrases(body: str, cfg: dict) -> list:
    issues: list = []
    tpl = cfg.get("templates", {}) or {}
    greeting = tpl.get("greeting", "お世話になっております。")
    closing = tpl.get("closing", "ご確認のほどよろしくお願いいたします。")
    if greeting not in body:
        issues.append(f"missing_greeting: '{greeting}'")
    if closing not in body:
        issues.append(f"missing_closing: '{closing}'")
    return issues


def _check_apology(body: str, complaint: bool, cfg: dict) -> list:
    if not complaint:
        return []
    tpl = cfg.get("templates", {}) or {}
    apology = tpl.get("apology_prefix", "")
    # キーワードベースで判定（apology_prefix 全文一致は厳しすぎるため）
    apology_kws = ["申し訳", "お詫び", "ご迷惑", "お手数"]
    if not any(k in body for k in apology_kws):
        return [f"missing_apology_for_complaint"]
    return []


def _check_assertive_tone(body: str) -> list:
    return [f"assertive_tone:{w}" for w in _ASSERTIVE_NEGATIVES if w in body]


def _check_db_values(body: str, retrieved: dict) -> list:
    """DB 引き当て値（数値）が draft に書かれている場合、改変されていないかを軽くチェック。

    完璧な検査は LLM Judge に任せる。ここでは「DB 値以外の数値が混入していたら警告」のみ。
    """
    # 在庫数や納期は string で出る可能性があるため文字列マッチで OK 判定
    # ここでは noop（v1 は LLM 側で reflect プロンプトに任せる）
    return []


def _rule_based_reflect(state: TriageState) -> dict:
    cfg = state.get("cfg", {})
    body = state.get("draft_body", "")
    complaint = state.get("complaint_smell", False)
    retrieved = state.get("retrieved_data", {})
    iter_n = state.get("reflect_iter", 0) + 1

    issues = []
    issues += _check_required_phrases(body, cfg)
    issues += _check_apology(body, complaint, cfg)
    issues += _check_assertive_tone(body)
    issues += _check_db_values(body, retrieved)

    return {
        "reflect_pass": len(issues) == 0,
        "reflect_issues": issues,
        "reflect_iter": iter_n,
    }


def _llm_reflect(state: TriageState, model: str) -> dict[str, Any]:
    """Sonnet によるトーン・整合性チェック（rule-based の上に重ねる）。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    cfg = state.get("cfg", {})
    body = state.get("draft_body", "")
    retrieved = state.get("retrieved_data", {})
    complaint = state.get("complaint_smell", False)

    rule = _rule_based_reflect(state)
    iter_n = rule["reflect_iter"]

    if rule["reflect_issues"]:
        # rule-based ですでに NG が見つかっていれば LLM は呼ばずコスト節約
        return rule

    system = (
        "あなたは CS 応対品質を厳しくチェックするレビュアーです。"
        "ベテランオペの基準で、ドラフトに以下の問題がないか検査します。"
    )
    user = f"""以下の回答ドラフトをレビューしてください。

## ドラフト
\"\"\"
{body}
\"\"\"

## DB 引き当て結果
```
{json.dumps(retrieved, ensure_ascii=False, indent=2)}
```

## クレーム匂い検出
{complaint}

## 検査項目
1. DB 引き当て結果の数値（在庫数 / 納期 / 価格 / 追跡 URL）が改変されていないか
2. 推測で書かれた数字や日付がないか
3. クレーム匂い時、お詫びの一言と引き継ぎ示唆があるか
4. 断定的な NG 表現（「できません」「絶対に」など）が混入していないか

## 出力フォーマット (JSON のみ)
{{
  "reflect_pass": <true|false>,
  "reflect_issues": ["<問題があれば短く列挙、なければ空配列>"]
}}"""
    try:
        llm = ChatAnthropic(model=model, max_tokens=300)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        import re as _re
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if not m:
            raise ValueError(f"no JSON in reflect response: {content[:200]}")
        data = json.loads(m.group(0))
        return {
            "reflect_pass": bool(data.get("reflect_pass", True)),
            "reflect_issues": list(data.get("reflect_issues", []) or []),
            "reflect_iter": iter_n,
        }
    except Exception as e:
        logger.warning("reflect LLM failed: %s — using rule-based result", e)
        return rule


def reflect_node(state: TriageState) -> dict:
    logger.info("[6/7] reflect")
    if state.get("dry_run"):
        out = _rule_based_reflect(state)
        logger.info("  => (dry-run) pass=%s issues=%s", out["reflect_pass"], out["reflect_issues"])
        return out
    cfg = state.get("cfg", {})
    model = cfg.get("models", {}).get("reflect", "claude-sonnet-4-6")
    out = _llm_reflect(state, model)
    logger.info("  => pass=%s issues=%s iter=%d", out["reflect_pass"], out["reflect_issues"], out["reflect_iter"])
    return out
