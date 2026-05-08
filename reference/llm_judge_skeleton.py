"""LLM as a Judge 雛形 — 5 次元採点で agent ドラフトの品質を客観評価する。

このファイルをコピーして scripts/eval/llm_judge.py の起点にする。
プロジェクト固有部分 (judge プロンプト、スコア次元) を調整して使う。

設計の原則:
    - judge には「評価対象の情報を完全に渡す」が最重要
      (本文を切り詰めない、DB結果を欠落させない、ペルソナ設定を渡す)
    - 5 次元前後で採点 (多すぎると分散しすぎ、少なすぎると粒度不足)
    - スコアは 1-5 で「3 を平均」標準
    - 失敗時は score=0 で無効化、運用時にカウント
    - judge model は Sonnet 推奨 (Haiku は判定が甘くなる)

実プロジェクトでの教訓 (cs_triage_agent v1 evolve から):
    - judge.py で「actual_body_excerpt (200文字)」と保存していたら、judge が
      「文章が途中で切断」と誤判定し全件低スコア (overall 2.46 → 修正後 3.52)
    - db_results を渡していなかったら「DBなしで捏造」と誤判定
    - => 評価対象の情報は「完全に」judge に渡す。切り詰め・欠落させない
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

from dotenv import load_dotenv

load_dotenv()

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

# Sonnet 4.6 のレート (1 トークンあたり USD)
RATE_INPUT = 3.00 / 1_000_000
RATE_OUTPUT = 15.00 / 1_000_000


# ---------------------------------------------------------------------------
# Judge プロンプトテンプレート
#
# プロジェクト固有: 5 次元の選定はドメインで変える。下記は B2B CS の例。
#   - persona_fit: 編集者ペルソナの声が出ているか
#   - tone_appropriate: トーン (敬語・簡潔さ) が適切か
#   - info_completeness: 情報を漏れなく過剰なく使っているか
#   - numerical_accuracy: 数値が DB値と一致しているか
#   - ng_phrase_avoidance: NG 表現を直接使わず汎化しているか
# ---------------------------------------------------------------------------
JUDGE_SYSTEM = (
    "あなたは AI エージェントが生成した出力の品質を評価する評価者です。"
    "編集者ペルソナの視点で、5 次元で 1-5 で採点してください。"
    "スコアは厳しめにつけ、3 を平均、5 を例外的に優秀、1 を即修正必要 とする標準で評価。"
)

JUDGE_USER_TMPL = """\
== 編集者ペルソナ ==
{persona}

== 編集者の視点 ==
{perspective}

== 執筆ルール ==
{rules}

== 入力情報 (judge の参考) ==
{context}

== AI 生成出力 (採点対象、フル本文) ==
{output}

== 評価 ==
以下 5 次元を 1-5 で採点し、JSON で返してください。プロジェクトに合わせて調整可:
- persona_fit: ペルソナの声が出ているか
- tone_appropriate: トーンが適切か
- info_completeness: 情報を漏れなく過剰なく使っているか
- numerical_accuracy: 数値が事実値と一致しているか
- ng_phrase_avoidance: NG表現を直接使わず汎化しているか

