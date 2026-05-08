"""ノード 5: draft — 顧客向け本文 + 内部メモを生成する。

LLM (Sonnet / 軽量モードは Haiku) で起草する。プロンプトに以下を必ず含める:
    - editorial.persona / perspective / rules（YAML から）
    - 必須テンプレ語（greeting / closing）
    - クレーム時の apology_prefix
    - 数字捏造禁止（DB 引き当て値以外を書かない）
"""
from __future__ import annotations

import json

from .cost import accumulate_usage
from .logger import logger
from .state import TriageState


def _build_persona_block(cfg: dict) -> str:
    ed = cfg.get("editorial", {}) or {}
    return f"""## ペルソナ
{ed.get("persona", "")}

## 視点
{ed.get("perspective", "")}

## 執筆ルール
{ed.get("rules", "")}"""


def _build_retrieved_block(retrieved: dict) -> str:
    if not retrieved:
        return "（DB 引き当てなし）"
    return "```\n" + json.dumps(retrieved, ensure_ascii=False, indent=2) + "\n```"


def _build_template_constraints(cfg: dict, complaint: bool) -> str:
    tpl = cfg.get("templates", {}) or {}
    items = [
        f"- 冒頭に必ず: 「{tpl.get('greeting', 'お世話になっております。')}」",
        f"- 末尾に必ず: 「{tpl.get('closing', 'ご確認のほどよろしくお願いいたします。')}」",
        "- 「工場の都合」「うちのシステム」などの社内用語は禁止",
        "- DB 引き当て値以外の数字は書かない（推測値は禁止）",
    ]
    if complaint:
        items.append(f"- 冒頭の挨拶直後にお詫び: 「{tpl.get('apology_prefix', 'この度はご不便をおかけし、誠に申し訳ございません。')}」")
        items.append("- 内部メモに「スーパーバイザー引き継ぎ要」と明記")
    return "\n".join(items)


def _rule_based_draft(state: TriageState) -> dict:
    """dry-run / LLM 失敗時のフォールバック。テンプレ定型のみ。"""
    cfg = state.get("cfg", {})
    tpl = cfg.get("templates", {}) or {}
    cat = state.get("category", "other")
    complaint = state.get("complaint_smell", False)
    parts: list = []
    parts.append(tpl.get("greeting", "お世話になっております。"))
    if complaint:
        parts.append(tpl.get("apology_prefix", "この度はご不便をおかけし、誠に申し訳ございません。"))
    parts.append(f"お問い合わせの件（カテゴリ: {cat}）について、現在確認中です。")
    parts.append("詳細を確認のうえ、改めてご連絡いたします。")
    parts.append(tpl.get("closing", "ご確認のほどよろしくお願いいたします。"))
    body = "\n".join(parts)
    memo = "[dry-run] テンプレ定型応答。LLM 起草はスキップ。"
    if complaint:
        memo += " スーパーバイザー引き継ぎ要。"
    return {"draft_body": body, "internal_memo": memo, "missing_info": []}


def draft_node(state: TriageState) -> dict:
    logger.info("[5/7] draft")
    if state.get("dry_run"):
        out = _rule_based_draft(state)
        logger.info("  => (dry-run) draft len=%d", len(out["draft_body"]))
        return out

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    cfg = state.get("cfg", {})
    masked = state.get("masked_text", "")
    category = state.get("category", "other")
    urgency = state.get("urgency", "normal")
    complaint = state.get("complaint_smell", False)
    retrieved = state.get("retrieved_data", {})

    lite = state.get("lite_mode", False)
    model_key = "draft_lite" if lite else "draft"
    model = cfg.get("models", {}).get(model_key, "claude-sonnet-4-6")

    system = (
        "あなたは中堅 B2B 部品商社のベテラン CS オペレータです。"
        "顧客メールに対する回答ドラフトを丁寧な日本語で起草します。"
        "最終送信は別の人間オペレータが行うため、断定を避け不確実な点は素直に書きます。"
    )

    constraints = _build_template_constraints(cfg, complaint)
    persona_block = _build_persona_block(cfg)
    retrieved_block = _build_retrieved_block(retrieved)

    user = f"""{persona_block}

## 対象メール（PII マスク済み）
\"\"\"
{masked}
\"\"\"

## 分類結果
- カテゴリ: {category}
- 緊急度: {urgency}
- クレーム匂い: {complaint}

## DB 引き当て結果
{retrieved_block}

## 必須制約
{constraints}

## 出力フォーマット
JSON のみで返答してください（説明文や前置き禁止）:
{{
  "draft_body": "<顧客向け本文。Markdown は不要。プレーン日本語。>",
  "internal_memo": "<オペ向け内部メモ。使用 DB / 信頼度 / 引き継ぎ要否を 100 文字以内で>",
  "missing_info": ["<顧客に逆質問すべき情報があれば箇条書き、なければ空配列>"]
}}"""

    try:
        llm = ChatAnthropic(model=model, max_tokens=1200)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        # JSON 抽出
        import re
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON in draft response: {content[:200]}")
        data = json.loads(m.group(0))
        out = {
            "draft_body": str(data.get("draft_body", "")).strip(),
            "internal_memo": str(data.get("internal_memo", "")).strip(),
            "missing_info": list(data.get("missing_info", []) or []),
        }
        logger.info("  => draft len=%d memo=%s", len(out["draft_body"]), out["internal_memo"][:50])
        return out
    except Exception as e:
        logger.warning("draft LLM failed: %s — falling back to template", e)
        return _rule_based_draft(state)
