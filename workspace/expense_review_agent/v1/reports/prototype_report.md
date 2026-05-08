# prototype レポート — Expense Review Agent v1

**実行日**: 2026-05-09 / **スキル**: `/agent-prototype`

---

## 1. 入力

| ファイル | 概要 |
|---|---|
| `v1/spec.md` / `design.md` / `tools.md` / `detailed_design.md` | discover + decompose 経由 |

---

## 2. 成果物

```
v1/scripts/
├── agent.py                       # CLI + LangGraph build_graph (8 ノード)
├── run.sh                         # 12 サンプル一括実行
├── .env.example
├── src/
│   ├── state.py                   # ReviewState (TypedDict)
│   ├── config.py / cost.py / logger.py
│   ├── pii_mask.py                # 申請者氏名・社員 ID マスク
│   ├── db_client.py               # 社員マスタ + 過去申請 DB（モック）
│   ├── preprocess.py              # ノード 1
│   ├── extract.py                 # ノード 2
│   ├── validate_rules.py          # ノード 3（コア安全装置）
│   ├── lookup_history.py          # ノード 4（重複検出）
│   ├── classify_gray.py           # ノード 5（LLM グレー判定）
│   ├── draft_decision.py          # ノード 6（4 判定 + フィードバック）
│   ├── reflect.py                 # ノード 7（自己レビュー）
│   └── assemble.py                # ノード 8
├── config/
│   └── expense_policy.yaml        # 規程・上限金額・モデル選定
└── eval/
    ├── mock_db/
    │   ├── employees.yaml         # 社員マスタ 6 名
    │   └── past_claims.yaml       # 過去 90 日 4 件
    ├── dataset/
    │   └── case_*/                # 12 ケース（input.json + metadata.yaml）
    ├── run_eval.py                # コードベース評価 + dry-run/real
    └── judge.py                   # LLM Judge 5 次元
```

合計: 14 Python ファイル + 1 YAML 設定 + 2 mock DB + 12 サンプル + 2 eval スクリプト。

---

## 3. 実装したノード（design.md §2 と対応）

| # | ノード | 実装ファイル | LLM |
|---|---|---|---|
| 1 | preprocess | src/preprocess.py | なし |
| 2 | extract | src/extract.py | なし（v1 は payload にフィールドある前提）|
| 3 | validate_rules | src/validate_rules.py | なし（**核心の安全装置**）|
| 4 | lookup_history | src/lookup_history.py | なし |
| 5 | classify_gray | src/classify_gray.py | あり（Sonnet）|
| 6 | draft_decision | src/draft_decision.py | あり（Sonnet / 軽量 Haiku） |
| 7 | reflect | src/reflect.py | あり（rule で issue あれば LLM スキップ）|
| 8 | assemble | src/assemble.py | なし |

---

## 4. 動作確認

### 4-1. コンパイル + Mermaid グラフ

```bash
python -m py_compile agent.py src/*.py     # OK
python agent.py --config config/expense_policy.yaml --print-graph    # OK
```

### 4-2. dry-run（全 12 件）

```bash
python eval/run_eval.py
# → 11/12 pass / critical_miss 0 / cost $0
```

| ケース | 期待 | dry-run 実測 | 一致 |
|---|---|---|---|
| 01 travel_clean | auto_approve | auto_approve | ✓ |
| 02 meeting_clean | auto_approve | auto_approve | ✓ |
| 03 consumable_clean | auto_approve | auto_approve | ✓ |
| 04 hospitality_no_counterparty | needs_fix | needs_fix | ✓ |
| 05 consumable_no_preapproval | needs_fix | needs_fix | ✓ |
| 06 training_no_manager_approval | needs_fix | needs_fix | ✓ |
| 07 meeting_no_receipt | needs_fix | needs_fix | ✓ |
| 08 hospitality_per_person_over | needs_review | needs_review | ✓ |
| 09 hospitality_duplicate | needs_review | needs_review | ✓ |
| 10 travel_unusual_amount | needs_review | **auto_approve** | ✗ |
| 11 deadline_exceeded | reject | reject | ✓ |
| 12 single_person_hospitality_violation | reject | reject | ✓ |

→ case_10（高額タクシー）のみ rule-based では auto_approve に倒れる（LLM 起動時に格上げされる設計）。

---

## 5. 設計判断と実装上の工夫

- **コード優先のハードチェック**: `validate_rules` が LLM の前段で必ず通る → 致命リスク（不正 auto_approve）の防御線
- **軽量モード起動条件**: 違反 0 + 重複 0 + 金額 30,000 円以下 で自動起動（Haiku でコスト 1/5）
- **Reflection は rule-based 検査優先**: rule で issue 検出時は LLM 呼ばずコスト節約
- **PII マスキング**: 申請者氏名・社員 ID を placeholder に。LLM API には masked のみ送信
- **regex は YAML から**: テストドリフト防止
- **rule_trace を必ず記録**: 監査要件（spec §11-2）の絶対条件

---

## 6. 既知の課題（次の eval / v2 で潰す）

### 6-1. 高優先度

1. **case_10 高額タクシーが auto_approve に倒れる（dry-run）**: LLM 起動で needs_review に格上げされる想定だが、LLM 判定の信頼性次第
2. **regex / OCR 補完の精度**: v1 では payload が構造化済み前提。実 API では OCR 抜粋のみで不確実

### 6-2. 中優先度

3. **重複検出の粒度**: 同一店名・同一金額のみ。「90 日に 3 回連続」のような時系列パターン未対応（ユースケース 23 / v2 候補）
4. **case_01 travel_clean の Judge 低評価（3.0）**: 「金額が新幹線実勢と乖離」を Judge が指摘。OCR 検証層の追加が v2 課題

### 6-3. 低優先度

5. **コスト上限の即時ガード**: `cfg.cost.max_per_request_usd` 未実装
6. **logs ローテーション**: 1 実行 1 ファイル、日次ローテは deploy で

---

## 7. 動作させるコマンド

### dry-run（CI 用、API 消費なし）
```bash
cd workspace/expense_review_agent/v1/scripts
bash run.sh                             # 全 12 件
python eval/run_eval.py                 # コードベース評価サマリ
```

### 実 LLM（要 ANTHROPIC_API_KEY）
```bash
python eval/run_eval.py --real
python eval/judge.py --result-dir eval/results/run_*_real
```

### 単発実行
```bash
python agent.py --config config/expense_policy.yaml \
    --input eval/dataset/case_04_hospitality_no_counterparty/input.json \
    --case-id case_04 --dry-run
```

---

## 8. 品質ゲート

- [x] **prototype 完了**: コンパイル + dry-run 完走 + 11/12 期待一致 + 致命ミス 0
- [ ] **eval 完了**: 主要指標目標達成 → `/agent-eval` で計測（次タスク）
- [ ] **deploy β 開始** / **本番昇格**: v1 のスコープ外（必要なら別途 deploy 設計）

---

## 9. 次のアクション

```
/agent-eval
```

入力: `v1/scripts/` + `v1/eval/dataset/` の 12 件
期待出力: `v1/reports/eval_report.md`
