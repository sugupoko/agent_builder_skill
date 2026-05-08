"""マルチエージェント協調の雛形（Planner + Worker + Reviewer）。

Sakana AI ShinkaEvolve が発見した
「diverse expert personas + critical peer review + final synthesis」の3段構成を踏襲。

詳細は reference/design_patterns.md および .claude/skills/agent-evolve/SKILL.md を参照。

注意:
    - コストが2〜5倍になる
    - デバッグ困難
    - 単純なワークフロー型 + ReAct で動かしてから検討する
"""
from __future__ import annotations

import logging
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# Personas（各 Worker に異なるシステムプロンプトを渡す）
# ---------------------------------------------------------------------------
WORKER_PERSONAS = {
    "industry_analyst": (
        "あなたは20年経験の業界アナリストです。市場規模・競合関係・経営判断の視点で読み解きます。"
    ),
    "tech_engineer": (
        "あなたはシニアエンジニアです。実装可能性・運用上の課題・技術的リスクの視点で読み解きます。"
    ),
    "regulator": (
        "あなたは規制・コンプライアンスの専門家です。法的論点・リスク・倫理面の視点で読み解きます。"
    ),
}


# ---------------------------------------------------------------------------
# 1. Planner — タスクを分解
# ---------------------------------------------------------------------------
def planner(task: str, model: str = "claude-sonnet-4-6") -> list[dict]:
    """タスクを Worker 用のサブタスクに分解する。

    Returns: [{"subtask": str, "persona": str}, ...]
    """
    llm = ChatAnthropic(model=model, max_tokens=1500)
    prompt = f"""次のタスクを、3名の専門家（業界アナリスト / 技術エンジニア / 規制専門家）が
それぞれ独立に検討するためのサブタスクに分解してください。

タスク:
{task}

出力フォーマット (JSON):
[
  {{"subtask": "...", "persona": "industry_analyst"}},
  {{"subtask": "...", "persona": "tech_engineer"}},
  {{"subtask": "...", "persona": "regulator"}}
]

JSONのみ返す。前置き禁止。"""

    resp = llm.invoke([
        SystemMessage(content="あなたはタスク分解の専門家です。"),
        HumanMessage(content=prompt),
    ])
    import json, re
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        logger.exception("Planner JSON parse error")
        return []


# ---------------------------------------------------------------------------
# 2. Worker — 各サブタスクを別ペルソナで処理
# ---------------------------------------------------------------------------
def worker(subtask: str, persona: str, context: str = "",
           model: str = "claude-sonnet-4-6") -> str:
    """指定されたペルソナでサブタスクに取り組む。"""
    llm = ChatAnthropic(model=model, max_tokens=2000)
    persona_text = WORKER_PERSONAS.get(persona, "あなたは汎用アナリストです。")

    resp = llm.invoke([
        SystemMessage(content=persona_text),
        HumanMessage(content=f"""次のサブタスクに取り組んでください。

サブタスク:
{subtask}

参考情報:
{context}

Markdownで出力してください。前置き・後付けは禁止。"""),
    ])
    return resp.content if isinstance(resp.content, str) else str(resp.content)


# ---------------------------------------------------------------------------
# 3. Reviewer — 各 Worker の出力を批判的にレビュー
# ---------------------------------------------------------------------------
def reviewer(drafts: list[dict], model: str = "claude-haiku-4-5-20251001") -> list[dict]:
    """各 Worker の出力に対して批判的レビューと改善指示を返す。

    drafts: [{"persona": str, "output": str}, ...]
    Returns: [{"persona": str, "critique": str, "fix_suggestions": [str]}, ...]
    """
    llm = ChatAnthropic(model=model, max_tokens=1500)
    reviews = []
    for d in drafts:
        prompt = f"""以下の {d['persona']} の出力を批判的にレビューしてください。

出力:
{d['output']}

評価軸:
- 具体性（数字・固有名詞）
- 論理整合性
- 漏れている観点

JSONで返す。
{{"critique": "全体評価1〜2文", "fix_suggestions": ["改善案1", "改善案2"]}}"""
        resp = llm.invoke([
            SystemMessage(content="あなたは厳格なレビュアーです。"),
            HumanMessage(content=prompt),
        ])
        import json, re
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\{[\s\S]*\}", text)
        try:
            parsed = json.loads(m.group(0)) if m else {"critique": text, "fix_suggestions": []}
        except Exception:
            parsed = {"critique": text, "fix_suggestions": []}
        parsed["persona"] = d["persona"]
        reviews.append(parsed)
    return reviews


# ---------------------------------------------------------------------------
# 4. Synthesizer — Worker 出力 + Reviewer フィードバックを統合
# ---------------------------------------------------------------------------
def synthesizer(drafts: list[dict], reviews: list[dict],
                model: str = "claude-sonnet-4-6") -> str:
    """3名の出力を最終レポートに統合。Reviewer の指摘を反映。"""
    llm = ChatAnthropic(model=model, max_tokens=3000)

    drafts_text = "\n\n".join(
        f"## {d['persona']}\n{d['output']}" for d in drafts
    )
    reviews_text = "\n\n".join(
        f"## {r['persona']} へのレビュー\n{r.get('critique', '')}\n改善案:\n- " +
        "\n- ".join(r.get("fix_suggestions", []))
        for r in reviews
    )
    prompt = f"""次の3名の専門家の出力と、それぞれへのレビューを統合し、最終レポートを書いてください。

各専門家の出力:
{drafts_text}

レビュー:
{reviews_text}

統合の方針:
- 各視点の強みを残す
- 重複を排除
- レビューの指摘を反映
- 矛盾は調整して明示

最終出力は Markdown で。前置き・後付けは禁止。"""

    resp = llm.invoke([
        SystemMessage(content="あなたは編集長として最終統合を担当します。"),
        HumanMessage(content=prompt),
    ])
    return resp.content if isinstance(resp.content, str) else str(resp.content)


# ---------------------------------------------------------------------------
# 5. Pipeline
# ---------------------------------------------------------------------------
def run_multi_agent(task: str, context: str = "") -> dict:
    """Planner → Workers → Reviewer → Synthesizer のパイプライン全体を実行。"""
    logger.info("Multi-Agent: Planner")
    plan = planner(task)
    if not plan:
        return {"final": "(タスク分解に失敗)", "drafts": [], "reviews": []}

    logger.info("Multi-Agent: %d Workers", len(plan))
    drafts = []
    for item in plan:
        out = worker(item["subtask"], item["persona"], context)
        drafts.append({"persona": item["persona"], "output": out})

    logger.info("Multi-Agent: Reviewer")
    reviews = reviewer(drafts)

    logger.info("Multi-Agent: Synthesizer")
    final = synthesizer(drafts, reviews)

    return {"final": final, "drafts": drafts, "reviews": reviews}


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY が未設定", flush=True)
        exit(1)
    logging.basicConfig(level=logging.INFO)
    result = run_multi_agent(
        task="○○業界の今週の動向をレポートしてください。",
        context="（ここに収集済みの記事一覧を入れる）",
    )
    print("=== FINAL ===")
    print(result["final"])
