# Workflow vs Agent — ハイブリッド構成戦略

> Anthropic の "Building effective agents" の言葉を借りれば:
> - **Workflow**: LLMs and tools are **orchestrated through predefined code paths**
> - **Agent**: LLMs **dynamically direct their own processes and tool usage**

実務で安定して動かすなら **基本ワークフロー型 + 一部エージェント化** が王道。

---

## ワークフロー型の特徴

```python
# 決定論的に処理が流れる
def workflow():
    data = fetch()              # コード
    filtered = filter(data)     # コード
    summary = llm.summarize(filtered)  # LLM 1回
    save(summary)               # コード
```

### 強み
- **予測可能**: 同じ入力で同じパス
- **デバッグ容易**: どのステップで何が起きたか追える
- **コスト管理**: LLM 呼び出し回数が固定
- **テスト容易**: 各ステップを単独テスト可能

### 弱み
- **柔軟性が低い**: 想定外の入力に対応しにくい
- **複雑な判断が苦手**: 多分岐の業務には向かない

---

## エージェント型の特徴

```python
# LLM が次に何をするか決める
def agent():
    while not done:
        action = llm.decide_next_action()
        result = execute(action)
        if llm.is_done(result):
            done = True
```

### 強み
- **柔軟性**: 想定外の状況にも対応
- **複雑な判断**: 動的に戦略を変えられる

### 弱み
- **予測不能**: 同じ入力で違うパスを通る
- **デバッグ困難**: どの判断で結果が変わったか追えない
- **コスト爆発**: LLM 呼び出し回数が動的に増える
- **無限ループリスク**: 終了判断を誤ると暴走

---

## 「決定論コード vs LLM」の振り分け

実装する各処理について、以下のフローチャートで判定:

```
Q1. その処理は規則で書ける？
  YES → 決定論コード
  NO  → Q2

Q2. その処理に「言語の意味理解」が必要？
  YES → LLM
  NO  → Q3

Q3. その処理に「経験的判断」が必要？
  YES → LLM (ペルソナ注入)
  NO  → 決定論コード
```

### 振り分け例

| 処理 | 規則化 | 言語意味 | 経験判断 | → 結論 |
|---|---|---|---|---|
| RSS から取得 | ○ | × | × | コード |
| URL 重複排除 | ○ | × | × | コード |
| 公開日でソート | ○ | × | × | コード |
| カテゴリ分類（事前定義） | ○ | × | × | コード |
| カテゴリ分類（曖昧） | × | ○ | × | LLM |
| 「重要度」のスコアリング | △ | ○ | ○ | LLM |
| 自然な日本語要約 | × | ○ | ○ | LLM |
| 編集者の見立て生成 | × | ○ | ○ | LLM |
| 出典 [N] 番号の抽出 | ○ | × | × | コード |
| Markdown 整形 | ○ | × | × | コード |
| メール送信 | ○ | × | × | コード |

---

## ハイブリッド構成の設計手順

### Step 1: 業務プロセスを 5〜7 ステップに分解

```
[Input] → A → B → C → D → E → [Output]
```

### Step 2: 各ステップを「コード」「LLM」に振り分け

```
[Input] → A(コード) → B(コード) → C(LLM) → D(コード) → E(コード) → [Output]
```

### Step 3: LLM ステップで「単純プロンプト」か「エージェント」か判定

```
C: 単純な要約 → 単純プロンプト1回（ワークフロー型のまま）
C: 多段の探索が必要 → ReAct エージェント（このステップだけエージェント化）
```

### Step 4: 全体は LangGraph の StateGraph で組む

```python
g = StateGraph(WorkflowState)
g.add_node("A", node_a)         # コード
g.add_node("B", node_b)         # コード
g.add_node("C", node_c)         # LLM 1回 or ReAct
g.add_node("D", node_d)         # コード
g.add_node("E", node_e)         # コード
g.add_edge(START, "A")
g.add_edge("A", "B")
...
```

---

## 「全部エージェント化」の罠

ありがちな失敗パターン:

```python
# ❌ 何でも LLM に任せる
def bad_design():
    instructions = "ニュースを集めて要約してメール送信して"
    result = mega_agent.run(instructions, tools=[fetch, summarize, send])
```

問題:
- どこで何が起きたか追えない
- コストが10〜100倍
- ツール呼び出し順序が毎回違う → 再現性ゼロ
- 失敗時のリトライ設計が不可能

---

## ハイブリッドの正解

```python
# ✓ 決定論ワークフロー + 一部エージェント
def good_design():
    # 決定論
    data = fetch_node(state)
    
    # 一部エージェント化（多段検索が必要なステップだけ）
    enriched = supplement_react_node(data, state)
    
    # 単純プロンプト1回
    summary = summarize_node(enriched, state)
    
    # 決定論
    body = compose_mail_node(summary, state)
    send_node(body, state)
```

---

## エージェント化を採用する判断基準

各ノードで「これはエージェント化が必要か？」を以下でチェック:

- [ ] 入力に応じて使うツールが変わる？
- [ ] ツール呼び出しの順序が動的に決まる？
- [ ] ツール結果を見て次のツールを選ぶ必要がある？

3つすべて YES → エージェント化（ReAct）が妥当。
2つ以下 → 単純プロンプト or ツール使用パターンで OK。

---

## ハイブリッドの典型例（news_searcher）

```
fetch → supplement → summarize → enrich → deep_dive → compose_mail → send

  ↑       ↑           ↑         ↑         ↑           ↑              ↑
  コード   ReAct       LLM 1回   コード    ReAct       コード         コード
          (補強検索)             (集計)   (背景調査)  (整形)
```

7ノード中、エージェント化は **2つだけ**（supplement, deep_dive）。
それ以外はコード or 単純な LLM 呼び出し。

これでコストと品質のバランスが取れる。

---

## まとめ

| 観点 | 推奨 |
|---|---|
| 全体構造 | ワークフロー型（LangGraph StateGraph） |
| LLM 使用箇所 | 「言語意味 or 経験判断」が必要な少数のノード |
| エージェント化 | 「動的に使うツールが変わる」ノードのみ |
| 採用パターン | Tool Use を中心に、必要なら Reflection / Planning |
| Multi-Agent | 最後の手段、コスト最大、デバッグ最難 |

「LLM に自由を与える」ことが目的ではなく、「**LLM が活きる場所を見つけて、それ以外は決定論コードで縛る**」のが実務エンジニアリング。
