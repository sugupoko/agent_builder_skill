# CLAUDE.md — Agent Builder Skill Pack

## 概要

業務ヒアリングからAIエージェントの実装・運用までを「**ヒアリング → ブレイクダウン → 実装 → 評価 → 運用 → 進化**」の6段階に分けてスキル化したパック。

**核心**: LLMには判断させすぎない。決定論コードと LLM 判断の境界を明確に引き、ハイブリッド構成で組む。

対応するエージェントの種類:

| 種別 | 例 | ヒアリングシート |
|---|---|---|
| **リサーチ系** | 週次ニュース収集・サマリ・配信、競合動向ウォッチ | `hearing_sheet_research.md` |
| **レビュー系** | 申請書レビュー、コード品質チェック、契約書チェック | `hearing_sheet_review.md` |
| **抽出系** | 証憑取得、情報抽出、構造化データ化 | `hearing_sheet_extraction.md` |
| **アシスタント系** | 業務問い合わせ対応、FAQ、ドキュメント検索 | `hearing_sheet_assistant.md` |

## セットアップ

```bash
git clone <this repo>
cd agent_builder_skill
pip install -r requirements.txt   # langgraph / langchain-anthropic / pyyaml etc.
claude    # このフォルダで Claude Code を起動
```

`.env` に `ANTHROPIC_API_KEY` を入れる（`.env.example` 参照）。

## ディレクトリ構成

```
agent_builder_skill/
├── CLAUDE.md                ← このファイル
├── AGENT_MINDSET.md         ← 7つの思考回路 + チェックリスト
├── README.md                ← プロジェクト概要
├── .claude/skills/          ← 6つのスキル (/agent-xxx)
│   ├── agent-discover/      ← Step 1: ヒアリング → ペルソナ・ユースケース抽出
│   ├── agent-decompose/     ← Step 2: 業務分解 + 設計パターン選定
│   ├── agent-prototype/     ← Step 3: 最小実装（雛形ベース）
│   ├── agent-eval/          ← Step 4: 評価駆動改善
│   ├── agent-deploy/        ← Step 5: 運用設計
│   └── agent-evolve/        ← Step 6: 探索的改善（マルチエージェント化等）
├── reference/               ← ガイド・テンプレート・ヒアリングシート
│   ├── hearing_templates.md            ← 共通ヒアリングチェック項目
│   ├── hearing_sheet_research.md       ← リサーチ系
│   ├── hearing_sheet_review.md         ← レビュー系
│   ├── hearing_sheet_extraction.md     ← 抽出系
│   ├── hearing_sheet_assistant.md      ← アシスタント系
│   ├── usecase_catalog.md              ← ユースケースカタログ（LayerX流）
│   ├── design_patterns.md              ← Reflection / Tool Use / Planning / Multi-Agent
│   ├── workflow_vs_agent.md            ← AIワークフロー vs エージェント、ハイブリッド戦略
│   ├── eval_driven_dev.md              ← 評価駆動開発（LayerX流）
│   ├── cost_management.md              ← トークン・コスト管理
│   ├── editorial_persona.md            ← 編集者の声をプロンプトに注入
│   ├── reranking_guide.md              ← テーマ関連性スコアでリランキング
│   ├── persistence_guide.md            ← 跨実行重複排除（SQLite）
│   ├── tools_pattern.md                ← ツールキャッシュ・最小権限・エラー設計
│   ├── workflow_skeleton.py            ← LangGraph 雛形
│   ├── react_skeleton.py               ← create_react_agent 雛形
│   ├── multi_agent_skeleton.py         ← Planner+Reviewer+Synthesizer 3段
│   ├── eval_skeleton.py                ← LLM as a judge + コードベース評価
│   ├── theme_yaml_template.yaml        ← 設定駆動YAMLのテンプレ
│   └── spec_template.md                ← エージェント仕様書テンプレ
└── workspace/               ← ★ ここで作業する
    └── <project_name>/
        └── v1/
            ├── spec.md              ← このバージョンの仕様
            ├── hearing_notes.md     ← ヒアリングメモ
            ├── usecase_catalog.md   ← ユースケース一覧
            ├── design.md            ← 設計図（ノード分割・パターン選定）
            ├── detailed_design.md   ← 詳細設計書（シーケンス図・状態遷移等）
            ├── scripts/             ← 実装コード
            ├── eval/                ← 評価データ・結果
            └── reports/             ← 各スキルが生成する報告書
```

