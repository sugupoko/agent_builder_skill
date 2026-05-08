---
name: agent-prototype
description: agent-decompose の後に呼び出すスキル。design.md を読み、reference/ の雛形コード（workflow_skeleton.py / react_skeleton.py / multi_agent_skeleton.py）を起点にエージェントの最小実装を作る。LangGraph の StateGraph、ReAct エージェント、ツール定義を組み合わせて scripts/ に動くコードを出力する。「最初の動くもの」を1日以内に作るのが目標。
---

# agent-prototype — 雛形からの最小実装

## いつ使うか

- `/agent-decompose` で `design.md` `tools.md` が完成した直後
- 「最初の動くもの」を作りたい

## 入力

- `workspace/<project>/vN/design.md`
- `workspace/<project>/vN/tools.md`
- `workspace/<project>/vN/spec.md`

## 出力

| ファイル | 内容 |
|---|---|
| `scripts/agent.py` | エージェント本体（LangGraph ワークフロー） |
| `scripts/tools.py` | ReAct ツール群（必要な場合） |
| `scripts/config.yaml` | 設定ファイル（テーマ・キーワード・editorial 等） |
| `scripts/run.sh` | 実行用シェルスクリプト |
| `reports/prototype_report.md` | 何を実装したか・テスト結果・既知の課題 |

## 進め方

### Step 1: 雛形の選択

design.md の構造を見て、`reference/` から起点となる雛形を選ぶ:

| design.md の構造 | 使う雛形 |
|---|---|
| 直線的な5〜7ノード（fetch→...→send） | `workflow_skeleton.py` |
| ノードの中で多段検索が必要 | `react_skeleton.py` を組み込み |
| Planner + Reviewer + Synthesizer の協調 | `multi_agent_skeleton.py` |

雛形をそのままコピーし、プロジェクト固有部分を埋める方式（**LayerX 流の写経起点**）。

### Step 2: WorkflowState の定義

design.md §4「データフロー」を見て、ノード間で受け渡す全フィールドを `WorkflowState` (TypedDict) に書く。

```python
class WorkflowState(TypedDict, total=False):
    cfg: dict
    items: list
    summary: str
    # ... design.md に書いた全フィールド
```

**注意**: 宣言外のフィールドは LangGraph に silent drop される。漏れなく書く。

### Step 3: 各ノードの実装

design.md のノード一覧に従って関数化。「LLM/コード」境界を守る:

```python
def fetch_node(state: WorkflowState) -> dict:
    """決定論的: API/RSS から取得して整形"""
    items = collect_from_sources(state["cfg"])
    items = dedup(items)
    items = rerank_by_relevance(items, state["cfg"])
    return {"items": items}

def summarize_node(state: WorkflowState) -> dict:
    """LLM: ペルソナを注入して構造化生成"""
    llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=2500)
    prompt = build_prompt_with_persona(state)
    resp = llm.invoke([SystemMessage(...), HumanMessage(content=prompt)])
    return parse_sections(resp.content)
```

### Step 4: ReAct エージェントの組み込み（必要な場合）

design.md でエージェント化したノードがあれば、`react_skeleton.py` のパターンで:

```python
from langgraph.prebuilt import create_react_agent

def deep_dive_node(state):
    agent = create_react_agent(llm, tools=[search, fetch_url, ...],
                                prompt="あなたは...")
    result = agent.invoke({"messages": [...]},
                          config={"recursion_limit": 40})
    return {"deep_dives": parse(result)}
```

**忘れずに**:
- `recursion_limit` を明示
- プロンプトで「ツール呼び出しは最大5回」と制約
- ツールにキャッシュを入れる（`tools_pattern.md`）

### Step 5: 設定の YAML 化

scripts/config.yaml にすべての可変パラメータを切り出す:

```yaml
theme: ...
sources: [...]
editorial:
  persona: |
    spec.md §4 をコピー
  perspective: |
    spec.md §5 をコピー
  rules: |
    spec.md §6 をコピー
sections:
  ...
```

業務担当者が触れるレベルまで切り出すこと。

### Step 6: コスト計測の組み込み

`reference/cost_management.md` を参照し、`USAGE` グローバル + `accumulate_usage(messages)` でトークン数を記録。実行末尾で概算コストをログに出す。

### Step 7: ロガーの設定

実行ごとに `logs/agent_<slug>_<timestamp>.log` に1ファイル残す。
各ノードの開始・終了・件数・エラーをログに記録。

### Step 8: 動作確認

- スキー設定だけで実行できる軽量モード（`--no-deep-dive` など）から動作確認
- 1〜2件の小規模データで動くことを確認してから本番データへ

### Step 9: prototype_report.md の生成

```markdown
# Prototype Report v1

## 実装したノード
（design.md のどれを実装したか）

## 動作確認結果
- 軽量モード: ✓ / ✗
- フルモード: ✓ / ✗
- コスト実測: $X.XX

## 既知の課題
（次の `/agent-eval` で潰すべき項目）

## 動作させるコマンド
\`\`\`
python scripts/agent.py --config scripts/config.yaml
\`\`\`
```

## 雛形からの実装パターン（LayerX 流の写経）

LayerX のブログでも言及されている進め方:

1. **段階1**: 雛形をそのままコピーして動かす（中身を完璧に理解しなくてよい）
2. **段階2**: 1ノードずつ自分のロジックに置き換える
3. **段階3**: ツール定義を整える、エラー処理を足す

完璧理解より早く回す。動くものを作って評価サイクルへ。

## このスキルが「やらない」こと

- 評価データセットでの精度測定（`/agent-eval` の仕事）
- 配信先実装・cron 設定（`/agent-deploy` の仕事）

## 次のステップ

```
/agent-eval
```

評価データセットで精度・コスト・所要時間を測り、改善案を出す。
