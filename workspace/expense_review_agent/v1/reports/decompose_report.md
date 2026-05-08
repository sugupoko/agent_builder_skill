# decompose レポート — Expense Review Agent v1

**実行日**: 2026-05-09
**スキル**: `/agent-decompose`
**実行者**: agent-builder

---

## 1. 入力

`v1/spec.md`（17 章構成、レビュー系メイン）

---

## 2. 成果物

| ファイル | 概要 |
|---|---|
| `v1/design.md` | 8 ノード LangGraph 構成、Tool Use + Reflection + コード優先ハードチェック |
| `v1/tools.md` | DB ツール 5 種（employee / policy / past_claims / pii_mask / extract_receipt）|
| `v1/detailed_design.md` | システムコンテキスト / シーケンス図 5 本 / Gantt / PII 流れ / テスト戦略 |
| `v1/reports/decompose_report.md` | 本レポート |

---

## 3. 主要設計判断

### 3-1. 採用

- **コード優先のハードチェック層**: `validate_rules` ノードで YAML ルールを検査（金額・期限・必須項目）。LLM の OK 判定で覆せない安全装置 → **不正 auto_approve（致命リスク）の防御線**
- **Tool Use**: `lookup_history`（過去 90 日重複検出）、`lookup_employee` / `lookup_policy`
- **Reflection**: `reflect` ノード（max 1 ループ） — 致命の二重防止 + フィードバック具体性チェック
- **軽量モード**: `validate_rules` violations 0 + duplicate 0 + 金額閾値以下 で自動起動（40〜50% カバレッジ想定）

### 3-2. 不採用

| 案 | 理由 |
|---|---|
| 純 ReAct | 業務定型、誤呼び出しリスク |
| Multi-Agent | v1 では複雑性過剰、v2 検討（ルール検査 / フィードバック起草 / 監査の 3 段化）|
| Planning | 「分類 → 判定 → フィードバック」が固定 |
| RAG（過去事例検索）| ユースケース 22 で C ランク |
| 領収書画像の LLM 直送 | 機密リスク、OCR 抜粋のみ送信 |

---

## 4. ノード構成

| # | ノード | 種別 | LLM/コード |
|---|---|---|---|
| 1 | preprocess | 決定論 | コード |
| 2 | extract | ハイブリッド | コード + Haiku 補完 |
| 3 | validate_rules | 決定論 | コード（核心の安全装置）|
| 4 | lookup_history | 決定論 | コード（疑似 Tool Use）|
| 5 | classify_gray | LLM 判定 | Sonnet |
| 6 | draft_decision | LLM 起草 | Sonnet / 軽量時 Haiku |
| 7 | reflect | LLM 自己レビュー | Sonnet |
| 8 | assemble | 決定論 | コード |

→ 8 ノード（cs_triage の 7 から 1 増）。`validate_rules` 独立化が安全性の鍵。

---

## 5. コスト試算

### 標準モード
- 1 件: **約 $0.025**（spec.md §9 目標 $0.03 の 83%）
- 内訳: classify_gray $0.006 / draft $0.0096 / reflect $0.0084 / extract補完 $0.0007

### 軽量モード（Haiku のみ）
- 1 件: **約 $0.005**（標準の 20%）

### 月間試算（月 1,800 件、標準 55% / 軽量 45%）
- 約 **$29/月**（予算 $1,000 の 3%）→ 極めて余裕

---

## 6. 性能試算

- レイテンシ P95: **約 12 秒**（spec.md §10-2 目標 15 秒以内）
- 軽量モード: **約 5〜7 秒**

---

## 7. spec.md §16 として上げた未確定項目

decompose 中の発覚なし。実装上の不明点は prototype フェーズで明らかになる想定。

---

## 8. 次のアクション

```
/agent-prototype
```

入力: `v1/spec.md` / `v1/design.md` / `v1/tools.md` / `v1/detailed_design.md`
期待出力:
- `v1/scripts/agent.py` + `v1/scripts/src/*.py`
- `v1/scripts/config/expense_policy.yaml`
- `v1/scripts/eval/mock_db/`（employees / past_claims / policy）
- `v1/scripts/eval/dataset/`（12 件のサンプル）

### Prototype 担当への申し送り

1. 雛形は `reference/workflow_skeleton.py`
2. DB は YAML フィクスチャ（実 DB は deploy フェーズ）
3. PII マスキング: 氏名・社員 ID（領収書 URL は外部送信しない）
4. regex は YAML から（テストドリフト防止）
5. TypedDict 全フィールド宣言
6. **validate_rules を必ず通す**: LLM の前段。LLM の OK 判定で覆せない（致命リスク防御線）
7. Sonnet/Haiku 切替は config で
8. cost / logger は cs_triage と同じ流儀

---

## 9. cs_triage_agent との設計差分

| 観点 | cs_triage_agent v1 | expense_review_agent v1 |
|---|---|---|
| ノード数 | 7 | **8**（validate_rules 独立）|
| パターン | Tool Use + Reflection | **コード優先ハードチェック** + Tool Use + Reflection |
| 軽量モード起動 | カテゴリ判定 | ルール OK + 重複 0 + 金額閾値 |
| LLM 役割 | 起草 / 分類 / 自己レビュー | **グレー判定** / 起草 / 自己レビュー |
| 致命リスク | クレーム見逃し | **不正 auto_approve** |
| 月予算消化 | $1,092 / $3,000 (36%) | **$29 / $1,000 (3%)** |

→ レビュー系は「ルール検査をコードに任せる」割合が高く、LLM の役割は **判断のグレーゾーン補助** に絞られる。コスト効率も良い。
