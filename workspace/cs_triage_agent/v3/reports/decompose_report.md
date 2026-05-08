# decompose レポート — CS Triage Agent v1

**実行日**: 2026-05-08
**スキル**: `/agent-decompose`
**実行者**: agent-builder

---

## 1. 入力

| ファイル | 概要 |
|---|---|
| `v1/spec.md` | discover で生成された 17 章構成の初版仕様書 |

---

## 2. 成果物

| ファイル | 概要 | サイズ目安 |
|---|---|---|
| `v1/design.md` | グラフ構造（Mermaid）/ ノード一覧 / パターン選定 / コスト見積もり / 失敗モード | 約 12 章 |
| `v1/tools.md` | DB ツール 5 種 + マスキング関数 1 種の仕様。最小権限 / キャッシュ / エラー設計 / 単体テスト指針 | 9 章 |
| `v1/detailed_design.md` | システムコンテキスト / デプロイ / コンポーネント / データモデル / シーケンス図 6 本 / 状態遷移 2 本 / Gantt / PII 流れ / テスト戦略 | 14 章 + 付録 3 |
| `v1/reports/decompose_report.md` | 本レポート |

`spec.md` も更新（§16-2 と §17 改訂履歴に追記）。

---

## 3. 設計判断のサマリ

### 3-1. 採用した設計

- **ハイブリッド構成**: ワークフロー型 + Tool Use（retrieve）+ Reflection（reflect 最大 1 回）
- **ノード数**: 7（preprocess / extract / classify / retrieve / draft / reflect / assemble）
- **モデル使い分け**:
  - Sonnet 4.6: classify / draft / reflect
  - Haiku 4.5: extract 補完 / 軽量モード時の draft / reflect
- **軽量モード起動**: classify 結果から自動切替（shipment / cad / billing で complaint_smell=false → Haiku）

### 3-2. 捨てた設計

| 案 | 捨てた理由 |
|---|---|
| 純 ReAct（LLM が retrieve を計画） | 業務が定型化されており、LLM の判断余地は害悪。誤呼び出しでコスト増 |
| Multi-Agent（Planner+Reviewer） | v1 では役割分担の利益がコスト・複雑性を上回らない |
| Planning（タスク分解） | 「分類 → 引き当て → 起草」が固定 |
| RAG（FAQ 検索） | v1 ではユースケース 25（FAQ 自動応答）が C ランク |
| Reflection 多段ループ（3 回以上）| コスト爆発リスク + 改善が頭打ち |
| ノード分割を 10 個以上に細分化 | 見通しが悪くなる |

---

## 4. ノード設計

### 4-1. 各ノードの役割と境界

| # | ノード | 種別 | 主な責務 |
|---|---|---|---|
| 1 | preprocess | コード | PII マスキング / 言語判定 / 添付検出 |
| 2 | extract | コード + Haiku | regex 主、LLM は補完のみ |
| 3 | classify | LLM (Sonnet) | カテゴリ + 緊急度 + クレーム匂い（kw 辞書 + LLM 二重判定）|
| 4 | retrieve | コード | DB 5 種を並列 lookup（疑似 Tool Use）|
| 5 | draft | LLM (Sonnet/Haiku) | 顧客向け本文 + 内部メモ起草 |
| 6 | reflect | LLM (Sonnet) | 数字捏造 / 必須語 / クレーム引き継ぎ / トーン |
| 7 | assemble | コード | unmask + 最終 JSON 組み立て |

### 4-2. 「LLM に判断させすぎない」境界

- **DB 引き当て値はコードで管理**、LLM が改変できないよう draft プロンプトに明示禁止 + reflect で検査
- **ツールは LLM の Tool Use ではなく、retrieve ノード内のコードがカテゴリで分岐呼び出し**（誤呼び出し防止）
- **マスキングは preprocess の 1 回のみ**、以後は masked_text を全ノードで使い回す

---

## 5. コスト試算

### 標準モード（Sonnet 中心）
- 1 件あたり: **約 $0.027**（spec.md §10-1 目標 $0.05 の 54%）
- ノード別: classify $0.0046 / draft $0.012 / reflect $0.009 / extract補完 $0.0008

