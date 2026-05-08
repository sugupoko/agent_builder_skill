# Agent Builder Skill Pack

> ⚠️ **運用前のテストバージョンです。** 本リポジトリは合成データ・想定ペルソナでスキル動線を検証した参考実装であり、実プロジェクトに組み込む前に必ず実関係者によるレビュー・補正を行ってください。仕様・スキル定義・サンプル実装は予告なく変更される可能性があります。

業務ヒアリングから AI エージェントの実装・運用までを「**ヒアリング → ブレイクダウン → 実装 → 評価 → 運用 → 進化**」の6段階に分けてスキル化した Claude Code 向けスキルパック。

> **核心**: LLMには判断させすぎない。決定論コードと LLM 判断の境界を明確に引き、ハイブリッド構成で組む。

---

## 改善ポイント / 既知の制限 (v0.0.1 時点)

「テストバージョン」であるため、現時点で **特にデプロイ・運用面が弱い** ことを明記しておきます。本パックを実プロジェクトに適用する際は以下を自分のチームで補完する必要があります。

### 🔴 Deploy / 運用面（最優先で要補強）

- **配信スクリプトはスケルトンのみ**: `cs_triage_agent/v3/scripts/` の `dispatch_salesforce.py` / `dispatch_slack.py` / `webhook_receiver.py` / `monitor.py` は外形だけで、実 SDK 呼び出し部分は `TODO` のまま。本番接続は SE による実装が前提
- **インフラの動作検証なし**: K8s / Vault / Datadog / Salesforce / Slack 等の連携は spec / design レベルで描かれているのみ。`reference/infra_k8s_skeleton/` も雛形（`<placeholder>` を置換する前提）
- **CI/CD は GitHub Actions yaml を書いただけ**: 実環境での動作確認はしていません
- **シャドー → パイロット → 全展開のロールアウト計画は文書のみ**: `cs_triage_agent/v3/ops/rollout_plan.md` は手順書、cutover 基準達成の実例なし
- **PII 監査ジョブ未実装**: 月次ランダム 100 件の照合スクリプト（`audit_pii.py`）は書いていません
- **expense_review_agent は deploy フェーズ未着手**: v1 評価で完結。楽楽精算 webhook / freee 連携は未設計

### 🟡 評価・品質面の制限

- **評価データはすべて合成**: 実業務ログでの検証なし。本番ログを `eval/dataset/` に昇格する月次サイクルは仕組みのみ
- **LLM Judge と業務ルールの整合性**: cs_triage v3 で「Judge プロンプトが業務ルールを知らずに不当低評価する」現象を確認済（`cs_triage_agent/v3/reports/evolve_v3_report.md §6`）。Judge プロンプトに YAML 抜粋を渡す改修案は v3.1 候補のまま
- **コスト・SLO は試算ベース**: cs_triage 月 $1,092 / expense 月 $30 はあくまで合成データからの推定。本番のバースト・リトライで上振れ余地あり
- **境界条件のバグが残存**: expense_review v1 case_10 で `amount > threshold` が境界等号で抜けるバグを確認、`>=` への修正は未対応

### 🟢 スキル設計面（マイナーな未完了）

- **agent-evolve の DSPy / Multi-Agent / 進化的探索は概念のみ**: 実装例なし、参考リンクのみ
- **`reference/multi_agent_skeleton.py` は使われていない**: cs_triage / expense とも v1 では Multi-Agent 不採用
- **TypedDict silent drop 罠 / regex 貪欲マッチ罠の単体テスト**: `tools_pattern.md` で明文化済だが、サンプル実装側にテスト固定が弱い

### 補完するための実務上の進め方（推奨）

1. **業務側の合意取り**（spec.md §16 の未確定項目を `/agent-discover` 追加モードで解消）
2. **インフラ実装**（SE 主導、上記 deploy スケルトンを実 SDK で埋める）
3. **シャドー運用 1 ヶ月**（cs_triage_agent/v3/ops/rollout_plan.md Phase 1 参照）
4. **本番ログから新規ケースを eval/dataset/ に昇格**（月次レビュー会で）
5. v2 / v3 を切る判断（CHANGELOG.md に動機 → 内容 → 結果を記録）

→ 本パックは「**設計の型と評価サイクルの作り方**」を提供するもので、本番運用の実装そのものは利用者側で補完する設計です。

---

## 対応するエージェントタイプ

