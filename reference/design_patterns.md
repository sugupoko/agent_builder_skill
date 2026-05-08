# LLM エージェント設計パターン

> 出典: [OpenBridge LLMエージェントのデザインパターン](https://www.openbridge.jp/column/llm-agent-design-patterns)

4つのパターンを使い分け・組み合わせるのが実務の王道。

---

## 1. Reflection（リフレクション）

LLM が自分の出力を再評価して改善するループ。

### 向く場面
- 高精度が必要なタスク（長文の整合性、論理的推論）
- 出力の品質が成果に直結する文書生成

### 最小実装
```
初回応答 → 自己分析 → 改善版生成 → (繰り返し or 終了判定)
```

### 落とし穴
- リフレクション回数制限なしだとコスト爆発
- 「無限の改善」になり収束しない
- 自分で生成した間違いを自分で気づくのは限界がある（外部評価との組み合わせ推奨）

### 実装メモ
```python
for i in range(MAX_REFLECTIONS):  # 必ず上限を切る
    output = llm.invoke(prompt + previous_output)
    critique = llm.invoke(f"以下の出力の問題点を3点まで: {output}")
    if "問題なし" in critique:
        break
    previous_output = output + f"\n\n改善指示: {critique}"
```

---

## 2. Tool Use（ツール利用）

LLM が外部ツール（API/DB/ファイル）を呼んで情報や能力を拡張する。

### 向く場面
- 外部データアクセスが必要（最新ニュース、社内文書、計算）
- 実操作（メール送信、ファイル作成）が含まれる
- LLM 単体では不可能な処理

### 最小実装の4層
1. **ツール定義**: 関数 + docstring（LLM はこれを読む）
2. **呼び出し仕組み**: LLM の出力から tool_call を抽出
3. **実行エンジン**: 実際に関数を呼ぶ
4. **結果統合**: 返り値を LLM の次のターンに渡す

LangGraph なら `create_react_agent(llm, tools=[...])` が全部やってくれる。

### 落とし穴
- **権限管理不備**: ツールが過剰な権限を持つとセキュリティ事故
- **エラー処理不足**: ツール失敗時のフォールバックなし
- **無限ループ**: 同じツールを延々と呼び続ける（recursion_limit + プロンプトで制約）

### 実装の鉄則
- **最小権限**: 必要なリソースだけアクセスできるツールに分割
- **キャッシュ**: 同一引数の2回叩きを防ぐ（`tools_pattern.md` 参照）
- **エラー時は失敗を返す**: 黙って成功扱いにしない

---

## 3. Planning（プランニング）

LLM がゴールから逆算してサブタスクを抽出・順序付けして実行する。

### 向く場面
- 多段階タスク（要件定義→設計→コード生成→テスト）
- 順序判断が価値になる業務
- 長期タスクで「次に何するか」を決め続けたい場合

### 最小実装
```
ゴール理解 → サブタスク抽出 → 順序決定 → 段階実行 → 結果集約
```

### 落とし穴
- **計画の過剰詳細化**: 50ステップの計画を作って途中で破綻する
- **修正困難**: 途中で気づいた前提間違いを反映しにくい
- **計画と実行の乖離**: 計画通りに行かないと止まる

### 実装メモ
- 計画は **3〜5ステップに抑える**
- 各ステップ実行後に「次は変更が必要か」を再評価
- 失敗時は計画を作り直す（再計画ロジック）

---

## 4. Multi-Agent Collaboration（マルチエージェント協調）

複数のエージェントが異なる役割で協力する。

### 向く場面
- 異なる専門性が必要（Planner / Coder / Reviewer / Manager）
- 役割分担で品質が上がる業務
- Sakana AI の ShinkaEvolve が示した「diverse expert personas + critical peer review + final synthesis」の3段構成は応用範囲が広い

### 最小実装
- **3段構成（推奨）**: Planner（計画） → Worker（実行） → Reviewer（評価）
- **役割別 LLM**: 各エージェントに別のシステムプロンプト
- **対話メカニズム**: 共通のメッセージバス or 直接渡し

詳細実装は `reference/multi_agent_skeleton.py` 参照。

### 落とし穴
- **過剰な対話**: エージェント同士で延々と議論してコスト増
- **調整コスト**: メッセージ受け渡しのオーバーヘッド
- **エラー伝播**: 1エージェントの誤りが全体を引きずる
- **デバッグ困難**: どのエージェントの判断で結果が変わったか追えない

### 実装の鉄則
- **エージェント数は3〜4が目安**: それ以上は調整困難
- **各エージェントは独立に評価可能に**: 個別テストできる構造
- **合議のラウンド数を制限**: 最大2〜3往復

---

## どう選ぶか — 決定木

```
Q1. LLM 単体で結果が出るか？
  YES → 単純なプロンプト1回で OK（パターン不要）
  NO  → Q2

Q2. 外部リソースアクセスが必要？
  YES → Tool Use を採用
  NO  → Q3

Q3. 出力品質を上げたい？
  YES → Reflection を組み合わせ（コスト注意）

Q4. 多段階のサブタスクがある？
  YES → Planning を組み合わせ（計画は3〜5ステップに抑制）

Q5. 異なる専門性が必要？
  YES → Multi-Agent を組み合わせ（最後の手段、コスト最大）
  NO  → ここまでの組み合わせで完結
```

---

## ハイブリッド構成が実務の王道

実務では「どれか1つ」ではなく、**複数を組み合わせる**:

```
ワークフロー型（決定論コード）の中で:
  - データ取得は決定論コードで
  - 一部のノードで Tool Use
  - 重要な出力ノードで Reflection
  - 探索が必要な場面だけ Multi-Agent
```

LayerX の実装も「基本ワークフロー型 + 一部エージェント」のパターンが多い。

---

## 採用するパターン × ノードの組み合わせ例

リサーチ系エージェントの典型:

| ノード | パターン |
|---|---|
| fetch | （パターン不要、決定論コード） |
| supplement | Tool Use（search/fetch_url） |
| summarize | （単純プロンプト1回） |
| deep_dive | Tool Use + 軽い Planning（5回まで調査） |
| compose_mail | （決定論コード） |
| send | （決定論コード） |

レビュー系エージェントの典型:

| ノード | パターン |
|---|---|
| intake | （決定論コード） |
| classify | （単純プロンプト1回） |
| extract_rules | Tool Use（社内ルール DB） |
| judge | Reflection（精度重視） |
| format | （決定論コード） |
| send | （決定論コード） |

---

## 2026 年標準: LangGraph + DSPy のハイブリッド

業界での主流パターン:

| 層 | フレームワーク | 役割 |
|---|---|---|
| Orchestration | **LangGraph** | graph 構造、状態管理、条件 edge、checkpoint |
| Prompt 最適化 | **DSPy** | typed signature → compiler が prompt 自動最適化 |
| Evaluation | LangSmith / RAGAS / 自作 LLM Judge | 品質・コスト・回帰検出 |
| Long-running | Temporal | 耐障害ワークフロー (>30秒タスク用) |

DSPy は人手プロンプトの「肥大化の罠」(AGENT_MINDSET.md 参照) を構造的に回避できる。
評価データから勾配的に最適化するため、追加で品質向上が狙える場面で有効。

→ 詳細は `agent-evolve` スキル §Approach 4.5、長時間タスクは `reference/long_running_pattern.md`。

---

## さらに学ぶ

- [reference/workflow_vs_agent.md](workflow_vs_agent.md) — ワークフロー vs エージェントの境界
- [reference/multi_agent_skeleton.py](multi_agent_skeleton.py) — 3段構成のコード
- [reference/long_running_pattern.md](long_running_pattern.md) — Temporal + LangGraph の二層アーキ
- [Sakana AI ShinkaEvolve](https://sakana.ai/shinka-evolve/) — 進化的にエージェント設計を発見した事例
- [DSPy (Stanford)](https://dspy.ai/) — programmatic prompt 最適化
