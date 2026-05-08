"""LLM as a judge — フィードバック品質を 5 次元採点。

次元:
- decision_appropriateness: 判定（auto_approve/needs_fix/needs_review/reject）が状況に対し妥当か
- feedback_specificity: 申請者向け文言が具体的か（修正例・違反内容の説明）
- tone_appropriate: 敬体・建設的、断定否定なし
- rule_traceability: rule_trace が完全で監査に耐えるか
- numerical_accuracy: 金額・期限・人数の数値が正しく扱われているか
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
RATE_INPUT = 3.00 / 1_000_000
RATE_OUTPUT = 15.00 / 1_000_000

DIMENSIONS = (
    "decision_appropriateness",
    "feedback_specificity",
    "tone_appropriate",
    "rule_traceability",
    "numerical_accuracy",
)

JUDGE_SYSTEM = (
    "あなたは経費申請レビュー結果の品質を評価する評価者です。"
    "中堅 SaaS 企業のベテラン経理担当の視点で、5 次元で 1-5 採点してください。"
    "スコアは厳しめに、3 を平均、5 を例外的優秀、1 を即修正、で評価。"
)

JUDGE_USER_TMPL = """\
== 業務ポリシー（編集者ペルソナ）==
あなたはベテラン経理。金額の中途半端さ、店名と科目の整合性、重複・グレーの嗅覚を持つ。
申請者には敬体で具体的にフィードバックし、修正例を必ず添える。
needs_review 時は「経理担当より追って連絡」とし、申請者を待たせない。

== 申請内容 ==
{input_text}

== 判定結果（採点対象、フル本文）==
- decision: {decision}
- 違反ルール:
{violations}
- 重複候補: {duplicates}
- フィードバック本文:
\"\"\"
{feedback}
\"\"\"
- 修正例: {suggested_fixes}
- 内部メモ: {internal_memo}
- rule_trace（監査用）:
{rule_trace}

== 評価 ==
以下 5 次元を 1-5 で採点し JSON で返してください:
- decision_appropriateness: 判定（auto_approve/needs_fix/needs_review/reject）がこの申請の状況に対し妥当か
- feedback_specificity: 申請者向け本文が具体的か（違反内容の説明 + 何をすればよいか）
- tone_appropriate: 敬体・建設的、断定否定なし
- rule_traceability: rule_trace が完全で監査に耐える内容か
- numerical_accuracy: 金額・期限・人数等の数値の扱いが正しいか

JSON 形式（前置き禁止、JSON 1 個）:
{{"decision_appropriateness": <1-5>, "feedback_specificity": <1-5>, "tone_appropriate": <1-5>, "rule_traceability": <1-5>, "numerical_accuracy": <1-5>, "comment": "<100 文字以内の総評>"}}
"""


def _parse_score(content: str) -> dict | None:
    if not content:
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def judge_one(case_row: dict, llm) -> dict:
    case_id = case_row["case_id"]
    run = case_row.get("run", {}) or {}
    final = run.get("final_output", {}) or {}

    feedback = final.get("feedback_message", "") or run.get("feedback_message", "")
    decision = final.get("decision", "?")
    violations = run.get("rule_violations", []) or []
    duplicates = run.get("duplicate_candidates", []) or []
    rule_trace = final.get("rule_trace", []) or []
    suggested = final.get("suggested_fixes", []) or []
    memo = final.get("internal_memo", "")
    input_text = case_row.get("input_text", "") or "(N/A)"

    user = JUDGE_USER_TMPL.format(
        input_text=input_text[:1500],
        decision=decision,
        violations=json.dumps(violations, ensure_ascii=False, indent=2),
        duplicates=json.dumps(duplicates, ensure_ascii=False, indent=2) if duplicates else "なし",
        feedback=feedback,
        suggested_fixes=json.dumps(suggested, ensure_ascii=False),
        internal_memo=memo,
        rule_trace=json.dumps(rule_trace, ensure_ascii=False, indent=2),
    )

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    t0 = time.time()
    try:
        resp = llm.invoke([SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=user)])
        elapsed = time.time() - t0
        meta = getattr(resp, "usage_metadata", {}) or {}
        in_t = int(meta.get("input_tokens", 0) or 0)
        out_t = int(meta.get("output_tokens", 0) or 0)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        parsed = _parse_score(content)
        if not parsed:
            return {"case_id": case_id, "scores": {k: 0 for k in DIMENSIONS},
                    "comment": f"JSON parse failed: {content[:120]}",
                    "elapsed_s": round(elapsed, 2), "input_tokens": in_t, "output_tokens": out_t}
        scores = {}
        for k in DIMENSIONS:
            try:
                scores[k] = max(0, min(5, int(parsed.get(k, 0))))
            except (TypeError, ValueError):
                scores[k] = 0
        return {"case_id": case_id, "scores": scores,
                "comment": str(parsed.get("comment", ""))[:300],
                "elapsed_s": round(elapsed, 2),
                "input_tokens": in_t, "output_tokens": out_t}
    except Exception as e:
        return {"case_id": case_id, "scores": {k: 0 for k in DIMENSIONS},
                "comment": f"ERROR: {e}", "elapsed_s": round(time.time() - t0, 2),
                "input_tokens": 0, "output_tokens": 0}


def aggregate(judgments: list) -> dict:
    n = len(judgments)
    if not n:
        return {}
    sums = {k: 0 for k in DIMENSIONS}
    for j in judgments:
        for k in DIMENSIONS:
            sums[k] += j["scores"].get(k, 0)
    avgs = {f"avg_{k}": round(sums[k] / n, 2) for k in DIMENSIONS}
    overall = round(sum(sums.values()) / (n * len(DIMENSIONS)), 2)
    in_total = sum(j.get("input_tokens", 0) for j in judgments)
    out_total = sum(j.get("output_tokens", 0) for j in judgments)
    cost = in_total * RATE_INPUT + out_total * RATE_OUTPUT
    low = []
    for j in judgments:
        avg = sum(j["scores"].values()) / len(j["scores"]) if j["scores"] else 0
        if avg < 3.5:
            low.append({"case_id": j["case_id"], "avg": round(avg, 2), "comment": j.get("comment", "")})
    return {"n_cases": n, **avgs, "overall_avg": overall,
            "low_score_cases": low,
            "judge_input_tokens": in_total, "judge_output_tokens": out_total,
            "judge_cost_usd": round(cost, 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-dir", type=Path, required=True)
    ap.add_argument("--dataset", type=Path,
                    default=Path(__file__).resolve().parent.parent / "eval" / "dataset")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--only", nargs="+", default=None)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    payload = json.loads((args.result_dir / "result.json").read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c["case_id"] in wanted]

    for c in cases:
        p = args.dataset / c["case_id"] / "input.json"
        if p.exists():
            c["input_text"] = p.read_text(encoding="utf-8")

    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model=args.judge_model, max_tokens=600)

    judgments = []
    for c in cases:
        j = judge_one(c, llm)
        avg = sum(j["scores"].values()) / len(j["scores"]) if j["scores"] else 0
        print(f"  {j['case_id']}: scores={j['scores']} avg={avg:.2f} ({j['elapsed_s']:.1f}s)")
        judgments.append(j)

    summary = aggregate(judgments)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = args.result_dir / f"judge_{ts}.json"
    out.write_text(json.dumps({
        "timestamp": ts, "judge_model": args.judge_model,
        "summary": summary, "cases": judgments,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {out}")
    print(f"[done] overall: {summary.get('overall_avg', 0)} / 5")
    print(f"[done] cost: ${summary.get('judge_cost_usd', 0):.4f}")


if __name__ == "__main__":
    main()
