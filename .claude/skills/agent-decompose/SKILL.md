---
name: agent-decompose
description: agent-discover の後に呼び出すスキル。spec.md を読んで業務プロセスをノードに分割し、各ノードを「決定論コード」と「LLM 判断」に振り分け、設計パターン（Reflection / Tool Use / Planning / Multi-Agent）を選定する。LangGraph のグラフ構造、ReAct ツール定義、ハイブリッド構成を design.md として出力する。「実装の設計をしたい」「ノードの切り方が分からない」「パターンの選び方を知りたい」と言われたとき呼ぶ。
---

# agent-decompose — 業務分解 + 設計パターン選定

## いつ使うか

- `/agent-discover` で `spec.md` が完成した直後
- 既存設計を見直したい / ノード構成を変えたい
- 「設計が複雑になりすぎている」と感じたとき

## 入力

`workspace/<project>/vN/spec.md`

## 出力

| ファイル | 内容 |
|---|---|
| `design.md` | グラフ構造・ノード一覧・各ノードの責務・LLM/コード境界・設計パターン選定理由 |
| `tools.md` | ReAct で使うツール一覧と各ツールの仕様（入出力・エラー時の挙動・最小権限） |
| **`detailed_design.md`** | **詳細設計書 (シーケンス図・状態遷移図・データモデル等を Mermaid で網羅)** ← `reference/detailed_design_template.md` を雛形に |
| `spec.md` | 必要があれば更新（決定したパラメータを反映） |
| `reports/decompose_report.md` | 作業報告（選んだ理由・捨てた選択肢） |

## 進め方

### Step 1: 業務プロセスをノードに分割

spec.md の「2. 業務ステップ」を読み、各ステップを LangGraph のノードに対応させる。

リサーチ系の典型例:
```
fetch → preprocess → enrich (コード) → summarize (LLM) → review → send
```

レビュー系の典型例:
```
intake → classify (LLM) → extract_rules (コード) → judge (LLM) → format → send
```

抽出系の典型例:
```
fetch → ocr → extract (LLM/Tool Use) → validate (コード) → store
```

アシスタント系の典型例:
```
receive_query → retrieve (Tool Use) → compose (LLM/Reflection) → respond
```

### Step 2: 各ノードを「決定論コード」と「LLM 判断」に振り分け

`reference/workflow_vs_agent.md` のフレームワークで判定:

| 決定論コードでやる | LLM でやる |
|---|---|
| データ取得（API/RSS/DB） | 自然言語の生成・要約 |
| フィルタ・並び替え・集計 | 分類・関連性判断 |
| 重複排除・正規化 | 文脈解釈・判断 |
| ファイル出力・配信 | エラーリカバリー思考 |

**鉄則**: コードで書ける処理はコードで書く。LLM は最後に集中投下。

### Step 3: 設計パターン選定

`reference/design_patterns.md` を読み、4パターンから必要なものを選ぶ:

| パターン | 向く場面 | 落とし穴 |
|---|---|---|
| **Reflection** | 高精度が必要、出力品質が最重要 | 回数制限なしでコスト爆発 |
| **Tool Use** | 外部 API/DB/ファイル操作が必要 | 権限管理不備、最小権限の原則 |
| **Planning** | 多段階タスク、順序判断が価値になる | 計画の過剰詳細化、修正困難 |
| **Multi-Agent** | 役割分担が価値（Planner+Reviewer 等） | エージェント間調整コスト |

**実務はハイブリッド**: 基本はワークフロー型、一部だけエージェント化が王道。

### Step 4: ツール設計（Tool Use を使う場合）

`reference/tools_pattern.md` を参照。各ツールに以下を定義:

```markdown
## tool_name
- 機能: 1行で説明
- 入力: 引数の型・意味
- 出力: 返り値の型・形式
- エラー時: 失敗パターンとフォールバック
- 権限: 何にアクセスできて、何にできないか
- キャッシュ: するか・キーは何か
- 呼び出し制限: 1回の実行で最大N回
```

### Step 5: コスト見積もり

`reference/cost_management.md` を参照し、設計ごとに概算:

```
- LLM 呼び出し回数 × 平均トークン数 × 単価
- ツール呼び出しコスト（ReAct がループするほど増える）
- 軽量モード（一部スキップ）の選択肢を用意
```

### Step 6: design.md を生成

```markdown
# 設計図: <エージェント名> v1

## 1. グラフ構造
（LangGraph の Mermaid 図）

## 2. ノード一覧
| ノード | 責務 | LLM/コード | 入力 | 出力 |
|---|---|---|---|---|

## 3. 設計パターン選定
- 採用: ○○、△△
- 不採用: □□（理由）

## 4. データフロー
（State の構造、ノード間で受け渡す情報）

## 5. ツール一覧
（Tool Use がある場合、各ツールの詳細）

## 6. コスト見積もり
| モード | 1回あたり | 月間 |
|---|---|---|

## 7. 失敗モードと対処
（各ノードが失敗したらどうなるか）

## 8. 次のアクション
（agent-prototype で何を実装するか）
```

### Step 7: detailed_design.md の生成 (必須)

`reference/detailed_design_template.md` をベースに、以下の図解を Mermaid で網羅:

- システムコンテキスト図 / デプロイ図
- コンポーネント図 (パッケージ依存)
- データモデル (TypedDict クラス図 + ER図)
- **シーケンス図** (主要ユースケース 5〜7本: 正常系 / 各バリエーション / 失敗系)
- **状態遷移図** (チケットライフサイクル / 状態の有効フィールド遷移)
- アクティビティ図 / フローチャート (主要ノードの判定分岐)
- インターフェース仕様 (公開関数シグネチャ表)
- エラー処理・例外フロー
- ロギング・監視メトリクス
- 性能・コスト試算 (Gantt 形式のレイテンシ予算など)
- セキュリティ要件 (PII 流れ、脅威モデル)
- テスト戦略

design.md は概要、detailed_design.md は実装・運用・レビュー時に参照する。
シーケンス図 1 本でも、後の prototype / deploy フェーズで業務担当・SE 間の認識合わせが格段に楽になる。

### Step 8: spec.md の更新

design.md と detailed_design.md の決定が spec.md と矛盾しないか確認。必要なら spec.md を更新。

## このスキルの注意点

### ノードを分けすぎない

10ノードを超えると見通しが悪くなる。**5〜7ノードが目安**。
複雑さが増えるなら、サブグラフ化（ノードの中にさらにグラフ）か、別エージェントに切り出し。

### 「全部エージェント化」を避ける

最初から Multi-Agent や Planning を採用すると、コストとデバッグ困難で頓挫しがち。
**ワークフロー型で動くものを作ってから、必要箇所だけエージェント化**する。

### TypedDict のフィールド宣言を忘れない

LangGraph の StateGraph を使うなら、`WorkflowState` (TypedDict) に流すフィールドを **必ず全て宣言**する。
未宣言のキーは silent drop され、ノード側で `state.get("foo", default)` のデフォルト値が常に使われる罠がある。

詳細: `AGENT_MINDSET.md` §「TypedDict / LangGraph での落とし穴」

## 次のステップ

```
/agent-prototype
```

`design.md` を読んで、`reference/workflow_skeleton.py` などの雛形から最小実装を組み立てる。
