"""ReAct エージェント雛形。

ワークフロー内のあるノード（深掘り・補強検索など）で、LLM が複数ツールを使って
段階的に情報を集める必要があるときに使う。

設計の原則:
    - ツールは最小権限・キャッシュ付き
    - プロンプトで「ツール呼び出し最大N回」を明示
    - recursion_limit を必ず設定
    - 最終出力はクリーンに（前置き・後付けは strip_preamble で除去）
    - 全メッセージのトークン使用量を集計
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# 1. Tool definitions（最小権限・キャッシュ付き）
# ---------------------------------------------------------------------------
_CACHE: dict = {}


@tool
def example_search(query: str, max_items: int = 5) -> str:
    """外部API/RSS等から情報を検索する。同じクエリは2回叩かない（キャッシュ）。

    Args:
        query: 検索クエリ
        max_items: 取得件数 (1〜10)
    """
    n = max(1, min(10, max_items))
    key = ("search", query.strip().lower(), n)
    if key in _CACHE:
        return _CACHE[key] + "\n\n_(cache hit)_"

    # TODO: 実際の API 呼び出しに置き換える
    result = f"(stub: {query}, {n} 件)"

    _CACHE[key] = result
    return result


@tool
def example_fetch_url(url: str, max_chars: int = 1500) -> str:
    """URL から本文テキストを取得する（最大3000文字）。同じURLは2回叩かない。

    Args:
        url: 取得したいURL
        max_chars: 返却する最大文字数（デフォルト1500、最大3000）
    """
    n = max(200, min(3000, max_chars))
    if url in _CACHE:
        return _CACHE[url][:n] + "\n\n_(cache hit)_"

    # TODO: requests.get + HTML タグ除去等を実装
    result = f"(stub: fetched {url})"

    _CACHE[url] = result
    return result[:n]


def clear_tool_cache():
    _CACHE.clear()


# ---------------------------------------------------------------------------
# 2. Postprocess: ReAct 出力のクリーンアップ
# ---------------------------------------------------------------------------
_PREAMBLE_PATTERNS = [
    r"^これで十分な情報[^\n]*\n+",
    r"^以下に(?:調査結果|まとめ|結果|レポート)[^\n]*\n+",
    r"^これまでに収集した情報[^\n]*\n+",
    r"^最終(?:レポート|出力)を(?:作成|構成|まとめ)[^\n]*\n+",
    r"^以上の(?:調査結果|情報)を[^\n]*\n+",
    r"^調査結果を整理[^\n]*\n+",
]


def strip_preamble(text: str) -> str:
    """ReAct の最終応答にありがちな前置きを除去。

    プロンプトで「前置き禁止」と書いても LLM はしばしば書いてしまうので、
    後処理で除く二段構え。
    """
    if not text:
        return text
    cleaned = text.strip()
    for _ in range(5):
        before = cleaned
        for pat in _PREAMBLE_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        if cleaned == before:
            break
    return cleaned


def extract_text(content) -> str:
    """AIMessage.content は str か list[dict] のことがある。テキスト部分のみ取り出す。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# 3. ReAct invocation
# ---------------------------------------------------------------------------
def run_react_node(
    user_msg: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4000,
    enable_thinking: bool = False,
    recursion_limit: int = 40,
    accumulate_usage_fn=None,
) -> dict:
    """ReAct エージェントを1回起動して結果を返す。

    Args:
        user_msg: ユーザープロンプト
        model: 使うモデル
        max_tokens: LLM 出力トークン上限
        enable_thinking: Extended thinking を有効化（コスト増、推論過程をログに残す）
        recursion_limit: LangGraph の最大ステップ数
        accumulate_usage_fn: usage_metadata を集計する関数

    Returns:
        {"text": <最終応答>, "tool_calls": <呼び出し回数>, "messages": [...]}
    """
    if enable_thinking:
        llm = ChatAnthropic(
            model=model, max_tokens=max(max_tokens, 8000), temperature=1,
            thinking={"type": "enabled", "budget_tokens": 4000},
        )
    else:
        llm = ChatAnthropic(model=model, max_tokens=max_tokens)

    # ツールリストはここに追加
    tools = [example_search, example_fetch_url]

    agent = create_react_agent(
        llm, tools,
        prompt=(
            "あなたは○○領域のリサーチアナリストです。"
            "与えられたタスクについて、ツールを使って**最大5回まで**調査し、"
            "最後に Markdown で結果をまとめてください。\n\n"
            "**ツール使用ルール**:\n"
            "- 同一クエリ・URL を繰り返し叩かない（キャッシュにヒットして無駄）\n"
            "- 5回呼び出した時点で必ず最終出力を返す\n\n"
            "**最終出力ルール**:\n"
            "- 「以下にまとめます」等の前置きや「以上です」の後付けを書かない\n"
            "- 一文目は見出し（### 〜）から始める\n"
            "- 推測しない。ツールから得た情報のみ使う\n"
            "- 情報源は必ず URL を明記する"
        ),
    )

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=user_msg)]},
            config={"recursion_limit": recursion_limit},
        )
    except Exception as e:
        logger.exception("ReAct失敗: %s", e)
        return {"text": f"(失敗: {e})", "tool_calls": 0, "messages": []}

    if accumulate_usage_fn:
        accumulate_usage_fn(result.get("messages", []))

    tool_calls = 0
    for m in result["messages"]:
        tcs = getattr(m, "tool_calls", None) or []
        tool_calls += len(tcs)

    final = extract_text(result["messages"][-1].content)
    final = strip_preamble(final)
    return {
        "text": final,
        "tool_calls": tool_calls,
        "messages": result["messages"],
    }


# ---------------------------------------------------------------------------
# 4. Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY が未設定", flush=True)
        exit(1)

    logging.basicConfig(level=logging.INFO)
    out = run_react_node("○○について最新情報を調べて要約してください。")
    print(out["text"])
    print(f"\nツール呼び出し回数: {out['tool_calls']}")
