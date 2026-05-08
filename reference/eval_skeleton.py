"""評価駆動開発の雛形。

eval/dataset/ の各ケースに対してエージェントを実行し、
コードベース評価 + LLM as a judge の結果を集計する。

詳細は reference/eval_driven_dev.md を参照。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# 1. コードベース評価
# ---------------------------------------------------------------------------
def evaluate_codebase(output: str, metadata: dict) -> dict:
    """期待値に対する出力の機械的評価。

    metadata 例:
        expected:
          must_include_entities: [A社, 決算]
          must_not_include_entities: [無関係なゲーム会社]
          min_citations: 5
          min_action_items: 3
          max_cost_usd: 0.50
    """
    expected = metadata.get("expected", {})
    score = {}

    must_include = expected.get("must_include_entities", []) or []
    if must_include:
        hits = sum(1 for k in must_include if k in output)
        score["must_include_rate"] = hits / len(must_include)
    else:
        score["must_include_rate"] = 1.0

    must_not = expected.get("must_not_include_entities", []) or []
    score["must_not_violations"] = sum(1 for k in must_not if k in output)

    score["citation_count"] = len(set(re.findall(r"\[(\d+)\]", output)))
    score["action_count"] = len(re.findall(
        r"\*\*(?:今週中|来週まで|来月まで)\*\*", output
    ))

    score["passed"] = (
        score["must_include_rate"] >= 0.8
        and score["must_not_violations"] == 0
        and score["citation_count"] >= expected.get("min_citations", 0)
        and score["action_count"] >= expected.get("min_action_items", 0)
    )
    return score


# ---------------------------------------------------------------------------
# 2. LLM as a judge
# ---------------------------------------------------------------------------
JUDGE_PROMPT = """次のレポートを編集者の視点から評価してください。

評価軸（各 1-5 で採点）:
1. specificity (具体性): 数字・固有名詞・日付が含まれているか
2. uniqueness (視点の独自性): 「業界が活況」のような汎用表現がないか
3. actionability (アクション可能性): 読者がすぐ動ける内容か
4. consistency (整合性): 出典と本文が矛盾していないか

各軸を 1-5 で採点し、根拠とともに JSON で返してください。

レポート:
---
{report}
---

出力フォーマット:
```json
{{"specificity": 4, "uniqueness": 3, "actionability": 5, "consistency": 4, "comments": ["..."]}}
```

JSONのみ返してください。前置き・後付けは禁止。"""


def evaluate_with_llm_judge(report: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    """別の Claude にレポートを評価させる。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage

    judge = ChatAnthropic(model=model, max_tokens=500, temperature=0)
    resp = judge.invoke([HumanMessage(content=JUDGE_PROMPT.format(report=report))])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    # ```json ... ``` のフェンスを取り除く
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    payload = m.group(1) if m else text
    try:
        parsed = json.loads(payload)
    except Exception:
        parsed = {"error": "json parse failed", "raw": text[:200]}
    return parsed


# ---------------------------------------------------------------------------
# 3. ケース実行
# ---------------------------------------------------------------------------
def run_case(case_dir: Path, agent_runner) -> dict:
    """ケース1件を実行して評価。

    agent_runner: callable(input_data) -> {"output": str, "cost_usd": float, "duration": float}
    """
    metadata = yaml.safe_load((case_dir / "metadata.yaml").read_text(encoding="utf-8"))

    # input は input.json or input.md
    input_data = None
    for ext in ("json", "md", "yaml"):
        p = case_dir / f"input.{ext}"
        if p.exists():
            text = p.read_text(encoding="utf-8")
            input_data = json.loads(text) if ext == "json" else text
            break

    start = time.time()
    result = agent_runner(input_data)
    duration = time.time() - start

    output = result.get("output", "")
    cost = result.get("cost_usd", 0.0)

    score_code = evaluate_codebase(output, metadata)
    # LLM judge は重いので任意
    score_llm = None
    if metadata.get("eval_with_llm_judge", False):
        score_llm = evaluate_with_llm_judge(output)

    return {
        "case": case_dir.name,
        "metadata": metadata,
        "output_excerpt": output[:500],
        "scores": {
            "codebase": score_code,
            "llm_judge": score_llm,
            "cost_usd": cost,
            "duration_sec": duration,
        },
    }


# ---------------------------------------------------------------------------
# 4. 集計
# ---------------------------------------------------------------------------
def aggregate(results: list) -> dict:
    if not results:
        return {}
    n = len(results)
    return {
        "n_cases": n,
        "passed": sum(1 for r in results if r["scores"]["codebase"]["passed"]),
        "avg_must_include_rate": sum(
            r["scores"]["codebase"].get("must_include_rate", 0) for r in results
        ) / n,
        "avg_citation_count": sum(
            r["scores"]["codebase"].get("citation_count", 0) for r in results
        ) / n,
        "avg_action_count": sum(
            r["scores"]["codebase"].get("action_count", 0) for r in results
        ) / n,
        "avg_cost_usd": sum(r["scores"]["cost_usd"] for r in results) / n,
        "avg_duration_sec": sum(r["scores"]["duration_sec"] for r in results) / n,
    }


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--out-dir", default="eval/results", type=Path)
    ap.add_argument("--version", default="v1")
    args = ap.parse_args()

    # TODO: 自分の agent_runner を import する
    def agent_runner(input_data):
        # 本物の実装では scripts/agent.py を呼んで output / cost を返す
        return {"output": "(stub)", "cost_usd": 0.0}

    cases = sorted([p for p in args.dataset.iterdir() if p.is_dir()])
    results = [run_case(c, agent_runner) for c in cases]
    summary = aggregate(results)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = args.out_dir / f"{args.version}_{ts}.json"
    out_path.write_text(json.dumps({
        "version": args.version,
        "timestamp": ts,
        "summary": summary,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote: {out_path}")
    print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