### 軽量モード（Haiku のみ）
- 1 件あたり: **約 $0.005**（標準の 1/5）

### 月間試算（月 6 万件、標準 60% / 軽量 40%）
- 約 **$1,092 / 月**（予算 $3,000 の 36%）

---

## 6. 性能試算

### レイテンシ予算（P95）
- 標準モード: **約 20 秒**（Reflection 1 回ループ含む、spec.md §10-2 目標 30 秒の 67%）
- 軽量モード: **約 5〜8 秒**

### 内訳（P95、標準）
- preprocess + extract: ~0.2s
- classify (Sonnet): ~3s
- retrieve (DB 並列): ~0.5s
- draft (Sonnet): ~5s
- reflect (Sonnet): ~3s
- + Reflection 1 回: ~+8s
- assemble + SF 書き込み: ~0.3s

---

## 7. spec.md §16 として上げた未確定項目（追加分）

decompose 中に発覚した実装上の不明点を `spec.md §16-2` に追記:

- DB ツールの実 SDK / 接続文字列（PostgreSQL / ERP API / 認証トークン Vault パス）
- Salesforce「AI ドラフト」タブのカスタムフィールド定義（カスタムオブジェクト or ロングテキスト項目）
- Slack 通知のチャンネル名と bot token
- `customer_id` の取得経路（Salesforce ケースのどのフィールドから取るか）

これらは `prototype` フェーズはモックで進められるが、`deploy` フェーズ前に SE と擦り合わせ必須。
§16 累計（業務 4 + 評価 2 + 実装 4）= **10 項目**。すでに 3 項目以上の閾値を超えているが、いずれも v1 prototype 着手をブロックしない性質の不明点なので、prototype 完了後に追加ヒアリングモードで一気に解消する流れを推奨。

---

## 8. リファレンス実装（cs_triage_agent v1）との差分

| 観点 | リファレンス v1 | 本 v3 v1 |
|---|---|---|
| ノード数 | 9 | **7**（preprocess + assemble に統合）|
| 軽量モード起動 | 別 YAML（cs_triage_lite.yaml）で明示実行 | classify の結果で自動切替 |
| Reflection | あり | あり（最大 1 回ループを明示）|
| Tool Use | 関数ノード（疑似 Tool Use）| 同方針 |
| モデル割り当て | Sonnet + Haiku 使い分け | 同方針 + 軽量モードの起動条件を YAML で明示 |
| シーケンス図 | 7 本 | 6 本（同等のカバレッジ）|

→ 大筋はリファレンスと整合。本 v3 はノード分割を一段シンプルにし、Reflection の終了条件と軽量モード起動を YAML で明示する点が改善ポイント。

---

## 9. 次のアクション

```
/agent-prototype
```

入力: `v1/spec.md` / `v1/design.md` / `v1/tools.md` / `v1/detailed_design.md`
期待出力:
- `v1/scripts/agent.py`（LangGraph StateGraph 実装）
- `v1/scripts/src/*.py`（各ノード関数）
- `v1/scripts/config/cs_triage.yaml`（design.md §8 を起点に）
- `v1/scripts/eval/mock_db/*.yaml`（DB モックフィクスチャ）
- `v1/reports/prototype_report.md`

### prototype 担当への申し送り

1. **雛形は `reference/workflow_skeleton.py` を起点**（StateGraph + TypedDict + dot-env）
2. **DB はモック**（YAML / JSON フィクスチャ）。実 DB 接続は deploy フェーズ
3. **PII マスキングは `src/pii_mask.py` に独立実装**。テストケース 30+ 件
4. **regex は YAML から読み込み**（テスト drift 防止、`reference/tools_pattern.md` 参照）
5. **TypedDict のフィールドは全て宣言**（silent drop 罠回避、`AGENT_MINDSET.md` 参照）
6. **Sonnet/Haiku の使い分けは config で切替可能に**（実機検証コストを下げる）
7. **コスト計測は `src/cost.py` で全 LLM 呼び出しに対して実施**（`reference/cost_management.md`）
8. **prototype 段階での品質ゲート**: コンパイル + dry-run 完走 + 単体テスト 70+ 件パス
