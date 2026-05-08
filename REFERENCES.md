# 参考リファレンス

このスキルパックを設計する際に参考にした記事・論文・リポジトリのリスト。
各文書の「学び」を要約し、本パックのどのファイルに反映したかを明記。

---

## 1. 構造の参考: mathematical_optimizer_skill

- **URL**: https://github.com/sugupoko/mathematical_optimizer_skill
- **学び**:
  - **6スキル構造**: assess / baseline / improve / report / deploy / request の段階的フロー
  - **ヒアリングシート**: 問題タイプ別 (`hearing_sheet_shift.md` 等) を印刷して現場で記入
  - **reference/ 集約**: ガイド・テンプレート・コード雛形を一箇所に
  - **workspace/vN/ バージョン管理**: spec.md がそのバージョンの「正」、バージョン間で差分追跡
  - **CLAUDE.md / OPTIMIZATION_MINDSET.md** の二重構造（プロジェクトガイドと思考回路）
- **本パックへの反映**:
  - ディレクトリ構造を踏襲（`.claude/skills/` `reference/` `workspace/vN/`）
  - `CLAUDE.md` + `AGENT_MINDSET.md` の二重構造
  - スキルを6段階に分けた（discover / decompose / prototype / eval / deploy / evolve）

---

## 2. LayerX エンジニアブログ（実務知見）

### 2.1 AIエージェント機能を継続的に生み出すPM

- **URL**: https://zenn.dev/layerx/articles/b9272c63152826
- **学び**:
  - **ユースケースカタログ**: 数十件のニーズを by name で記載し、開発時に参照
  - **エンジニア主導の優先順位**: 技術的に突破しやすいトピックから着手
  - **継続的デリバリー**: 過度な品質追求より段階的リリース
- **反映**: `reference/usecase_catalog.md` のテンプレ、`agent-discover` の進め方

### 2.2 PMがAIエージェントを自作

- **URL**: https://tech.layerx.co.jp/entry/2025/11/06/080000
- **学び**:
  - 段階1: 写経で雰囲気をつかむ → 段階2: tools定義まで概念理解 → 段階3: エンジニアから雛形コードもらう
  - 完璧な理解より早く回す
  - 出力制御は Tool で外部指定、重要分岐に Human-in-the-Loop
- **反映**: `reference/workflow_skeleton.py` `react_skeleton.py` を雛形として用意（写経の起点）。`tools_pattern.md` の最小権限・出力制御。

### 2.3 評価駆動開発（Eval-driven development）

- **URL**: https://tech.layerx.co.jp/entry/2024/12/12/191131
- **学び**:
  - **代表的な少数（十数個）データセット**から始める。最初から大量不要
  - 評価3軸: **人間評価 / LLM as a judge / コードベース評価**
  - リリース後にユーザーフィードバックでデータセット拡充
  - ツール: RAGAS（評価指標）、Langfuse（モニタリング）
- **反映**: `reference/eval_driven_dev.md` ガイド、`agent-eval` スキル、`reference/eval_skeleton.py` 雛形

### 2.4 「使えば使うほど賢くなるAI機能」

- **URL**: https://tech.layerx.co.jp/entry/2025/10/23/222742
- **学び**:
  - **AIオンボーディング**: 役割・ゴール明確化 → 専門知識・情報資産・業務手順を提供
  - LLMに「何をしてほしいか」を明示する設計
- **反映**: `reference/editorial_persona.md`（ペルソナ・視点・ルールをプロンプトに注入）

### 2.5 LayerX AI Agent ブログリレー振り返り

- **URL**: https://tech.layerx.co.jp/entry/2025/11/28/124908
- **学び**: 業務オペレーションの細部にまでAIエージェントが入り込む流れ
- **反映**: ヒアリングシートで「業務プロセスの細部までヒアリングする」項目を増やした

---

## 3. 設計パターン: OpenBridge LLMエージェントデザインパターン

- **URL**: https://www.openbridge.jp/column/llm-agent-design-patterns
- **学び**: 4つのデザインパターンと使い分け
  - **Reflection**: 高精度が必要な場合、回数制限なしだとコスト爆発
  - **Tool Use**: API/DB操作、最小権限の原則
  - **Planning**: 多段階タスク、計画の過剰詳細化に注意
  - **Multi-Agent**: 役割分担、エージェント間調整コスト
  - 実務はハイブリッド構成が主流
