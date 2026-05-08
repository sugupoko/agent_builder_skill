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


def _build_db_value_policy_block(cfg: dict, category: str) -> str:
    """v3: カテゴリ別 DB 値記載ポリシーを文言として注入。"""
    pol = (cfg.get("db_value_policies") or {}).get(category, {})
    if not pol:
        return ""
    lines = ["- DB 値の記載ポリシー（カテゴリ別）:"]
    label = {
        "level_only": "レベル感（充足/部分/欠品）で記載、正確な数値は書かない",
        "number_with_unit": "数値+単位（例: 14 営業日）で必ず明記",
        "tax_excluded_number": "税抜単価を数値で明記（取引先別価格は customer_id があれば優先）",
        "date_iso": "YYYY-MM-DD 形式で明示",
        "url_with_carrier": "URL + 配送業者名のみ記載。追跡番号は internal_memo へ",
        "disclose": "理由を本文に開示する（隠さない）",
        "with_reason": "後継品 SKU + 理由（材質変更・寸法互換等）を併記",
        "internal_memo_only": "本文には書かず internal_memo にのみ記載",
    }
    for field, rule in pol.items():
        lines.append(f"  - {field}: {label.get(rule, rule)}")
    return "\n".join(lines)


def _build_template_constraints(cfg: dict, complaint: bool, category: str) -> str:
    """v3: カテゴリ別 DB 値ポリシー / apology 範囲 / 内部用語禁止を明示。"""
    tpl = cfg.get("templates", {}) or {}
    apology_cfg = cfg.get("apology", {}) or {}
    forbidden_phrases = cfg.get("forbidden_internal_phrases", []) or []
    forbidden_apology = apology_cfg.get("forbidden_when_no_complaint", []) or []
    allowed_apology = apology_cfg.get("allowed", []) or []

    items = [
        f"- 冒頭に必ず: 「{tpl.get('greeting', 'お世話になっております。')}」",
        f"- 末尾に必ず: 「{tpl.get('closing', 'ご確認のほどよろしくお願いいたします。')}」",
        f"- 内部用語禁止（reflect ハードチェック対象）: {', '.join(forbidden_phrases)}",
        "- 推測値は禁止（DB 引き当て値以外の数字を本文に書かない）",
    ]

    db_pol = _build_db_value_policy_block(cfg, category)
    if db_pol:
        items.append(db_pol)

    if complaint:
        items.append(f"- 冒頭の挨拶直後にお詫び: 「{tpl.get('apology_prefix', 'この度はご不便をおかけし、誠に申し訳ございません。')}」")
        items.append("- 内部メモに「スーパーバイザー引き継ぎ要」と明記")
    else:
        # v3: 禁止語と許容語を分離（共感表現は許容）
        forb_str = "/".join(forbidden_apology) if forbidden_apology else "申し訳/お詫び/ご迷惑"
        allow_str = "/".join(allowed_apology) if allowed_apology else "お手数/ご不便/ご心配"
        items.append(f"- **complaint_smell=False のため、お詫び語（{forb_str}）は禁止**。共感表現（{allow_str}）は使ってよい")

    if category == "cad":
        items.append("- **CAD URL は本文に書かず internal_memo のみ**。本文は「CAD データは担当者より別途ご案内いたします」")
    if category == "tech":
        items.append("- 技術質問のため、使用条件（温度・薬品環境・荷重・精度グレード等）から該当する 2〜3 項目を逆質問に含める")

    # v3: 本文中に PII placeholder（[NAME_001] など）が含まれる場合は **そのまま残す**
    # （unmask は assemble の最終段でのみ。LLM Judge 採点でも placeholder 状態で評価）
    items.append("- **本文中の `[NAME_001]` `[PHONE_001]` 等のプレースホルダはそのまま残す**（人名や電話番号を生で書かない、置換しない）")

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

    constraints = _build_template_constraints(cfg, complaint, category)
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