JSON 形式 (前置き禁止、JSON1個のみ):
{{"persona_fit": <int 1-5>, "tone_appropriate": <int 1-5>, "info_completeness": <int 1-5>, "numerical_accuracy": <int 1-5>, "ng_phrase_avoidance": <int 1-5>, "comment": "<50文字以内の総評>"}}
"""

DIMENSIONS = (
    "persona_fit",
    "tone_appropriate",
    "info_completeness",
    "numerical_accuracy",
    "ng_phrase_avoidance",
)


# ---------------------------------------------------------------------------
# 個別ケース判定
# ---------------------------------------------------------------------------
def _parse_score_json(content: str) -> dict | None:
    if not content:
        return None
    m = re.search(r'\{.*\}', content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def judge_one(case: dict, editorial: dict, llm) -> dict:
    """1 ケースを採点。

    Args:
        case: {
            "case_id": str,
            "actual_body": str,           # フル本文 (絶対に切り詰めない)
            "actual_db_results": dict,    # DB 結果 (judge が事実値検証に使う)
            "actual_meta": dict,          # category, intent_summary 等
        }
        editorial: {"persona": str, "perspective": str, "rules": str}
        llm: ChatAnthropic インスタンス
    """
    body = case.get("actual_body") or case.get("final_draft_body") or ""
    db_results = case.get("actual_db_results") or {}
    meta = case.get("actual_meta") or case.get("meta_json") or {}

    # judge への入力は「完全」が原則。切り詰め・欠落させない
    context_lines = [
        f"カテゴリ: {meta.get('category', '(unknown)')}",
        f"意図: {meta.get('intent_summary', '')}",
    ]
    if db_results:
        context_lines.append(
            f"DB ルックアップ結果:\n```json\n{json.dumps(db_results, ensure_ascii=False, indent=2)}\n```"
        )
    else:
        context_lines.append("DB ルックアップ対象外 (本ケースは DB 照会なし)")

    user = JUDGE_USER_TMPL.format(
        persona=(editorial.get("persona") or "").strip(),
        perspective=(editorial.get("perspective") or "").strip(),
        rules=(editorial.get("rules") or "").strip(),
        context="\n".join(context_lines),
        output=body,  # 切り詰めない!
    )

    t0 = time.time()
    try:
        resp = llm.invoke(
            [SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=user)]
        )
        elapsed = time.time() - t0
        meta_usage = getattr(resp, "usage_metadata", {}) or {}
        in_t = int(meta_usage.get("input_tokens", 0) or 0)
        out_t = int(meta_usage.get("output_tokens", 0) or 0)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        parsed = _parse_score_json(content)

        if not parsed:
            return {
                "case_id": case.get("case_id", "?"),
                "scores": {k: 0 for k in DIMENSIONS},
                "comment": "JSON parse failed",
                "elapsed_s": round(elapsed, 2),
                "input_tokens": in_t,
                "output_tokens": out_t,
            }

        scores = {}
        for k in DIMENSIONS:
            v = parsed.get(k, 0)
            try:
                scores[k] = max(0, min(5, int(v)))
            except (TypeError, ValueError):
                scores[k] = 0

        return {
            "case_id": case.get("case_id", "?"),
            "scores": scores,
            "comment": (parsed.get("comment", "") or "")[:200],
            "elapsed_s": round(elapsed, 2),
            "input_tokens": in_t,
            "output_tokens": out_t,
        }
    except Exception as e:
        return {
            "case_id": case.get("case_id", "?"),
            "scores": {k: 0 for k in DIMENSIONS},
            "comment": f"ERROR: {e}",
            "elapsed_s": round(time.time() - t0, 2),
            "input_tokens": 0,
            "output_tokens": 0,
        }


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def aggregate(judgments: list[dict]) -> dict:
    n = len(judgments)
    if n == 0:
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

    low = []
    for j in judgments:
        if not j["scores"]:
            continue
        avg = sum(j["scores"].values()) / len(j["scores"])
        if avg < 3.5:
            low.append({
                "case_id": j["case_id"],
                "avg": round(avg, 2),
                "comment": j.get("comment", ""),
            })

    in_total = sum(j.get("input_tokens", 0) for j in judgments)
    out_total = sum(j.get("output_tokens", 0) for j in judgments)
    cost = in_total * RATE_INPUT + out_total * RATE_OUTPUT

    return {
        "n_cases": n,
        **avgs,
        "overall_avg": overall,
        "min_scores_by_dimension": mins,
        "low_score_cases": low,
        "judge_input_tokens": in_total,
        "judge_output_tokens": out_total,
        "judge_cost_usd": round(cost, 4),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """雛形なのでプロジェクト側で書き換える前提。

    最低限の構成:
      1. result_*.json を読む (eval/judge.py の出力前提)
      2. config.yaml から editorial.persona / perspective / rules を取得
      3. judge_one を全ケースに適用
      4. aggregate で集計
      5. judge_<ts>.json と judge_<ts>.md に出力
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True, help="採点対象 result_*.json")
    ap.add_argument("--config", type=Path, required=True, help="editorial を読む YAML")
    ap.add_argument("--output-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    import yaml
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    editorial = cfg.get("editorial", {}) or {}

    payload_in = json.loads(args.result.read_text(encoding="utf-8"))
    cases = payload_in.get("cases", [])

    llm = ChatAnthropic(model=args.judge_model, max_tokens=400)

    judgments = []
    for case in cases:
        if case.get("error"):
            continue
        j = judge_one(case, editorial, llm)
        print(f"  {j['case_id']}: {j['scores']} ({j['elapsed_s']:.1f}s)")
        judgments.append(j)

    summary = aggregate(judgments)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_json = args.output_dir / f"judge_{ts}.json"
    out_json.write_text(
        json.dumps(
            {
                "timestamp": ts,
                "judge_model": args.judge_model,
                "source_result": args.result.name,
                "summary": summary,
                "cases": judgments,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWritten: {out_json}")
    print(f"Overall avg: {summary['overall_avg']} / 5")
    print(f"Cost: ${summary['judge_cost_usd']:.4f}")


if __name__ == "__main__":
    main()
