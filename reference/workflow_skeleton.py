"""LangGraph ワークフロー雛形（決定論ノード + 1〜2 LLM ノード）。

このファイルをコピーして scripts/agent.py の起点にする。
プレースホルダ（TODO コメント）を自分のロジックに置き換える。

設計の原則:
    - 収集・整形・並び替えは決定論コードで
    - LLM は要約・判断・深掘りに限定
    - WorkflowState (TypedDict) には流す全フィールドを宣言する
    - ロガー1実行1ファイル、コスト計測も入れる

dry-run モードのパターン (推奨: 「LLM ノードで早期 return」型):
    state["dry_run"] が True のとき、LLM ノードは LLM を呼ばずに
    ルールベースまたはテンプレ応答で代替する。利点:
      - 評価インフラ動作確認が無料 ($0)
      - CI で全パイプラインを毎コミット走らせられる
      - 本番経路と分岐経路が同じ関数内に並ぶので保守しやすい
    モック LLM クラスを噛ませる方法もあるが、実プロジェクト経験では
    各ノードに dry-run 分岐を直接書く方が見通しが良い (cs_triage_agent v1
    実装で当初 _DryRunLLM クラスを書いたが冗長と判明し削除した経緯あり)。

    例:
        def llm_node(state):
            if state.get("dry_run"):
                # ルールベースで仮応答
                return {"output": rule_based_fallback(state)}
            llm = ChatAnthropic(...)
            resp = llm.invoke(...)
            return {"output": resp.content}
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import yaml
from dotenv import load_dotenv

load_dotenv()

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# State definition — 流す全フィールドをここに書く（漏れに注意）
# ---------------------------------------------------------------------------
class WorkflowState(TypedDict, total=False):
    cfg: dict
    items: list
    summary: str
    output_path: str


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------
RATE_INPUT = 3.00 / 1_000_000   # USD per token (Sonnet 4.6 公開レート)
RATE_OUTPUT = 15.00 / 1_000_000

USAGE = {"input_tokens": 0, "output_tokens": 0}


def reset_usage():
    for k in USAGE:
        USAGE[k] = 0


def accumulate_usage(messages):
    for m in messages:
        meta = getattr(m, "usage_metadata", None) or {}
        USAGE["input_tokens"] += int(meta.get("input_tokens", 0) or 0)
        USAGE["output_tokens"] += int(meta.get("output_tokens", 0) or 0)


def compute_cost() -> dict:
    cost = USAGE["input_tokens"] * RATE_INPUT + USAGE["output_tokens"] * RATE_OUTPUT
    return {**USAGE, "cost_usd": cost}


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
def setup_logger(slug: str) -> Path:
    Path("logs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = Path("logs") / f"agent_{slug}_{ts}.log"
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return log_path


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def fetch_node(state: WorkflowState) -> dict:
    """[決定論コード] 入力ソースからデータを取得して整形する。

    TODO: 自分の入力ソースに合わせて実装。
        - API 呼び出し、ファイル読み込み、DB クエリ等
        - 取得結果を items リストとして返す
    """
    logger.info("[1/3] fetch")
    cfg = state["cfg"]
    items = []
    # TODO: items = collect_from_sources(cfg["sources"])
    # TODO: items = dedup(items)
    # TODO: items = rerank_by_relevance(items, cfg)
    logger.info("  => %d 件取得", len(items))
    return {"items": items}


def summarize_node(state: WorkflowState) -> dict:
    """[LLM] 取得データを要約する。ペルソナをプロンプトに注入する。"""
    logger.info("[2/3] summarize (LLM)")
    cfg = state["cfg"]
    items = state.get("items", [])

    llm = ChatAnthropic(model=MODEL, max_tokens=2500)

    editorial = cfg.get("editorial", {}) or {}
    persona = editorial.get("persona", "")
    perspective = editorial.get("perspective", "")
    rules = editorial.get("rules", "")

    bullets = []
    for idx, it in enumerate(items[:60], start=1):
        # TODO: 自分の Item 構造に合わせて整形
        bullets.append(f"[{idx}] {it}")

    prompt = f"""次は今週のデータです。以下の観点で要約してください。

ペルソナ:
{persona}

視点:
{perspective}

執筆ルール:
{rules}

データ:
{chr(10).join(bullets)}

出力: Markdown 箇条書き4〜6項目。各項目末尾に根拠ID `[N]` を付ける。"""

    resp = llm.invoke([
        SystemMessage(content="あなたは社内向け編集者です。"),
        HumanMessage(content=prompt),
    ])
    accumulate_usage([resp])
    summary = resp.content if isinstance(resp.content, str) else str(resp.content)
    logger.info("  => 生成完了 (%d 文字)", len(summary))
    return {"summary": summary.strip()}


def send_node(state: WorkflowState) -> dict:
    """[決定論コード] 出力をファイルに書き出す（または配信する）。"""
    logger.info("[3/3] send")
    slug = state["cfg"].get("slug", "agent")
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"output_{slug}_{ts}.md"
    body = f"# 出力\n\n{state.get('summary', '(empty)')}\n"
    path.write_text(body, encoding="utf-8")
    logger.info("  => %s", path)
    return {"output_path": str(path)}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(WorkflowState)
    g.add_node("fetch", fetch_node)
    g.add_node("summarize", summarize_node)
    g.add_node("send", send_node)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "summarize")
    g.add_edge("summarize", "send")
    g.add_edge("send", END)
    return g.compile()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--print-graph", action="store_true",
                    help="グラフ構造を Mermaid で出力して終了")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY が未設定", file=sys.stderr)
        sys.exit(1)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    app = build_graph()

    if args.print_graph:
        print(app.get_graph().draw_mermaid())
        return

    slug = cfg.get("slug", args.config.stem)
    log_path = setup_logger(slug)
    reset_usage()

    logger.info("=== agent start ===")
    logger.info("config: %s / model: %s / log: %s", args.config, MODEL, log_path)

    final = app.invoke({"cfg": cfg})

    cost = compute_cost()
    logger.info("=== done === output: %s", final.get("output_path"))
    logger.info("💰 input=%d output=%d cost=$%.4f",
                cost["input_tokens"], cost["output_tokens"], cost["cost_usd"])


if __name__ == "__main__":
    main()
