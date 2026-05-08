"""eval/dataset/case_*/ を順次実行し、コードベース評価で集計。

使い方:
    python scripts/eval/run_eval.py             # dry-run、API 消費なし
    python scripts/eval/run_eval.py --real      # 実 LLM
    python scripts/eval/run_eval.py --only case_08 case_10
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from src.config import load_config
from src.cost import reset_usage, snapshot
from src.db_client import clear_tool_cache, load_fixtures


def _build_app():
    import importlib
    return importlib.import_module("agent").build_graph()


def _run_one(app, cfg: dict, payload: dict, case_id: str, dry_run: bool) -> dict:
    clear_tool_cache()
    reset_usage()
    initial = {
        "raw_payload": payload,
        "application_id": case_id,
        "cfg": cfg,
        "dry_run": dry_run,
        "lite_mode": False,
    }
    t0 = time.time()
    final_state = app.invoke(initial)
    elapsed = time.time() - t0
    cost = snapshot()
    return {
        "final_output": final_state.get("final_output", {}),
        "decision": final_state.get("decision", ""),
        "rule_violations": final_state.get("rule_violations", []) or [],
        "duplicate_candidates": final_state.get("duplicate_candidates", []) or [],
        "risk_score": final_state.get("risk_score", 0.0),
        "feedback_message": final_state.get("feedback_message", ""),
        "suggested_fixes": final_state.get("suggested_fixes", []) or [],
        "elapsed_sec": round(elapsed, 3),
        "cost_usd": round(cost.get("cost_usd", 0.0), 6),
        "input_tokens": cost.get("input_tokens", 0),
        "output_tokens": cost.get("output_tokens", 0),
    }


def _score_case(meta: dict, run: dict) -> dict:
    expected = meta.get("expected", {}) or {}
    final = run["final_output"]
    body = final.get("feedback_message", "") or run.get("feedback_message", "")

    decision_match = run["decision"] == expected.get("decision")

    # rule_violations_must_include
    must_include_rules = expected.get("rule_violations_must_include", []) or []
    actual_rule_ids = {v.get("rule_id") for v in run["rule_violations"]}
    missing_rules = [r for r in must_include_rules if r not in actual_rule_ids]
    rules_ok = (len(missing_rules) == 0)

    # feedback must include
    must_inc = expected.get("feedback_must_include", []) or []
    body_must_inc_hits = sum(1 for k in must_inc if k in body)
    feedback_inc_rate = body_must_inc_hits / len(must_inc) if must_inc else 1.0

    # feedback must not include
    must_not_inc = expected.get("feedback_must_not_include", []) or []
    forbidden_violations = [k for k in must_not_inc if k in body]

    # suggested fixes
    sf_min = int(expected.get("suggested_fixes_min", 0))
    sf_count = len(run["suggested_fixes"])
    sf_ok = (sf_count >= sf_min)

    # duplicate
    dup_expected = bool(expected.get("duplicate_suspected", False))
    dup_actual = bool(final.get("flags", {}).get("duplicate_suspected"))
    dup_match = (dup_expected == dup_actual)

    # critical: auto_approve なのに違反あり
    is_critical_miss = (run["decision"] == "auto_approve") and bool(run["rule_violations"])

    passed = (
        decision_match and rules_ok
        and feedback_inc_rate >= 0.99
        and len(forbidden_violations) == 0
        and sf_ok and dup_match
    )

    return {
        "passed": passed,
        "is_critical_miss": is_critical_miss,
        "decision_match": decision_match,
        "rules_ok": rules_ok,
        "missing_rules": missing_rules,
        "feedback_inc_rate": round(feedback_inc_rate, 4),
        "forbidden_violations": forbidden_violations,
        "suggested_fixes_ok": sf_ok,
        "suggested_fixes_count": sf_count,
        "duplicate_match": dup_match,
    }


def _aggregate(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {}

    def _avg(arr, key):
        return round(sum(r["score"].get(key, 0) for r in arr) / len(arr), 4) if arr else 0.0

    return {
        "n_cases": n,
        "passed": sum(1 for r in rows if r["score"]["passed"]),
        "critical_misses": sum(1 for r in rows if r["score"]["is_critical_miss"]),
        "decision_accuracy": round(sum(1 for r in rows if r["score"]["decision_match"]) / n, 4),
        "rule_check_accuracy": round(sum(1 for r in rows if r["score"]["rules_ok"]) / n, 4),
        "feedback_inc_rate_avg": _avg(rows, "feedback_inc_rate"),
        "forbidden_violations_total": sum(len(r["score"]["forbidden_violations"]) for r in rows),
        "duplicate_match_rate": round(sum(1 for r in rows if r["score"]["duplicate_match"]) / n, 4),
        "avg_elapsed_sec": round(sum(r["run"]["elapsed_sec"] for r in rows) / n, 3),
        "avg_cost_usd": round(sum(r["run"]["cost_usd"] for r in rows) / n, 6),
        "total_cost_usd": round(sum(r["run"]["cost_usd"] for r in rows), 6),
        "total_input_tokens": sum(r["run"]["input_tokens"] for r in rows),
        "total_output_tokens": sum(r["run"]["output_tokens"] for r in rows),
    }


def _write_summary_md(out_dir: Path, summary: dict, rows: list, dry_run: bool, target_path: Path) -> None:
    lines = [
        f"# 評価サマリ — Expense Review Agent v1",
        "",
        f"- 実行: {datetime.now().isoformat(timespec='seconds')}",
        f"- モード: **{'dry-run' if dry_run else '実 LLM'}**",
        f"- ケース数: {summary['n_cases']}",
        "",
        "## 主要メトリクス",
        "",
        "| 指標 | 値 |",
        "|---|---|",
        f"| パス率 | {summary['passed']}/{summary['n_cases']} ({summary['passed']/summary['n_cases']*100:.0f}%) |",
        f"| 致命ミス（auto_approve なのに違反あり）| {summary['critical_misses']} 件 |",
        f"| 判定一致率 | {summary['decision_accuracy']*100:.1f}% |",
        f"| ルール検査正解率 | {summary['rule_check_accuracy']*100:.1f}% |",
        f"| フィードバック必須語含有率（平均）| {summary['feedback_inc_rate_avg']*100:.1f}% |",
        f"| 禁止語違反（合計）| {summary['forbidden_violations_total']} 件 |",
        f"| 重複検出一致率 | {summary['duplicate_match_rate']*100:.1f}% |",
        f"| 平均所要時間 / 件 | {summary['avg_elapsed_sec']:.3f} 秒 |",
        f"| 平均コスト / 件 | ${summary['avg_cost_usd']:.4f} |",
        f"| 総コスト | ${summary['total_cost_usd']:.4f} |",
        "",
        "## ケース別",
        "",
        "| case | 期待判定 | 実測判定 | passed | rules | 必須語 | 禁止語違反 | 致命 | 秒 | $ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        s = r["score"]
        ru = r["run"]
        m = r["meta"]
        exp = m.get("expected", {}).get("decision", "-")
        crit = "🔴" if s["is_critical_miss"] else ""
        lines.append(
            f"| {r['case_id']} | {exp} | {ru['decision']} | "
            f"{'✓' if s['passed'] else '✗'} | "
            f"{'✓' if s['rules_ok'] else '✗'} | "
            f"{s['feedback_inc_rate']*100:.0f}% | "
            f"{len(s['forbidden_violations'])} | "
            f"{crit} | {ru['elapsed_sec']:.2f} | {ru['cost_usd']:.4f} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path,
                    default=_SCRIPTS_DIR / "config" / "expense_policy.yaml")
    ap.add_argument("--dataset", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "dataset")
    ap.add_argument("--mock-db", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "mock_db")
    ap.add_argument("--output-root", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "results")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--only", nargs="+", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    load_fixtures(args.mock_db)
    app = _build_app()

    cases = sorted([p for p in args.dataset.iterdir() if p.is_dir() and p.name.startswith("case_")])
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c.name in wanted]

    dry_run = not args.real
    if not dry_run and not __import__("os").environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = args.output_root / f"run_{ts}{'_dry' if dry_run else '_real'}"
    (run_dir / "cases").mkdir(parents=True, exist_ok=True)

    print(f"[info] cases={len(cases)} mode={'dry-run' if dry_run else 'real'}")

    rows = []
    for case_dir in cases:
        case_id = case_dir.name
        meta = yaml.safe_load((case_dir / "metadata.yaml").read_text(encoding="utf-8")) or {}
        payload = json.loads((case_dir / "input.json").read_text(encoding="utf-8"))
        try:
            run = _run_one(app, cfg, payload, case_id, dry_run)
        except Exception as e:
            print(f"  {case_id}: ERROR {e}")
            continue
        score = _score_case(meta, run)
        rows.append({"case_id": case_id, "meta": meta, "run": run, "score": score})

        case_out = run_dir / "cases" / case_id
        case_out.mkdir(parents=True, exist_ok=True)
        (case_out / "final.json").write_text(
            json.dumps(run["final_output"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        mark = "✓" if score["passed"] else "✗"
        print(f"  {case_id}: {mark} decision={run['decision']} time={run['elapsed_sec']:.2f}s cost=${run['cost_usd']:.4f}")

    summary = _aggregate(rows)
    out_json = run_dir / "result.json"
    out_json.write_text(json.dumps({
        "timestamp": ts, "dry_run": dry_run, "summary": summary,
        "cases": [{"case_id": r["case_id"], "meta": r["meta"], "run": r["run"], "score": r["score"]}
                  for r in rows],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_summary_md(run_dir, summary, rows, dry_run, out_json)

    print(f"\n[done] result={out_json}")
    print(f"[done] passed={summary['passed']}/{summary['n_cases']} "
          f"critical_miss={summary['critical_misses']} "
          f"cost=${summary['total_cost_usd']:.4f}")


if __name__ == "__main__":
    main()
