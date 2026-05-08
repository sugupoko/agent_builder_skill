"""Expense Review Agent v1 — LangGraph 8 ノード実装。

CLI:
    python scripts/agent.py --config scripts/config/expense_policy.yaml \\
        --input scripts/eval/dataset/case_01.json --case-id case_01

    python scripts/agent.py --config ... --input ... --dry-run    # API 消費なし
    python scripts/agent.py --config ... --print-graph             # Mermaid 出力
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

load_dotenv()

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from src.assemble import assemble_node
from src.classify_gray import classify_gray_node
from src.config import load_config
from src.cost import reset_usage, snapshot
from src.db_client import clear_tool_cache, load_fixtures
from src.draft_decision import draft_decision_node
from src.extract import extract_node
from src.logger import logger, setup_logger
from src.lookup_history import lookup_history_node
from src.preprocess import preprocess_node
from src.reflect import reflect_node
from src.state import ReviewState
from src.validate_rules import validate_rules_node


def route_after_reflect(state: ReviewState) -> str:
    cfg_max_iter = state.get("cfg", {}).get("reflection", {}).get("max_iterations", 1)
    if state.get("reflect_pass"):
        return "assemble"
    if (state.get("reflect_iter", 0) or 0) >= cfg_max_iter:
        return "assemble"
    return "draft_decision"


def _decide_lite_mode(state: ReviewState) -> dict:
    """軽量モード起動条件: 違反 0 + 重複 0 + 金額閾値以下。"""
    violations = state.get("rule_violations", []) or []
    duplicates = state.get("duplicate_candidates", []) or []
    amount = int((state.get("parsed_fields", {}) or {}).get("amount_jpy", 0) or 0)
    threshold = int((state.get("cfg", {}) or {}).get("lite_mode_amount_threshold_jpy", 30000))
    lite = (not violations) and (not duplicates) and (amount <= threshold)
    return {"lite_mode": lite}


def lite_mode_decision_node(state: ReviewState) -> dict:
    """軽量モード起動条件をチェックする中間ノード（lookup_history → classify_gray の間）。"""
    out = _decide_lite_mode(state)
    logger.info("[lite_check] lite_mode=%s", out["lite_mode"])
    return out


def build_graph():
    g = StateGraph(ReviewState)
    g.add_node("preprocess", preprocess_node)
    g.add_node("extract", extract_node)
    g.add_node("validate_rules", validate_rules_node)
    g.add_node("lookup_history", lookup_history_node)
    g.add_node("lite_check", lite_mode_decision_node)
    g.add_node("classify_gray", classify_gray_node)
    g.add_node("draft_decision", draft_decision_node)
    g.add_node("reflect", reflect_node)
    g.add_node("assemble", assemble_node)

    g.set_entry_point("preprocess")
    g.add_edge("preprocess", "extract")
    g.add_edge("extract", "validate_rules")
    g.add_edge("validate_rules", "lookup_history")
    g.add_edge("lookup_history", "lite_check")
    g.add_edge("lite_check", "classify_gray")
    g.add_edge("classify_gray", "draft_decision")
    g.add_edge("draft_decision", "reflect")
    g.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"draft_decision": "draft_decision", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)
    return g.compile()


def main() -> None:
    ap = argparse.ArgumentParser(description="Expense Review Agent v1")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--input", type=Path,
                    help="申請 JSON ファイル。未指定は stdin")
    ap.add_argument("--case-id", default="LOCAL-CASE-0000")
    ap.add_argument("--dry-run", action="store_true",
                    help="LLM を呼ばず rule-based で完走")
    ap.add_argument("--mock-db", type=Path,
                    default=_THIS_DIR / "eval" / "mock_db")
    ap.add_argument("--output", type=Path,
                    default=_THIS_DIR / "output")
    ap.add_argument("--print-graph", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    app = build_graph()

    if args.print_graph:
        print(app.get_graph().draw_mermaid())
        return

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 未設定。--dry-run なら不要", file=sys.stderr)
        sys.exit(1)

    log_dir = _THIS_DIR / "logs"
    setup_logger(slug=cfg.get("slug", "expense"), log_dir=log_dir)

    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())

    load_fixtures(args.mock_db)
    clear_tool_cache()
    reset_usage()

    initial: ReviewState = {
        "raw_payload": payload,
        "application_id": args.case_id,
        "cfg": cfg,
        "dry_run": args.dry_run,
        "lite_mode": False,
    }

    logger.info("=== agent start === case=%s dry_run=%s", args.case_id, args.dry_run)
    final_state = app.invoke(initial)

    cost = snapshot()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"final_{args.case_id}.json"
    out_path.write_text(
        json.dumps(final_state.get("final_output", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("=== done === output=%s cost=$%.4f", out_path, cost["cost_usd"])
    print(json.dumps(final_state.get("final_output", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
