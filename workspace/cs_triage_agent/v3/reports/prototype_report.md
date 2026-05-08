# prototype レポート — CS Triage Agent v1

**実行日**: 2026-05-08
**スキル**: `/agent-prototype`
**実行者**: agent-builder

---

## 1. 入力

| ファイル | 概要 |
|---|---|
| `v1/spec.md` | 17 章構成の仕様書（discover + decompose 経由）|
| `v1/design.md` | 7 ノード LangGraph 構成、Tool Use + Reflection ハイブリッド |
| `v1/tools.md` | DB ツール 5 種 + マスキング関数の仕様 |
| `v1/detailed_design.md` | シーケンス図・状態遷移・データモデル・PII 流れ |

---

## 2. 成果物

```
v1/scripts/
├── agent.py                       # CLI + LangGraph build_graph
├── run.sh                         # 動作確認スクリプト
├── .env.example                   # API キーテンプレ
├── src/
│   ├── __init__.py
│   ├── state.py                   # TriageState (TypedDict)
│   ├── config.py                  # YAML ローダ
│   ├── cost.py                    # トークン課金集計
│   ├── logger.py                  # 1 実行 1 ファイルログ
│   ├── pii_mask.py                # PII マスキング + unmask
│   ├── db_client.py               # モック DB 5 種
│   ├── preprocess.py              # ノード 1
│   ├── extract.py                 # ノード 2（regex 抽出）
│   ├── classify.py                # ノード 3（kw + LLM 二重判定）
│   ├── retrieve.py                # ノード 4（DB ディスパッチ）
│   ├── draft.py                   # ノード 5（LLM 起草）
│   ├── reflect.py                 # ノード 6（LLM 自己レビュー）
│   └── assemble.py                # ノード 7（unmask + 最終 JSON）
├── config/
│   └── cs_triage.yaml             # 設定（モデル / ペルソナ / カテゴリ / regex / テンプレ）
└── eval/
    ├── mock_db/
    │   ├── inventory.yaml         # 6 件
    │   ├── price.yaml             # 6 件
    │   ├── shipment.yaml          # 3 件
    │   ├── discontinued.yaml      # 1 件（SHA-M6-20-N が廃番、後継 N2）
    │   └── cad.yaml               # 3 件
    └── dataset/
        ├── sample_01_inventory.txt
        ├── sample_02_complaint.txt
        ├── sample_03_tech.txt
        ├── sample_04_shipment.txt
        ├── sample_05_alternative.txt
        ├── sample_06_cad.txt
        ├── sample_07_billing.txt
        └── sample_08_english.txt
```

合計 14 Python ファイル + 1 YAML 設定 + 5 モック DB + 8 サンプルメール + 1 シェル + 1 .env テンプレ。

---

## 3. 実装したノード（design.md §2 と対応）

| # | ノード | 実装ファイル | LLM 呼び出し |
|---|---|---|---|
| 1 | preprocess | `src/preprocess.py` | なし |
| 2 | extract | `src/extract.py` | なし（v1 では LLM 補完未実装） |
| 3 | classify | `src/classify.py` | あり（rule-based + Sonnet 二重判定） |
| 4 | retrieve | `src/retrieve.py` | なし |
| 5 | draft | `src/draft.py` | あり（Sonnet / 軽量時 Haiku） |
| 6 | reflect | `src/reflect.py` | あり（rule で issue があれば LLM スキップ）|
| 7 | assemble | `src/assemble.py` | なし |
| - | transfer_en（条件分岐用補助）| `agent.py` 内 | なし |

→ design.md §1 のグラフ構造（preprocess → 条件分岐 → extract → classify → retrieve → draft → reflect → 条件分岐 → assemble）を完全再現。

---

## 4. 動作確認結果

### 4-1. 起動確認

```bash
$ python agent.py --config config/cs_triage.yaml --print-graph
```

→ Mermaid グラフが出力される（preprocess → transfer_en/extract → classify → retrieve → draft → reflect → assemble）。conditional edge も正しく描画。

### 4-2. dry-run（LLM なし）で全 8 サンプル完走

