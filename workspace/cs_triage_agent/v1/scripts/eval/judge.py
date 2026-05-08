"""LLM as a judge — run_eval.py の結果（cases/<id>/final.json）に対して 5 次元採点。

設計の核 (`reference/llm_judge_skeleton.py` から):
    - judge には full body + DB 結果 + ペルソナ・ルールを完全に渡す（切り詰め禁止）
    - 5 次元: persona_fit / tone_appropriate / info_completeness /
              numerical_accuracy / ng_phrase_avoidance
    - judge model は Sonnet 推奨（Haiku は判定が甘い）

使い方:
    python scripts/eval/judge.py --result-dir eval/results/run_2026-05-08_xxx_real
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

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

# Sonnet 4.6 公開レート
RATE_INPUT = 3.00 / 1_000_000
RATE_OUTPUT = 15.00 / 1_000_000

DIMENSIONS = (
    "persona_fit",
    "tone_appropriate",
    "info_completeness",
    "numerical_accuracy",
    "ng_phrase_avoidance",
)

JUDGE_SYSTEM = (
    "あなたは AI エージェントが生成した CS 応対ドラフトの品質を評価する評価者です。"
    "編集者ペルソナの視点で、5 次元で 1-5 で採点してください。"
    "スコアは厳しめにつけ、3 を平均、5 を例外的に優秀、1 を即修正必要、とする標準で評価。"
)

JUDGE_USER_TMPL = """\
== 編集者ペルソナ ==
{persona}

== 編集者の視点 ==
{perspective}

== 執筆ルール ==
{rules}

== 入力情報 ==
- 元メール本文（PII マスク済み）:
{input_text}

- カテゴリ: {category}
- 緊急度: {urgency}
- クレーム匂い検出: {complaint_smell}

== DB ルックアップ結果 ==
{db_block}

== AI 生成ドラフト（採点対象、フル本文） ==
{body}

== 内部メモ ==
{memo}

== 評価 ==
以下 5 次元を 1-5 で採点し、JSON で返してください:
- persona_fit: ベテランオペのペルソナ（型番から仕様を読む / 逆質問 / クレーム察知）が出ているか
- tone_appropriate: 敬体・断定回避・社内用語禁止が守られているか
- info_completeness: DB ルックアップ結果（在庫・納期・追跡 URL・後継品 等）を正しく漏れなく使っているか。過剰に推測していないか
- numerical_accuracy: ドラフト中の数値（在庫数・納期日数・価格・追跡番号）が DB 結果と一致しているか。捏造があれば即 1
- ng_phrase_avoidance: NG 表現（断定の「できません」「絶対に」、社内用語「工場の都合」等）を直接使わず汎化しているか

