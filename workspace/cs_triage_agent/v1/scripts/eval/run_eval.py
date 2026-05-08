"""eval/dataset/case_*/ を順次実行し、コードベース評価で集計する。

使い方:
    # dry-run（既定）— 全件をルールベースで完走、API 消費なし
    python scripts/eval/run_eval.py

    # 実 LLM — agent 本実行（コスト発生）
    python scripts/eval/run_eval.py --real

    # 部分実行 — 指定 case のみ
    python scripts/eval/run_eval.py --real --only case_01_inventory_basic case_15_complaint_repeat

出力:
    eval/results/run_<timestamp>/
        result.json                   個別ケースの結果
        summary.md                    集計サマリ
        cases/<case_id>/final.json    各ケースの final_output（後段の judge.py で使用）
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

from src.config import load_config  # noqa: E402
from src.cost import reset_usage, snapshot  # noqa: E402
from src.db_client import clear_tool_cache, load_fixtures  # noqa: E402


def _build_app():
    """LangGraph compile を 1 回だけ行う。"""
    import importlib

    agent = importlib.import_module("agent")
    return agent.build_graph()


def _run_one(app, cfg: dict, raw_text: str, case_id: str, dry_run: bool) -> dict:
    """1 ケースを実行して final_output と所要時間 / コストを返す。"""
    clear_tool_cache()
    reset_usage()
    initial = {
        "raw_text": raw_text,
        "case_id": case_id,
        "customer_id": "",
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
        "skus": final_state.get("skus", []) or [],
        "order_nos": final_state.get("order_nos", []) or [],
        "lang": final_state.get("lang", ""),
        "category": final_state.get("category", ""),
        "urgency": final_state.get("urgency", ""),
        "complaint_smell": final_state.get("complaint_smell", False),
        "retrieved_data": final_state.get("retrieved_data", {}),
        "missing_info": final_state.get("missing_info", []) or [],
        "draft_body": final_state.get("draft_body", ""),
        "elapsed_sec": round(elapsed, 3),
        "cost_usd": round(cost.get("cost_usd", 0.0), 6),
        "input_tokens": cost.get("input_tokens", 0),
        "output_tokens": cost.get("output_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Code-based scoring
# ---------------------------------------------------------------------------
def _list_inter(actual: list, expected: list) -> dict:
    actual_set = set(actual or [])
    expected_set = set(expected or [])
    if not expected_set:
        return {"recall": 1.0, "precision": 1.0 if not actual_set else 0.0, "tp": 0,
                "missing": [], "extra": list(actual_set)}
    tp = expected_set & actual_set
    return {
        "recall": round(len(tp) / len(expected_set), 4),
        "precision": round(len(tp) / max(1, len(actual_set)), 4),
        "tp": len(tp),
        "missing": sorted(expected_set - actual_set),
        "extra": sorted(actual_set - expected_set),
    }


def _score_case(meta: dict, run: dict) -> dict:
    expected = meta.get("expected", {}) or {}
    final = run["final_output"]
    body = final.get("customer_body", "")
    flags = final.get("flags", {}) or {}

    sku_score = _list_inter(run["skus"], expected.get("skus_must_include", []) or [])
    order_score = _list_inter(run["order_nos"], expected.get("order_nos_must_include", []) or [])

    must_inc = expected.get("body_must_include", []) or []
    must_inc_hits = sum(1 for k in must_inc if k in body)
    must_inc_rate = must_inc_hits / len(must_inc) if must_inc else 1.0

    must_not_inc = expected.get("body_must_not_include", []) or []
    must_not_violations = [k for k in must_not_inc if k in body]

    cat_match = run["category"] == expected.get("category")
    urg_match = run["urgency"] == expected.get("urgency")
    complaint_match = bool(run["complaint_smell"]) == bool(expected.get("complaint_smell"))
    sup_match = bool(flags.get("needs_supervisor")) == bool(expected.get("needs_supervisor"))

    # apology check
    # v2 修正をバックポート: 「お手数」は通常敬語の依頼表現として使われるため除外し、
    # 「申し訳」「お詫び」「ご迷惑」のみを apology とする。v1 eval の偽陽性を解消。
    apology_required = bool(expected.get("apology_required"))
    apology_kws = ["申し訳", "お詫び", "ご迷惑"]
    apology_present = any(k in body for k in apology_kws)
    apology_match = (apology_present == apology_required)

    # missing_info expectation (any-of)
    mi_any = expected.get("missing_info_should_include_any", []) or []
    mi_text = " ".join(run.get("missing_info", []) or [])
    mi_satisfied = (not mi_any) or any(k in mi_text or k in body for k in mi_any)

    # 致命: クレーム見逃し（complaint_smell=true 期待だが false） / カテゴリ完全外し
    is_critical_miss = (
        (expected.get("complaint_smell") and not run["complaint_smell"])
        or (expected.get("category") == "complaint" and run["category"] != "complaint")
    )

    passed = (
        cat_match and urg_match and complaint_match and sup_match
        and sku_score["recall"] >= 0.99
        and order_score["recall"] >= 0.99
        and must_inc_rate >= 0.99
        and len(must_not_violations) == 0
        and apology_match
        and mi_satisfied
    )

    return {
        "passed": passed,
        "is_critical_miss": is_critical_miss,
        "category_match": cat_match,
        "urgency_match": urg_match,
        "complaint_smell_match": complaint_match,
        "needs_supervisor_match": sup_match,
        "skus": sku_score,
        "order_nos": order_score,
        "body_must_include_rate": round(must_inc_rate, 4),
        "body_must_not_violations": must_not_violations,
        "apology_required": apology_required,
        "apology_present": apology_present,
        "apology_match": apology_match,
        "missing_info_satisfied": mi_satisfied,
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
def _aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    main = [r for r in rows if not r["meta"].get("edge_case")]
    edge = [r for r in rows if r["meta"].get("edge_case")]

    def _avg(arr, key, default=0.0):
        return round(sum(r["score"].get(key, default) for r in arr) / len(arr), 4) if arr else 0.0

    def _avg_nested(arr, *path):
        if not arr:
            return 0.0
        vals = []
        for r in arr:
            cur = r["score"]
            for p in path:
                cur = cur.get(p, {}) if isinstance(cur, dict) else {}
            if isinstance(cur, (int, float)):
                vals.append(float(cur))
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    summary = {
        "n_cases": n,
        "n_main": len(main),
        "n_edge": len(edge),
        "passed_total": sum(1 for r in rows if r["score"]["passed"]),
        "passed_main": sum(1 for r in main if r["score"]["passed"]),
        "critical_misses": sum(1 for r in rows if r["score"]["is_critical_miss"]),
        "category_accuracy": round(sum(1 for r in main if r["score"]["category_match"]) / len(main), 4) if main else 0.0,
        "urgency_accuracy": round(sum(1 for r in main if r["score"]["urgency_match"]) / len(main), 4) if main else 0.0,
        "complaint_recall_overall": _complaint_recall(rows),
        "complaint_precision_overall": _complaint_precision(rows),
        "sku_recall_avg": _avg_nested(main, "skus", "recall"),
        "sku_precision_avg": _avg_nested(main, "skus", "precision"),
        "order_no_recall_avg": _avg_nested(main, "order_nos", "recall"),
        "body_must_include_rate_avg": _avg(main, "body_must_include_rate"),
        "body_must_not_violations_total": sum(len(r["score"]["body_must_not_violations"]) for r in rows),
        "apology_match_rate": round(sum(1 for r in main if r["score"]["apology_match"]) / len(main), 4) if main else 0.0,
        "needs_supervisor_match_rate": round(sum(1 for r in main if r["score"]["needs_supervisor_match"]) / len(main), 4) if main else 0.0,
        "avg_elapsed_sec": round(sum(r["run"]["elapsed_sec"] for r in rows) / n, 3),
        "avg_cost_usd": round(sum(r["run"]["cost_usd"] for r in rows) / n, 6),
        "total_cost_usd": round(sum(r["run"]["cost_usd"] for r in rows), 6),
        "total_input_tokens": sum(r["run"]["input_tokens"] for r in rows),
        "total_output_tokens": sum(r["run"]["output_tokens"] for r in rows),
    }
    return summary


def _complaint_recall(rows: list[dict]) -> float:
    pos = [r for r in rows if r["meta"].get("expected", {}).get("complaint_smell")]
    if not pos:
        return 1.0
    tp = sum(1 for r in pos if r["run"]["complaint_smell"])
    return round(tp / len(pos), 4)


def _complaint_precision(rows: list[dict]) -> float:
    flagged = [r for r in rows if r["run"]["complaint_smell"]]
    if not flagged:
        return 1.0
    tp = sum(1 for r in flagged if r["meta"].get("expected", {}).get("complaint_smell"))
    return round(tp / len(flagged), 4)


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------
def _write_summary_md(out_dir: Path, summary: dict, rows: list[dict], dry_run: bool, target_path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# 評価サマリ — CS Triage Agent v1")
    lines.append("")
    lines.append(f"- 実行日時: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- モード: **{'dry-run（LLM なし、ルールベース完走）' if dry_run else '実 LLM'}**")
    lines.append(f"- ケース数: {summary['n_cases']}（main {summary['n_main']} + edge {summary['n_edge']}）")
    lines.append("")
    lines.append("## 主要メトリクス")
    lines.append("")
    lines.append("| 指標 | 値 | spec.md §9 目標 |")
    lines.append("|---|---|---|")
    lines.append(f"| パス率（主要 6 カテゴリ）| {summary['passed_main']}/{summary['n_main']} ({summary['passed_main']/summary['n_main']*100:.0f}%) | - |")
    lines.append(f"| カテゴリ正解率 | {summary['category_accuracy']*100:.1f}% | 90% |")
    lines.append(f"| クレーム再現率（recall）| {summary['complaint_recall_overall']*100:.1f}% | 95% |")
    lines.append(f"| クレーム適合率（precision）| {summary['complaint_precision_overall']*100:.1f}% | 60% |")
    lines.append(f"| SKU 抽出再現率（平均）| {summary['sku_recall_avg']*100:.1f}% | 95% |")
    lines.append(f"| 必須テンプレ語含有率（平均）| {summary['body_must_include_rate_avg']*100:.1f}% | 100% |")
    lines.append(f"| 禁止語違反（合計）| {summary['body_must_not_violations_total']} 件 | 0 件 |")
    lines.append(f"| お詫び一致率 | {summary['apology_match_rate']*100:.1f}% | - |")
    lines.append(f"| SV 引き継ぎフラグ一致率 | {summary['needs_supervisor_match_rate']*100:.1f}% | - |")
    lines.append(f"| 致命ミス（クレーム見逃し / カテゴリ大外し）| {summary['critical_misses']} 件 | 0 件 |")
    lines.append(f"| 平均所要時間 / 件 | {summary['avg_elapsed_sec']:.3f} 秒 | < 30 秒 |")
    lines.append(f"| 平均コスト / 件 | ${summary['avg_cost_usd']:.4f} | < $0.05 |")
    lines.append(f"| 総コスト | ${summary['total_cost_usd']:.4f} | - |")
    lines.append("")
    lines.append("## ケース別結果")
    lines.append("")
    lines.append("| case | カテゴリ | 期待 | 実測 | clmpassed | SKU rec | order rec | body 必須 | お詫び | SV | 致命 | 秒 | $ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        s = r["score"]
        ru = r["run"]
        m = r["meta"]
        exp_cat = m.get("expected", {}).get("category", "-")
        crit = "🔴" if s["is_critical_miss"] else ""
        passed_mark = "✓" if s["passed"] else "✗"
        lines.append(
            f"| {r['case_id']} | {exp_cat} | {exp_cat} | {ru['category']} | {passed_mark} | "
            f"{s['skus']['recall']*100:.0f}% | {s['order_nos']['recall']*100:.0f}% | "
            f"{s['body_must_include_rate']*100:.0f}% | {'✓' if s['apology_match'] else '✗'} | "
            f"{'✓' if s['needs_supervisor_match'] else '✗'} | {crit} | {ru['elapsed_sec']:.2f} | {ru['cost_usd']:.4f} |"
        )
    lines.append("")
    lines.append(f"Detailed JSON: `{target_path.name}`")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path,
                    default=_SCRIPTS_DIR / "config" / "cs_triage.yaml")
    ap.add_argument("--dataset", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "dataset")
    ap.add_argument("--mock-db", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "mock_db")
    ap.add_argument("--output-root", type=Path,
                    default=_SCRIPTS_DIR / "eval" / "results")
    ap.add_argument("--real", action="store_true",
                    help="dry-run を解除し実 LLM を呼ぶ（コスト発生）")
    ap.add_argument("--only", nargs="+", default=None,
                    help="特定 case のみ実行（複数指定可）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    load_fixtures(args.mock_db)
    app = _build_app()

    cases = sorted([p for p in args.dataset.iterdir() if p.is_dir() and p.name.startswith("case_")])
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c.name in wanted]
    if not cases:
        print("ERROR: 評価対象 case が見つかりません", file=sys.stderr)
        sys.exit(1)

    dry_run = not args.real
    if not dry_run and not _has_api_key():
        print("ERROR: --real 指定だが ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = args.output_root / f"run_{ts}{'_dry' if dry_run else '_real'}"
    (run_dir / "cases").mkdir(parents=True, exist_ok=True)

    print(f"[info] dataset={args.dataset.name} cases={len(cases)} mode={'dry-run' if dry_run else 'real'}")

    rows: list[dict] = []
    for case_dir in cases:
        case_id = case_dir.name
        meta = yaml.safe_load((case_dir / "metadata.yaml").read_text(encoding="utf-8")) or {}
        raw = (case_dir / "input.txt").read_text(encoding="utf-8")
        try:
            run = _run_one(app, cfg, raw, case_id, dry_run)
        except Exception as e:
            print(f"  {case_id}: ERROR {e}")
            run = {
                "final_output": {}, "skus": [], "order_nos": [], "lang": "",
                "category": "", "urgency": "", "complaint_smell": False,
                "retrieved_data": {}, "missing_info": [], "draft_body": "",
                "elapsed_sec": 0.0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                "error": str(e),
            }
        score = _score_case(meta, run) if "error" not in run else {"passed": False, "is_critical_miss": True, "skus": {"recall": 0, "precision": 0}, "order_nos": {"recall": 0, "precision": 0}, "body_must_include_rate": 0, "body_must_not_violations": [], "apology_required": False, "apology_present": False, "apology_match": False, "missing_info_satisfied": False, "category_match": False, "urgency_match": False, "complaint_smell_match": False, "needs_supervisor_match": False}
        rows.append({"case_id": case_id, "meta": meta, "run": run, "score": score})

        # 個別 final.json を保存（後段 judge.py で使う）
        case_out = run_dir / "cases" / case_id
        case_out.mkdir(parents=True, exist_ok=True)
        (case_out / "final.json").write_text(
            json.dumps(run["final_output"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        passed_mark = "✓" if score["passed"] else "✗"
        print(f"  {case_id}: {passed_mark} cat={run['category']} cmp={run['complaint_smell']} "
              f"sku_rec={score['skus']['recall']*100:.0f}% time={run['elapsed_sec']:.2f}s")

    summary = _aggregate(rows)
    out_json = run_dir / "result.json"
    out_json.write_text(
        json.dumps({
            "timestamp": ts,
            "dry_run": dry_run,
            "config_path": str(args.config),
            "n_cases": len(rows),
            "summary": summary,
            "cases": [
                {"case_id": r["case_id"], "meta": r["meta"], "run": r["run"], "score": r["score"]}
                for r in rows
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_summary_md(run_dir, summary, rows, dry_run, out_json)
    print(f"\n[done] result={out_json}")
    print(f"[done] summary={run_dir / 'summary.md'}")
    print(f"[done] passed={summary['passed_total']}/{summary['n_cases']} "
          f"critical_miss={summary['critical_misses']} "
          f"cost=${summary['total_cost_usd']:.4f}")


def _has_api_key() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


if __name__ == "__main__":
    main()