| 種別 | 例 |
|---|---|
| **リサーチ系** | 週次ニュース配信、競合動向ウォッチ、論文サーベイ |
| **レビュー系** | 申請書レビュー、コード品質チェック、契約書チェック |
| **抽出系** | メール添付ファイル取得、構造化データ化、エンティティ抽出 |
| **アシスタント系** | 業務問い合わせ対応、FAQ、社内ドキュメント検索 |

---

## クイックスタート

```bash
# 1. クローン
git clone <this repo>
cd agent_builder_skill

# 2. 依存パッケージ
pip install -r requirements.txt

# 3. .env に APIキー
cp .env.example .env
# ANTHROPIC_API_KEY=sk-ant-... を書き込む

# 4. Claude Code を起動
claude
```

---

## 進め方

```
ヒアリング → ブレイクダウン → 実装 → 評価 → 運用 → 進化
     ↓              ↓             ↓       ↓       ↓        ↓
/agent-discover → /agent-decompose → /agent-prototype → /agent-eval → /agent-deploy → /agent-evolve
```

### Step 0: ヒアリング前の準備
`reference/hearing_sheet_*.md` を業務種別に応じて選び、印刷して関係者に記入してもらう。

### Step 1: ヒアリング → ユースケース抽出
```
/agent-discover
```
ペルソナ・ユースケース・成功基準・評価データを抽出して `spec.md` を初版化。

### Step 2: ブレイクダウン
```
/agent-decompose
```
業務プロセスをノードに分割、設計パターン（Reflection / Tool Use / Planning / Multi-Agent）を選定。`design.md` を生成。

### Step 3: プロトタイプ
```
/agent-prototype
```
`reference/workflow_skeleton.py` などの雛形を起点に最小実装。

### Step 4: 評価
```
/agent-eval
```
評価データセットで品質・コスト・所要時間を測定。改善案を出す。

### Step 5: 運用設計
```
/agent-deploy
```
cron / 配信先 / 監視 / コスト上限 / レビュー工程を設計。

### Step 6: 進化（オプション）
```
/agent-evolve
```
マルチエージェント化・パラメータ探索などの構成変更を試みる。

---

## ディレクトリ構成

```
agent_builder_skill/
├── CLAUDE.md                ← プロジェクトガイド
├── AGENT_MINDSET.md         ← 7つの思考回路 + チェックリスト
├── REFERENCES.md            ← 設計の参考にした記事・論文
├── README.md                ← 本ファイル
├── .claude/skills/          ← /agent-xxx スキル
├── reference/               ← ガイド・テンプレート・ヒアリングシート
└── workspace/
    └── <project_name>/v1/   ← あなたのプロジェクト (cs_triage_agent がリファレンス実装)
```

---

## サンプル実装

スキルパックの 4 種別（リサーチ / レビュー / 抽出 / アシスタント）に対するサンプル実装を順次公開しています。いずれも **合成データ + 想定ペルソナ** で動線を検証した参考実装で、実プロジェクト適用前に実関係者によるレビューが必要です。

| サンプル | 種別 | 特徴 |
|---|---|---|
| `workspace/cs_triage_agent/` | アシスタント + 抽出 + レビュー | B2B 製造業 CS のメール → 回答ドラフト。v1 → v2 → v3 の進化、deploy 設計まで完備 |
| `workspace/expense_review_agent/` | **レビュー（純）** | 経費 / 請求申請レビュー（auto_approve / needs_fix / needs_review / reject の 4 段階振り分け）|

---

## サンプル実装 1: `workspace/cs_triage_agent/`

スキル動線の全体像を掴むためのリファレンス実装として、**B2B 製造業のコールセンター問い合わせを「トリアージ + 回答ドラフト生成」するエージェント**を作ってみています。同梱の合成データ・想定ペルソナ（CS センター長 / ベテランオペ / SV / 法務 / SE）でシミュレートしながら、6 つのスキルを順番に走らせ、v1 → v2 → v3 で段階改善する過程を一通り再現しました。

```
workspace/cs_triage_agent/
├── CHANGELOG.md             ← v1/v2/v3 の動機 → 内容 → 評価結果
├── data/                    ← 初期入力 + 第 2 回追加ヒアリング議事録
├── v1/                      ← 初版（discover → decompose → prototype → eval）
├── v2/                      ← P0/P1 改善（CAD URL 顧客送出問題の修正など）
└── v3/                      ← 致命ミス 0 達成、deploy 設計付き
```

