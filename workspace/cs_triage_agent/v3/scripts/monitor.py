"""コスト・品質モニタ（日次 / 月次集計）。

deploy フェーズで SE 工藤が CronJob 化する想定のスケルトン。

入力:
    data/cost_log.csv          1 行 1 実行（ts, case_id, cost_usd, latency_sec, models, complaint）
    data/pii_audit_log.sqlite  PII 監査用ハッシュ

出力:
    data/cost_summary_<YYYY-MM>.md       月次レポート
    Slack #cs-system-alerts              閾値超過通知
    Datadog metrics                      集計値の publish

TODO（SE 工藤）:
    - statsd / datadog SDK で metrics publish
    - 月初に cost_log.csv を rotate（前月分は data/archive/ に移動）
    - PII 監査結果のサマリ生成
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent

# from datadog import statsd  # TODO: 本番で import
# from src.dispatch_slack import SlackDispatcher

MAX_PER_REQUEST_USD = float(os.environ.get("MAX_PER_REQUEST_USD", "0.10"))
MAX_MONTHLY_USD = float(os.environ.get("MAX_MONTHLY_USD", "3000"))
LATENCY_P95_TARGET = float(os.environ.get("LATENCY_P95_TARGET", "30"))


def load_cost_log(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def daily_summary(rows: list, target_date: date) -> dict:
    target_str = target_date.isoformat()
    today = [r for r in rows if r.get("ts", "").startswith(target_str)]
    if not today:
        return {"date": target_str, "n": 0}
    n = len(today)
    cost = sum(float(r.get("cost_usd", 0)) for r in today)
    latencies = sorted(float(r.get("latency_sec", 0)) for r in today)
    p95 = latencies[int(n * 0.95)] if n > 1 else (latencies[0] if latencies else 0)
    complaints = sum(1 for r in today if r.get("complaint", "").lower() == "true")
    fallbacks = sum(1 for r in today if r.get("ai_failed", "").lower() == "true")
    return {
        "date": target_str,
        "n": n,
        "total_cost_usd": round(cost, 4),
        "avg_cost_usd": round(cost / n, 6) if n else 0,
        "p95_latency_sec": round(p95, 2),
        "complaint_count": complaints,
        "fallback_count": fallbacks,
    }


def monthly_summary(rows: list, year: int, month: int) -> dict:
    prefix = f"{year:04d}-{month:02d}"
    target = [r for r in rows if r.get("ts", "").startswith(prefix)]
    if not target:
        return {"month": prefix, "n": 0}
    n = len(target)
    cost = sum(float(r.get("cost_usd", 0)) for r in target)
    by_cat = defaultdict(int)
    for r in target:
        by_cat[r.get("category", "other")] += 1
    return {
        "month": prefix,
        "n": n,
        "total_cost_usd": round(cost, 4),
        "avg_cost_usd": round(cost / n, 6),
        "budget_used_pct": round(cost / MAX_MONTHLY_USD * 100, 1),
        "by_category": dict(by_cat),
    }


def write_monthly_report(summary: dict, out_path: Path) -> None:
    lines = [
        f"# コスト月次レポート — {summary['month']}",
        "",
        f"- 処理件数: {summary['n']:,}",
        f"- 月合計コスト: ${summary['total_cost_usd']:.2f}",
        f"- 平均/件: ${summary['avg_cost_usd']:.4f}",
        f"- 月予算消化率: {summary['budget_used_pct']}%",
        "",
        "## カテゴリ別件数",
        "",
        "| カテゴリ | 件数 |",
        "|---|---|",
    ]
    for cat, n in sorted(summary.get("by_category", {}).items(), key=lambda x: -x[1]):
        lines.append(f"| {cat} | {n:,} |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_alerts(daily: dict) -> list[tuple[str, str, dict]]:
    """日次サマリから発火すべきアラートを返す: [(severity, message, context), ...]"""
    alerts = []
    if daily["n"] == 0:
        return alerts
    # P95 latency
    if daily.get("p95_latency_sec", 0) > LATENCY_P95_TARGET:
        alerts.append(("P3", f"P95 レイテンシが目標 {LATENCY_P95_TARGET}s を超過: {daily['p95_latency_sec']}s",
                       daily))
    # 1 件あたりコスト超過率
    if daily.get("avg_cost_usd", 0) > MAX_PER_REQUEST_USD * 0.5:
        alerts.append(("P3", f"平均コスト/件が予算の 50% 超: ${daily['avg_cost_usd']:.4f}", daily))
    # fallback 急増
    if daily.get("n") > 0 and daily["fallback_count"] / daily["n"] > 0.05:
        alerts.append(("P2", f"テンプレフォールバック率 5% 超: {daily['fallback_count']}/{daily['n']}",
                       daily))
    return alerts


def check_monthly_budget(monthly: dict) -> list[tuple[str, str, dict]]:
    alerts = []
    pct = monthly.get("budget_used_pct", 0)
    if pct >= 95:
        alerts.append(("P1", f"月予算 95% 到達: ${monthly['total_cost_usd']:.2f} / ${MAX_MONTHLY_USD}",
                       monthly))
    elif pct >= 80:
        alerts.append(("P3", f"月予算 80% 到達: ${monthly['total_cost_usd']:.2f} / ${MAX_MONTHLY_USD}",
                       monthly))
    return alerts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-log", type=Path, default=_ROOT / "data" / "cost_log.csv")
    ap.add_argument("--out-dir", type=Path, default=_ROOT / "data")
    ap.add_argument("--mode", choices=["daily", "monthly"], default="daily")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--month", type=int, default=date.today().month)
    ap.add_argument("--date", type=str, default=date.today().isoformat())
    ap.add_argument("--notify-slack", action="store_true",
                    help="アラート発火時に Slack 通知（要 dispatch_slack.py）")
    args = ap.parse_args()

    rows = load_cost_log(args.cost_log)
    if not rows:
        print("cost_log.csv が空です。スキップ。")
        return

    if args.mode == "daily":
        target_d = date.fromisoformat(args.date)
        s = daily_summary(rows, target_d)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        alerts = check_alerts(s)
    else:
        s = monthly_summary(rows, args.year, args.month)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        out = args.out_dir / f"cost_summary_{s['month']}.md"
        write_monthly_report(s, out)
        print(f"wrote {out}")
        alerts = check_monthly_budget(s)

    for severity, msg, ctx in alerts:
        print(f"[ALERT] {severity}: {msg}")
        if args.notify_slack:
            # TODO: from dispatch_slack import SlackDispatcher
            # SlackDispatcher().notify_system_alert(severity, msg, ctx)
            pass


if __name__ == "__main__":
    main()
