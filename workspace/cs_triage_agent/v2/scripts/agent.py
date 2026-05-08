"""CS Triage Agent v1 — LangGraph StateGraph 7 ノード実装。

CLI:
    python scripts/agent.py --config scripts/config/cs_triage.yaml \\
        --input scripts/eval/dataset/sample_01.txt --case-id CASE-0001

    python scripts/agent.py --config scripts/config/cs_triage.yaml \\
        --input scripts/eval/dataset/sample_01.txt --dry-run
        # LLM 呼び出しなしでパイプライン全体を完走（コスト $0）

    python scripts/agent.py --config scripts/config/cs_triage.yaml --print-graph
        # Mermaid グラフを出力して終了
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

# scripts/ をパッケージ親として import 解決
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from src.assemble import assemble_node
from src.classify import classify_node
from src.config import load_config
from src.cost import reset_usage, snapshot
from src.db_client import clear_tool_cache, load_fixtures
from src.draft import draft_node
from src.extract import extract_node
from src.logger import logger, setup_logger
from src.preprocess import preprocess_node
from src.reflect import reflect_node
from src.retrieve import retrieve_node
from src.state import TriageState


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------
def route_after_preprocess(state: TriageState) -> str:
    """英語メールは extract をスキップして英語転送テンプレに直行。"""
    return "transfer_en" if state.get("lang") == "en" else "extract"


def route_after_reflect(state: TriageState) -> str:
    """reflect が NG かつ未リトライなら draft に戻る。それ以外は assemble へ。"""
    cfg_max_iter = state.get("cfg", {}).get("reflection", {}).get("max_iterations", 1)
    if state.get("reflect_pass"):
        return "assemble"
    if state.get("reflect_iter", 0) >= cfg_max_iter:
        return "assemble"
    return "draft"


def transfer_en_node(state: TriageState) -> dict:
    """英語問い合わせ → 英語担当へ転送するテンプレ応答。

    v2: 本文は英語転送テンプレのまま。ただし extract（型番 / 注文番号）は
    英語本文にも適用し、内部メモには日本語で「型番・希望納期・推定意図」を残して
    英語担当オペが受け取りやすくする。
    """
    from src.extract import extract_order_nos, extract_skus

    cfg = state.get("cfg", {})
    tpl = cfg.get("templates", {}) or {}
    body = tpl.get(
        "english_transfer",
        "Thank you for your inquiry. We will transfer your request to our English support team.",
    )

    # v2: 英語パスでも regex で型番 / 注文番号を抽出して内部メモへ
    masked_text = state.get("masked_text", "")
    skus = extract_skus(masked_text, cfg.get("sku_patterns", []) or [])
    order_nos = extract_order_nos(masked_text, cfg.get("order_no_patterns", []) or [])

    memo_parts = ["[v2] English inquiry — 英語担当へ転送する旨のテンプレを返答。"]
    if skus:
        memo_parts.append(f"抽出型番: {', '.join(skus)}")
    if order_nos:
        memo_parts.append(f"抽出注文番号: {', '.join(order_nos)}")
    if not skus and not order_nos:
        memo_parts.append("型番・注文番号の抽出なし。")
    memo_parts.append("元メール本文を英語担当に共有してください。")

    logger.info("[en] transfer template applied skus=%s order_nos=%s", skus, order_nos)
    return {
        "category": "other",
        "urgency": "normal",
        "complaint_smell": False,
        "classify_confidence": 1.0,
        "extract_confidence": 1.0 if (skus or order_nos) else 0.5,
        "skus": skus,
        "order_nos": order_nos,
        "retrieved_data": {},
        "retrieve_errors": [],
        "draft_body": body,
        "internal_memo": " / ".join(memo_parts),
        "missing_info": [],
        "reflect_pass": True,
        "reflect_issues": [],
        "reflect_iter": 0,
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(TriageState)
    g.add_node("preprocess", preprocess_node)
    g.add_node("transfer_en", transfer_en_node)
    g.add_node("extract", extract_node)
    g.add_node("classify", classify_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("draft", draft_node)
    g.add_node("reflect", reflect_node)
    g.add_node("assemble", assemble_node)

    g.set_entry_point("preprocess")
    g.add_conditional_edges(
        "preprocess",
        route_after_preprocess,
        {"transfer_en": "transfer_en", "extract": "extract"},
    )
    g.add_edge("transfer_en", "assemble")
    g.add_edge("extract", "classify")
    g.add_edge("classify", "retrieve")
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", "reflect")
    g.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"draft": "draft", "assemble": "assemble"},
    )
    g.add_edge("assemble", END)
    return g.compile()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="CS Triage Agent v1")
    ap.add_argument("--config", required=True, type=Path,
                    help="メイン設定 YAML（cs_triage.yaml）")
    ap.add_argument("--input", type=Path,
                    help="メール本文のテキストファイル。未指定時は stdin から読む")
    ap.add_argument("--case-id", default="CASE-LOCAL-0000",
                    help="Salesforce ケース ID 相当（ログ・出力に付与）")
    ap.add_argument("--customer-id", default=None,
                    help="取引先コード（lookup_price 用、任意）")
    ap.add_argument("--dry-run", action="store_true",
                    help="LLM を呼ばずルールベースで完走させる（CI / 動作確認用）")
    ap.add_argument("--lite", action="store_true",
                    help="軽量モード（draft / reflect で Haiku を使う）")
    ap.add_argument("--mock-db", type=Path,
                    default=_THIS_DIR / "eval" / "mock_db",
                    help="モック DB フィクスチャのディレクトリ")
    ap.add_argument("--output", type=Path,
                    default=_THIS_DIR / "output",
                    help="final_output JSON の出力先ディレクトリ")
    ap.add_argument("--print-graph", action="store_true",
                    help="Mermaid グラフ図を出力して終了")
    args = ap.parse_args()

    cfg = load_config(args.config)
    app = build_graph()

    if args.print_graph:
        print(app.get_graph().draw_mermaid())
        return

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY が未設定。--dry-run なら不要", file=sys.stderr)
        sys.exit(1)

    log_dir = _THIS_DIR / "logs"
    setup_logger(slug=cfg.get("slug", "cs_triage"), log_dir=log_dir)

    if args.input:
        raw_text = args.input.read_text(encoding="utf-8")
    else:
        raw_text = sys.stdin.read()
    if not raw_text.strip():
        print("ERROR: 入力本文が空です", file=sys.stderr)
        sys.exit(1)

    load_fixtures(args.mock_db)
    clear_tool_cache()
    reset_usage()

    initial: TriageState = {
        "raw_text": raw_text,
        "case_id": args.case_id,
        "customer_id": args.customer_id or "",
        "cfg": cfg,
        "dry_run": args.dry_run,
        "lite_mode": args.lite,
    }

    logger.info("=== agent start === case=%s dry_run=%s lite=%s",
                args.case_id, args.dry_run, args.lite)
    final_state = app.invoke(initial)

    cost = snapshot()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"final_{args.case_id}.json"
    out_path.write_text(
        json.dumps(final_state.get("final_output", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("=== done === output=%s cost=$%.4f (in=%d / out=%d tokens)",
                out_path, cost["cost_usd"], cost["input_tokens"], cost["output_tokens"])
    print(json.dumps(final_state.get("final_output", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