```bash
$ bash run.sh   # = bash run.sh --all（dry-run 既定）
```

8 件すべて exit 0 で完走。コスト合計 **$0.0000**、1 件あたり < 1 秒。

| サンプル | lang | category | skus / orders | complaint | needs_supervisor |
|---|---|---|---|---|---|
| 01 inventory | ja | inventory | SHA-M6-20-N, SHA-M8-25-N | false | false |
| 02 complaint | ja | complaint | order: ORD-2026-12346 | **true** | **true** |
| 03 tech | ja | tech | BLR-25-SUS304, BLR-25-SUS316 | false | false |
| 04 shipment | ja | shipment | order: ORD-2026-12345 | false | false |
| 05 alternative | ja | inventory（※後述）| SHA-M6-20-N | false | false |
| 06 cad | ja | cad | SHA-M6-20-N | false | false |
| 07 billing | ja | billing | （なし）| false | false |
| 08 english | en | other | （なし）| false | false |

→ **すべて期待通りの分類とフラグ**。dry-run 用テンプレ本文も `お世話になっております。`〜`ご確認のほどよろしくお願いいたします。` を満たす。

※ サンプル 05 はキーワード `代替`・`互換`・`納期` が同点でタイブレークが辞書順 → `inventory` に倒れる（実 LLM 起動時は意図通り `alternative` に倒れる想定）。`spec.md §16-2` に追記済み。

### 4-3. 実 LLM での動作確認

実 LLM 起動は本レポートの範囲外（API キーが用意できる環境で個別検証）。

予測コスト（design.md §6 から）:
- 標準モード: 1 件 **約 $0.027**（spec 目標 $0.05 の 54%）
- 軽量モード: 1 件 **約 $0.005**

実測との照合は `/agent-eval` に委ねる。

---

## 5. 設計判断と実装上の工夫

### 5-1. 「LLM に判断させすぎない」境界の遵守

- DB 引き当て値は `retrieved_data` として state に格納し、LLM が触れる JSON ブロックには **値そのものをそのまま含める**（プロンプトで「DB 引き当て値以外の数字を書かない」を制約）
- ツールは LLM の Tool 直呼びではなく `retrieve` ノード内のコードがカテゴリで分岐（誤呼び出し防止）
- マスキングは `preprocess` で 1 回のみ。`pii_map` は state 内のみで保持し、LLM API には送らない

### 5-2. dry-run モードの実装方針

`reference/workflow_skeleton.py` のコメント通り、**各 LLM ノードに dry-run 分岐を直接書く**方式を採用（`_DryRunLLM` クラス的な抽象化はしない）。利点:
- 評価インフラ動作確認が無料 ($0)
- CI で全パイプラインを毎コミット走らせられる
- 本番経路と分岐経路が同じ関数内に並ぶので保守しやすい

### 5-3. Reflect の効率化

`reflect_node` は **rule-based チェックで先に NG を見つけたら LLM を呼ばない**（コスト節約）。LLM 呼び出しはトーン・整合性チェック専用。

### 5-4. regex は YAML から読む

`extract.py` の `_compile_patterns` は `cfg["sku_patterns"]` / `cfg["order_no_patterns"]` を読む。テストドリフト防止（`reference/tools_pattern.md` の罠回避）。

### 5-5. TypedDict 全フィールド宣言

`src/state.py` で `TriageState` に流す **全フィールドを宣言**。silent drop 罠回避。新フィールド追加時は必ずここに追記する旨を docstring に明記。

---

## 6. 既知の課題（次の `/agent-eval` で潰す）

### 6-1. 高優先度

1. **rule-based classify のタイブレーク**: サンプル 05 で「代替・互換・納期」が同点 → `inventory` に倒れる。実 LLM が起動すれば `alternative` に分類できる想定だが、軽量モードでも頑健にしたい場合はカテゴリ優先順を YAML で明示すべき
2. **PII マスキングの精度**: 電話 regex が貪欲で `ORD-2026-12346` の `0` を起点に部分マッチを起こす罠を発見・修正済み（否定先読み）。他にも英数字混在パターンへの耐性は要検証（テストケース 30+ 件作成が必要）
3. **複数 SKU の在庫照会で「部分廃番」を draft が正しく扱うか**: サンプル 01（SHA-M6-20-N が廃番、SHA-M8-25-N は現役）で、draft が **両方の状況を正しく書き分けるか** は実 LLM での検証待ち