各バージョンの `reports/` と `spec.md / design.md / detailed_design.md / tools.md` を読むと、そのフェーズで「何を判断したか / なぜそうしたか」が辿れます。**「どんなアウトプットが出るか」を見たいときは v3 の reports と ops から見るのが早いです**:

- `v3/reports/deploy_design.md` — 17 章の運用設計書
- `v3/ops/runbook.md` / `v3/ops/rollout_plan.md` — 障害対応 + Phase 0〜3 ロールアウト
- `v3/reports/evolve_v3_report.md` — Judge コメント分析と次のイテレーション計画

### このサンプルを組み立てるときに参考にした考え方

- **「決定論コードと LLM 判断の境界を引く」**（AGENT_MINDSET.md §1）→ DB 引き当てはコード、起草と分類は LLM、両者を 7 ノード LangGraph で接続
- **LayerX 流の評価駆動開発・ユースケースカタログ**（REFERENCES.md §2）→ 22 件の評価データセット + 30 件のユースケースカタログから着手判断
- **OpenBridge の 4 つの設計パターン**（reference/design_patterns.md）→ Reflection（reflect ノード）+ Tool Use（疑似 Tool Use の retrieve ノード）を採用、Planning / Multi-Agent は v1 では見送り
- **Anthropic "Building effective agents"** の workflow vs agent（reference/workflow_vs_agent.md）→ 基本ワークフロー型 + 部分的な Reflection ループのハイブリッド
- **Sakana AI ShinkaEvolve の発想**（agent-evolve スキル）→ 設計を決め打ちせず、評価を回しながら v2 / v3 で段階探索

> あくまで **検証用のサンプル** です。実プロジェクトで再利用する場合は、業務担当者・SV・法務・SE による spec / runbook のレビューが必須です（`v3/reports/deploy_report.md §13` 参照）。

---

## サンプル実装 2: `workspace/expense_review_agent/`

スキルパックの **レビュー系** に特化したサンプル。中堅 SaaS 企業（300 名規模）の経費 / 請求申請を `auto_approve / needs_fix / needs_review / reject` の 4 段階に自動振り分けし、申請者向けの具体的なフィードバック文言まで生成します。

```
workspace/expense_review_agent/
├── data/                    ← 5 ペルソナ（経理マネージャ / ベテラン経理 / 申請者 / 監査 / SE）の合成ヒアリング
└── v1/                      ← v1 フル（discover → decompose → prototype → eval）
    ├── spec.md / design.md / detailed_design.md / tools.md / hearing_notes.md / usecase_catalog.md
    ├── scripts/             ← 8 ノード LangGraph（preprocess / extract / validate_rules / lookup_history /
    │                          classify_gray / draft_decision / reflect / assemble）
    └── reports/             ← discover / decompose / prototype / eval の各レポート
```

### v1 評価結果（合成 12 件、実 LLM）

| 指標 | 値 | 目標 |
|---|---|---|
| 判定一致率 | **91.7%** (11/12) | 85% ✅ |
| 致命ミス（不正 auto_approve） | **0 件** | 0 ✅ |
| クレーム / 重複検出再現率 | **100%** | 95% ✅ |
| LLM Judge overall | **3.85 / 5** | 4.0 ⚠️ |
| 1 件コスト | $0.017 | $0.03 ✅ |

→ **コード優先のハードチェック層**（`validate_rules` ノード）が LLM の前段で動くため、レビュー系は初版で高い完成度に達しやすい。LLM の役割は「グレーゾーン判断 + フィードバック起草 + 自己レビュー」に絞られる。

### このサンプルを組み立てるときの参考

- **「決定論コードと LLM 判断の境界を引く」** を最も忠実に実装した例（コード比率が高い）
- LayerX 流の評価駆動開発で 12 件サンプルから着手、Judge スコアと併走でケース増強の優先度を判断
- OpenBridge の Tool Use + Reflection を「ルール検査の二重防御」として位置付け

---

## 設計の参考

このスキルパックは以下の知見を凝縮しています。詳細は `REFERENCES.md`:

- **mathematical_optimizer_skill** — ディレクトリ構造・スキル分け
- **LayerX エンジニアブログ** — Eval-driven 開発、ユースケースカタログ、AIオンボーディング
- **OpenBridge LLMエージェントデザインパターン** — Reflection / Tool Use / Planning / Multi-Agent
- **Sakana AI** — ShinkaEvolve（進化的設計探索）、AI Scientist-v2（agentic tree search）

---

## ライセンス

[MIT License](LICENSE) — Copyright (c) 2026 sugupoko