JSON 形式（前置き禁止、JSON 1 個のみ）:
{{"persona_fit": <int 1-5>, "tone_appropriate": <int 1-5>, "info_completeness": <int 1-5>, "numerical_accuracy": <int 1-5>, "ng_phrase_avoidance": <int 1-5>, "comment": "<100 文字以内の総評。何が良く何が悪いか>"}}
"""


def _parse_score_json(content: str) -> dict | None:
    if not content:
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def judge_one(case_row: dict, editorial: dict, llm) -> dict:
    """1 ケースを採点。case_row は run_eval の cases[*] と同じ構造。"""
    case_id = case_row["case_id"]
    run = case_row.get("run", {}) or {}
    final = run.get("final_output", {}) or {}
    meta_input = final.get("meta", {}) or {}

    body = final.get("customer_body", "") or run.get("draft_body", "")
    memo = final.get("internal_memo", "")
    input_text = case_row.get("input_text", "") or "(N/A)"
    category = meta_input.get("category", run.get("category", "?"))
    urgency = meta_input.get("urgency", run.get("urgency", "?"))
    complaint = meta_input.get("complaint_smell", run.get("complaint_smell", False))
    db = run.get("retrieved_data", {}) or {}

    db_block = "（DB 照会対象外）" if not db else f"```json\n{json.dumps(db, ensure_ascii=False, indent=2)}\n```"

    user = JUDGE_USER_TMPL.format(
        persona=(editorial.get("persona") or "").strip(),
        perspective=(editorial.get("perspective") or "").strip(),
        rules=(editorial.get("rules") or "").strip(),
        input_text=input_text.strip(),
        category=category,
        urgency=urgency,
        complaint_smell=complaint,
        db_block=db_block,
        body=body,
        memo=memo,
    )

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    t0 = time.time()
    try:
        resp = llm.invoke([SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=user)])
        elapsed = time.time() - t0
        meta_usage = getattr(resp, "usage_metadata", {}) or {}
        in_t = int(meta_usage.get("input_tokens", 0) or 0)
        out_t = int(meta_usage.get("output_tokens", 0) or 0)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        parsed = _parse_score_json(content)
        if not parsed:
            return {
                "case_id": case_id,
                "scores": {k: 0 for k in DIMENSIONS},
                "comment": f"JSON parse failed: {content[:120]}",
                "elapsed_s": round(elapsed, 2),
                "input_tokens": in_t, "output_tokens": out_t,
            }
        scores: dict = {}
        for k in DIMENSIONS:
            try:
                scores[k] = max(0, min(5, int(parsed.get(k, 0))))
            except (TypeError, ValueError):
                scores[k] = 0
        return {
            "case_id": case_id,
            "scores": scores,
            "comment": str(parsed.get("comment", ""))[:300],
            "elapsed_s": round(elapsed, 2),
            "input_tokens": in_t, "output_tokens": out_t,
        }
    except Exception as e:
        return {
            "case_id": case_id,
            "scores": {k: 0 for k in DIMENSIONS},
            "comment": f"ERROR: {e}",
            "elapsed_s": round(time.time() - t0, 2),
            "input_tokens": 0, "output_tokens": 0,
        }


def aggregate(judgments: list) -> dict:
    n = len(judgments)
    if not n:
        return {}
    sums = {k: 0 for k in DIMENSIONS}
    mins = {k: 5 for k in DIMENSIONS}
    for j in judgments:
        for k in DIMENSIONS:
            v = j["scores"].get(k, 0)
            sums[k] += v
            if v < mins[k]:
                mins[k] = v
    avgs = {f"avg_{k}": round(sums[k] / n, 2) for k in DIMENSIONS}
    overall = round(sum(sums.values()) / (n * len(DIMENSIONS)), 2)

    low: list = []
    for j in judgments:
        if not j["scores"]:
            continue
        avg = sum(j["scores"].values()) / len(j["scores"])
        if avg < 3.5:
            low.append({"case_id": j["case_id"], "avg": round(avg, 2), "comment": j.get("comment", "")})

    in_total = sum(j.get("input_tokens", 0) for j in judgments)
    out_total = sum(j.get("output_tokens", 0) for j in judgments)
    cost = in_total * RATE_INPUT + out_total * RATE_OUTPUT
    return {
        "n_cases": n, **avgs, "overall_avg": overall,
        "min_scores_by_dimension": mins,
        "low_score_cases": low,
        "judge_input_tokens": in_total,
        "judge_output_tokens": out_total,
        "judge_cost_usd": round(cost, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-dir", type=Path, required=True,
                    help="run_eval.py の出力ディレクトリ（result.json があるところ）")
    ap.add_argument("--config", type=Path,
                    default=_SCRIPTS_DIR / "config" / "cs_triage.yaml")
    ap.add_argument("--dataset", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "dataset")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--only", nargs="+", default=None)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    editorial = cfg.get("editorial", {}) or {}

    payload = json.loads((args.result_dir / "result.json").read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c["case_id"] in wanted]

    # メール原文を input.txt から付与（judge プロンプトに含めるため）
    for c in cases:
        p = args.dataset / c["case_id"] / "input.txt"
        if p.exists():
            c["input_text"] = p.read_text(encoding="utf-8")

    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model=args.judge_model, max_tokens=400)

    judgments: list = []
    for c in cases:
        # dry-run の case はテンプレ本文しか出さない → judge には不適だが、参考スコアとして記録
        j = judge_one(c, editorial, llm)
        avg_str = (
            f"avg={sum(j['scores'].values()) / len(j['scores']):.2f}"
            if j["scores"] else "avg=NA"
        )
        print(f"  {j['case_id']}: scores={j['scores']} {avg_str} ({j['elapsed_s']:.1f}s)")
        judgments.append(j)

    summary = aggregate(judgments)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_json = args.result_dir / f"judge_{ts}.json"
    out_json.write_text(
        json.dumps({
            "timestamp": ts, "judge_model": args.judge_model,
            "source_result": str((args.result_dir / "result.json").resolve()),
            "summary": summary, "cases": judgments,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[done] judge result: {out_json}")
    print(f"[done] overall avg: {summary.get('overall_avg', 'NA')} / 5")
    print(f"[done] judge cost: ${summary.get('judge_cost_usd', 0):.4f}")


if __name__ == "__main__":
    main()