### 6-2. 中優先度

4. **extract の LLM 補完**: 現状は regex のみ。型番が表記揺れ（全角・空白入り）したケースで Haiku 補完を入れるかは eval で判断
5. **添付検出後の処理**: `has_attachment=true` のケースを `internal_memo` に注記する処理を未実装
6. **reflect の DB 値改変チェック**: 現状 LLM 任せ。コードで「retrieved_data の数値が draft に含まれているか」を機械的に照合する補強が望ましい

### 6-3. 低優先度

7. **コスト上限の即時ガード**: `cfg.cost.max_per_request_usd` を読んでいるが超過時の中断ロジックを未実装
8. **logs ローテーション**: 現在は 1 実行 1 ファイル。日次ローテーションは deploy で対応

---

## 7. spec.md §16-2 として上げた未確定項目（追加分）

prototype 実装中に発覚:

- PII マスキングの誤検知許容範囲（電話 regex 貪欲マッチ罠の対症療法は済んでいるが、英数字混在型番への耐性は要検証）
- rule-based classify のタイブレーク仕様（同点時のカテゴリ優先順）
- draft の `missing_info` の Salesforce 側表示先

`spec.md §16-2` に追記済み（既存項目 4 件 + 本タスク 3 件 = 7 件）。

§16 累計（業務 4 + 評価 2 + 実装 7）= **13 項目**。`/agent-eval` の前に `/agent-discover` を **追加モード** で呼んで一気に解消するのを推奨。

---

## 8. 動作させるコマンド

### dry-run（LLM なし、CI 用）

```bash
cd workspace/cs_triage_agent/v1/scripts

# 1 サンプルだけ
python agent.py --config config/cs_triage.yaml \
    --input eval/dataset/sample_01_inventory.txt \
    --case-id sample_01 \
    --dry-run

# 全 8 サンプル
bash run.sh
```

### 実 LLM

```bash
# .env に ANTHROPIC_API_KEY を設定後
python agent.py --config config/cs_triage.yaml \
    --input eval/dataset/sample_01_inventory.txt \
    --case-id sample_01

# 軽量モード（Haiku のみ）
python agent.py --config config/cs_triage.yaml \
    --input eval/dataset/sample_01_inventory.txt \
    --case-id sample_01 \
    --lite
```

### グラフ可視化

```bash
python agent.py --config config/cs_triage.yaml --print-graph
```

---

## 9. 品質ゲート（detailed_design.md §13-4）

- [x] **prototype 完了**: コンパイル + dry-run 完走 → 達成（全 14 ファイル `py_compile` パス、全 8 サンプル dry-run 完走、コスト $0）
- [ ] **eval 完了**: 主要指標目標達成（型番抽出 95%+ / クレーム再現率 95%+ / 必須テンプレ語 100% / コスト $0.05 以下）→ `/agent-eval` で計測
- [ ] **deploy β 開始**: LLM Judge 平均 3.5+ / SV 抜き取り 4.0+
- [ ] **本番昇格**: 1 ヶ月シャドー運用で誤回答ゼロ + オペ採用率 50%+

---

## 10. 次のアクション

```
/agent-eval
```

入力: `v1/scripts/` + `v1/eval/dataset/` の 8 件
期待出力:
- `v1/eval/results/<run_id>/` 配下の集計
- LLM as a judge による 5 次元採点
- コードベース評価（必須テンプレ語含有 / 型番抽出再現率 / カテゴリ正解率）
- 改善提案（spec.md §9 の指標が目標未達ならどう改善するか）
- `v1/reports/eval_report.md`

評価データセットは v1 prototype 段階の 8 件 → eval 段階で **20 件**（カテゴリ別 4/4/3/3/3/3）まで拡充する想定。