## ワークフロー

```
業務ヒアリング → ユースケース → 設計 → プロトタイプ → 評価 → 運用 → 進化
        ↓             ↓          ↓        ↓          ↓        ↓        ↓
  /agent-discover → /agent-decompose → /agent-prototype → /agent-eval → /agent-deploy → /agent-evolve
                                                              ↑↓
                                                         (繰り返し)
```

## 各スキルの入出力

| スキル | 入力 | 出力ファイル（v*/ 内） |
|---|---|---|
| `/agent-discover` | 「○○業務を効率化したい」 | `spec.md`（初版）/ `hearing_notes.md` / `usecase_catalog.md` |
| `/agent-decompose` | spec.md | `design.md` / `tools.md` / `spec.md` 更新 |
| `/agent-prototype` | design.md | `scripts/` + 動く最小実装 |
| `/agent-eval` | scripts/ + 評価データ | `eval/results/` + 改善提案 |
| `/agent-deploy` | 動くプロトタイプ | `reports/deploy_design.md` + 運用スクリプト |
| `/agent-evolve` | 動くシステム | 探索結果（構成変更案・パラメータ調整） |

## 出力ルール（共通）

全成果物は `workspace/<project>/vN/` 内に出力する。最新の `spec.md` が「今の正」。

- `spec.md` を見れば常にエージェントの現在の仕様が分かる
- バージョン間の `spec.md` を比較すれば変更点がわかる
- 各バージョンは独立で再実行可能

## 設計の核心（必ず読む）

`AGENT_MINDSET.md` を最初に読むこと。特に:

1. **LLMには判断させすぎない**: 収集・整形・並び替えは決定論コード。LLMは要約・判断・深掘りに限定
2. **ハイブリッド構成で始める**: 純エージェント（自律的）ではなく、ワークフロー型を基本にし一部だけエージェント化
3. **評価データから始める**: 大規模データセット不要。代表的な十数個から評価サイクルを回す
4. **編集者の声を注入する**: editorial.persona に判断スキルを言語化して書く

## 設計上の取り決め

- **依存バージョンは pin**: `langgraph==1.0.5` など固定。アップグレードは動作確認とセット
- **TypedDict には全フィールド宣言**: LangGraph の StateGraph は宣言外フィールドを silent drop する
- **API キーは `.env`**: `python-dotenv` で読む。Git に絶対コミットしない
- **YAMLで運用**: テーマ追加・キーワード調整・セクションON/OFFは config/ のみで完結
- **コスト管理**: `--no-deep-dive` `--no-supplement` など段階的に機能を切れる設計

## バージョン駆動の運用（重要）

`workspace/<project>/` は **vN/ ディレクトリで完全分離**する。

```
<project>/
├── v1/  ← 完結したバージョン（spec.md / scripts / eval / reports）
├── v2/  ← v1 から派生した次バージョン
├── v3/
└── CHANGELOG.md   ← バージョン間の変更記録（動機・内容・結果）
```

### バージョンを切るタイミング
- ノード追加・削除、設計パターン変更、モデル変更、YAMLスキーマ変更 → **新バージョン**
- キーワード追加・プロンプトの数語修正 → 同バージョン内更新

### 必須の運用ルール
- vN+1 を切るときは **vN と同じデータセットで両方評価し回帰チェック**
- `CHANGELOG.md` に「変更の動機 → 内容 → 評価結果」を記録
- 月次で本番ログから「うまくいかなかったケース」を eval/dataset/ に昇格

詳細は `reference/ops_iteration.md` を参照。

## 既存の実装例

`workspace/cs_triage_agent/v1/` を読むのが最速の理解への近道。B2B 製造業のコールセンターで顧客問い合わせを「カテゴリ分類 + 型番抽出 + DB引き当て + 回答ドラフト生成 + クレーム匂い検知」する 9 ノード LangGraph エージェントで、本パックの設計原則を実装したリファレンス実装。詳細設計書 (シーケンス図・状態遷移)・LLM Judge 評価 (5次元採点)・運用設計書まで揃っている。

v2 として「persona 強化」を試して **品質劣化により不採用** にした事例も含み、「プロンプト改善は『追加』より『整理・削減』」の教訓 (`AGENT_MINDSET.md` 参照) の実例として参照可能。