- **反映**: `reference/design_patterns.md`、`agent-decompose` で設計パターン選定ガイド

---

## 4. Sakana AI 研究知見

### 4.1 ShinkaEvolve: 進化的エージェント設計

- **URL**: https://sakana.ai/shinka-evolve/
- **論文**: arxiv 2509.19349（推定）
- **学び**:
  - LLMでマルチエージェント scaffold を進化的に発見
  - **3-stage アーキテクチャ**: diverse expert personas + critical peer review + final synthesis
  - 75世代という驚異的なサンプル効率
  - 設計探索のメンタルモデル: 決め打ちせず探索
- **反映**: `agent-evolve` スキル、`reference/multi_agent_skeleton.py` の3段構成

### 4.2 The AI Scientist-v2

- **URL**: https://arxiv.org/abs/2504.08066
- **学び**:
  - **Agentic Tree Search** で実験計画を探索
  - VLM フィードバックループで図表の品質改善
  - 人間テンプレート不要、experiment manager がツリー管理
  - フェーズ分け: idea generation → experiment → visualization → manuscript → review
- **反映**: スキルのフェーズ分けを参考。`workflow_vs_agent.md` で tree search 戦略を紹介

### 4.3 Darwin Gödel Machine

- **URL**: https://sakana.ai/dgm/
- **学び**: エージェントが自身のコードを書き換えて改善（自己改良）。SWE-bench / Polyglot で検証
- **反映**: `agent-evolve` の長期目標に「自己改良型」を含める。本パック現状はそこまで自動化しない

### 4.4 Sakana Fugu

- **URL**: https://mpost.io/sakana-ai-launches-fugu-multi-agent-ai-system-built-on-collective-intelligence-research-and-frontier-model-coordination/
- **学び**: 複数の frontier モデル（Claude/GPT/Gemini など）をオーケストレーション
- **反映**: `multi_agent_skeleton.py` で異なるLLMを使い分ける選択肢を残す

---

## 5. 実装の参考: Claude Code 公式

### 5.1 Anthropic skills repo

- **URL**: https://github.com/anthropics/skills
- **学び**: SKILL.md のフォーマット、yaml frontmatter (name/description)、トリガー条件の書き方
- **反映**: `.claude/skills/agent-*/SKILL.md` のフォーマットを準拠

### 5.2 LangGraph / LangChain Anthropic ドキュメント

- **学び**:
  - StateGraph + create_react_agent
  - TypedDict のフィールド管理（宣言外は silent drop）
  - recursion_limit, config injection
- **反映**: `workflow_skeleton.py` `react_skeleton.py`、`AGENT_MINDSET.md` の TypedDict 注意

---

## 6. このプロジェクトで得た知見（news_searcher 実装から）

`workspace/<project>/` の開発を通じて以下を抽出。詳細は各ガイド参照。

| 知見 | 反映先ファイル |
|---|---|
| LangGraph + ReAct のハイブリッド | `workflow_vs_agent.md` |
| 編集者ペルソナのプロンプト注入 | `editorial_persona.md` |
| テーマ関連性スコアでリランキング | `reranking_guide.md` |
| ツールキャッシュ + ループ制御 | `tools_pattern.md` |
| SQLite で跨実行重複排除 | `persistence_guide.md` |
| TypedDict 全フィールド宣言の罠 | `AGENT_MINDSET.md` |
| URL短縮 / 前置き除去 / 出典[N]番号 | `editorial_persona.md` / `tools_pattern.md` |
| コストモード（軽量/フル/thinking） | `cost_management.md` |
| YAML 駆動でテーマ追加可能 | `theme_yaml_template.yaml` |
| ヒアリング → ブレイクダウン → 実装 の段階性 | スキルの分け方そのもの |

---

## 7. その他参考

- **Anthropic Engineering Blog: Building effective agents** — エージェント vs ワークフローの定義（公式）
- **LangChain Hub** — ReAct プロンプトテンプレ
- **Microsoft AutoGen / CrewAI / Semantic Kernel** — マルチエージェント実装の比較対象（本パックでは LangGraph 採用）

---

## 引用ルール

ガイドや SKILL.md でこれらのリソースを引用する場合、必ず本ファイル（REFERENCES.md）に追記し、出典を辿れるようにする。
