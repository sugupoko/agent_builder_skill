"""ノード 3: classify — カテゴリ + 緊急度 + クレーム匂い検出。

キーワード辞書スキャン（決定論）→ LLM 二重判定（Sonnet）の二段構え。
クレーム匂いは再現率優先で kw OR LLM のどちらかが立ったら true にする。
"""
from __future__ import annotations

import json
import re

from .cost import accumulate_usage
from .logger import logger
from .state import TriageState

# dry-run 時のフォールバック分類（kw 辞書のみで判定）
_DEFAULT_CATEGORY = "other"


def keyword_scan(text: str, kw_dict: dict) -> dict:
    """各カテゴリのキーワード一致数を返す。"""
    text_lower = text.lower()
    result: dict = {}
    for cat, conf in (kw_dict or {}).items():
        kws = (conf or {}).get("keywords", [])
        hits = sum(1 for kw in kws if kw.lower() in text_lower)
        result[cat] = {"hits": hits, "matched": [kw for kw in kws if kw.lower() in text_lower]}
    return result


def _complaint_kw_hit(text: str, kw_list: list) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in kw_list or [])


def _determine_urgency(text: str, complaint: bool, cfg: dict) -> str:
    """v3: urgency_rules YAML に基づく決定論判定。"""
    rules = cfg.get("urgency_rules", {}) or {}
    text_lower = text.lower()
    if complaint and rules.get("high_complaint_smell", True):
        return "high"
    high_kws = rules.get("high_keywords", []) or []
    if any(kw.lower() in text_lower for kw in high_kws):
        return "high"
    low_kws = rules.get("low_keywords", []) or []
    if any(kw.lower() in text_lower for kw in low_kws):
        return "low"
    return "normal"


def _rule_based_classify(text: str, cfg: dict) -> dict:
    """LLM 不在時のフォールバック分類。

    v3: タイブレークを category_priority に従って解決、urgency も urgency_rules で決定。
    """
    cats = cfg.get("categories", {})
    scan = keyword_scan(text, cats)
    priority = cfg.get("category_priority", []) or list(cats.keys())

    # 一致数最大、同点は category_priority で解決
    def _sort_key(item):
        cat, info = item
        hits = info["hits"]
        prio_idx = priority.index(cat) if cat in priority else 999
        return (-hits, prio_idx)

    sorted_cats = sorted(scan.items(), key=_sort_key)
    if sorted_cats and sorted_cats[0][1]["hits"] > 0:
        category = sorted_cats[0][0]
    else:
        category = _DEFAULT_CATEGORY

    complaint = _complaint_kw_hit(text, cfg.get("complaint_keywords", []))
    if complaint:
        category = "complaint"   # v3: complaint 最優先

    urgency = _determine_urgency(text, complaint, cfg)
    return {
        "category": category,
        "urgency": urgency,
        "complaint_smell": complaint,
        "classify_confidence": 0.6 if scan else 0.3,
    }


def _llm_classify(text: str, cfg: dict, model: str) -> dict:
    """Sonnet による分類。失敗時は rule-based にフォールバック。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    cats = list((cfg.get("categories") or {}).keys())
    cats_str = ", ".join(cats) or "inventory, tech, alternative, shipment, cad, billing, complaint, other"
    priority = cfg.get("category_priority", []) or []
    priority_str = " > ".join(priority) if priority else "complaint > alternative > shipment > billing > tech > cad > inventory > other"
    urgency_rules = cfg.get("urgency_rules", {}) or {}
    high_kws = urgency_rules.get("high_keywords", [])
    low_kws = urgency_rules.get("low_keywords", [])

    system = (
        "あなたは中堅 B2B 部品商社のベテラン CS オペレータの分類専門アシスタントです。"
        "顧客メールを読み、定義済みカテゴリ・緊急度・クレーム匂いを JSON で返してください。"
    )
    user = f"""以下のメール本文を分類してください。

## カテゴリ候補
{cats_str}

## カテゴリ優先順位（境界が曖昧な場合のタイブレーク）
{priority_str}

→ 特にクレーム調 + 注文番号がある場合は、複合的な内容でも **complaint** を最優先する。

## 緊急度判定ルール
- high: complaint_smell が True、または以下のキーワードが含まれる場合
  - {", ".join(high_kws) if high_kws else "至急 / 今日中 / 製造ライン停止 等"}
- low: 以下の表現がある場合
  - {", ".join(low_kws) if low_kws else "お時間あるとき / いずれ"}
- normal: 上記以外（24 時間以内に応対希望）

## クレーム匂い検出
「困って」「至急」「何度目」「まだ」「結局」「前回も」などの表現、
冒頭が挨拶なくいきなり用件から始まる文体、感情語が複数含まれる場合は true。

## メール本文
\"\"\"
{text}
\"\"\"

## 出力フォーマット (JSON のみ、説明文不要)
{{
  "category": "<上記候補から1つ>",
  "urgency": "<low|normal|high>",
  "complaint_smell": <true|false>,
  "classify_confidence": <0.0-1.0>
}}"""

    try:
        llm = ChatAnthropic(model=model, max_tokens=200)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        accumulate_usage([resp], model)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON in LLM response: {content[:200]}")
        return json.loads(m.group(0))
    except Exception as e:
        logger.warning("classify LLM failed: %s — falling back to rule-based", e)
        return _rule_based_classify(text, cfg)


def classify_node(state: TriageState) -> dict:
    logger.info("[3/7] classify")
    cfg = state.get("cfg", {})
    text = state.get("masked_text", "")

    rule = _rule_based_classify(text, cfg)
    if state.get("dry_run"):
        logger.info("  => (dry-run) %s urgency=%s complaint=%s",
                    rule["category"], rule["urgency"], rule["complaint_smell"])
        return rule

    model = cfg.get("models", {}).get("classify", "claude-sonnet-4-6")
    llm_result = _llm_classify(text, cfg, model)

    # 再現率優先で kw OR LLM のどちらかが立ったら complaint=true
    final_complaint = rule["complaint_smell"] or bool(llm_result.get("complaint_smell"))
    final_category = llm_result.get("category", rule["category"])
    # v3: complaint_smell=True なら category=complaint を強制（業務 round-2 §A-4）
    if final_complaint:
        final_category = "complaint"
    # v3: urgency も決定論ルールで上書き可能性をチェック（high キーワード / complaint で high 強制）
    final_urgency = llm_result.get("urgency", rule["urgency"])
    rule_urgency = _determine_urgency(text, final_complaint, cfg)
    if rule_urgency == "high":
        final_urgency = "high"   # rule が high を指す場合は LLM 判定を上書き
    final = {
        "category": final_category,
        "urgency": final_urgency,
        "complaint_smell": final_complaint,
        "classify_confidence": float(llm_result.get("classify_confidence", 0.6)),
    }
    if final_complaint and final["category"] != "complaint" and rule["complaint_smell"]:
        # kw が「クレーム匂いだが他のカテゴリ的特徴も強い」場合は元カテゴリ温存
        # ただし complaint_smell=true は維持
        pass
    logger.info("  => %s urgency=%s complaint=%s conf=%.2f",
                final["category"], final["urgency"], final["complaint_smell"],
                final["classify_confidence"])
    return final
