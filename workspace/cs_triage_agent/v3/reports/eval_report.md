# eval レポート — CS Triage Agent v1

**実行日**: 2026-05-08
**スキル**: `/agent-eval`
**実行者**: agent-builder

---

## 1. 入力 / 環境

| 項目 | 値 |
|---|---|
| 評価データセット | 22 件（main 20 + edge 2）。カテゴリ別 4/4/3/3/3/3 + cad/english エッジ |
| 設定 | `scripts/config/cs_triage.yaml`（Sonnet 中心、Reflection 1 回ループ）|
| モック DB | `scripts/eval/mock_db/`（在庫 6 / 価格 6 / 出荷 3 / 廃番 1 / CAD 3）|
| Agent モデル | classify / draft / reflect = Claude Sonnet 4.6 |
| Judge モデル | Claude Sonnet 4.6 |
| 実行モード | dry-run（コスト 0）+ real（API 起動）+ LLM Judge |

---

## 2. 成果物

| パス | 内容 |
|---|---|
| `scripts/eval/run_eval.py` | コードベース評価ランナ（dry-run / real 両対応）|
| `scripts/eval/judge.py` | LLM as a judge（5 次元 1-5 採点）|
| `scripts/eval/dataset/case_NN_*/` | 22 件のケース（input.txt + metadata.yaml）|
| `scripts/eval/results/run_2026-05-08_*_dry/` | dry-run 結果 |
| `scripts/eval/results/run_2026-05-08_152440_real/` | 実 LLM 結果 + Judge スコア |
| `reports/eval_report.md` | 本レポート |

---

## 3. メトリクス（spec.md §9 と対応）

### 3-1. コードベース評価（実 LLM）

| # | 指標 | 実測 | 目標 | 評価 |
|---|---|---|---|---|
| 1 | 型番抽出精度（再現率） | **100.0%** | 95% | ✅ |
| 2 | カテゴリ分類正解率 | **75.0%** | 90% | ❌ |
| 3 | クレーム再現率 | **100.0%** | 95% | ✅ |
| 4 | クレーム適合率 | **75.0%** | 60% | ✅ |
| 5 | 必須テンプレ語含有率 | **100.0%** | 100% | ✅ |
| 7 | 1 件コスト | **$0.0169** | $0.05 | ✅（設計試算 $0.027 より良い）|
| 8 | 平均所要時間 | **18.9 秒** | 30 秒 | ✅ |

ケースパス率（厳格基準）: 4/20 main pass = 20%。低い理由は「urgency ラベル」「apology 過剰検出」のため（後述）。

### 3-2. LLM Judge 5 次元採点

| 次元 | 平均 | 最小 |
|---|---|---|
| persona_fit（ベテランらしさ）| 2.86 | 1 |
| tone_appropriate（敬体・断定回避）| 3.91 | 3 |
| info_completeness（DB 値の活用）| 2.95 | 1 |
| numerical_accuracy（捏造なし）| 4.05 | 1 |
| ng_phrase_avoidance（NG 表現回避）| 4.05 | 3 |
| **Overall** | **3.56 / 5** | - |

→ 目標 4.0/5 に対し **0.44 ポイント不足**。低い 2 次元（persona_fit, info_completeness）が改善ターゲット。

### 3-3. コスト・時間

| 項目 | 値 |
|---|---|
| Agent 実行コスト（22 件）| $0.3713 |
| Judge コスト（22 件）| $0.1715 |
| **合計** | **$0.5428** |
| 平均所要時間 | 18.9 秒（外れ値: case_01 = 138 秒、初回 API レイテンシと推定）|

月間 6 万件想定のコスト試算（実測ベース）: **$1,014/月**（spec §10-1 上限 $3,000 の 34%）。

---

## 4. 失敗パターン分析

### 4-1. 致命ミス（critical_miss = 2 件）

| Case | 期待 → 実測 | 実害 | 対応優先度 |
|---|---|---|---|
| case_15 complaint_repeat | complaint → **shipment** | complaint_smell=True で SV 通知は出る。本文も「お詫び + 担当者引き継ぎ」が入っている。**実害は小さい**が分類の混在は気になる | 中 |
| case_16 complaint_urgent | complaint → **shipment** | 同上。本文は写真依頼・優先対応で良質 | 中 |

→ いずれも **complaint_smell + needs_supervisor フラグは正しく立っている**。LLM が「主カテゴリは shipment（注文番号がある）、副要素として complaint」と判断した解釈の差。実運用上の致命ではないが、SV ダッシュボード上での集計が崩れる懸念あり。

### 4-2. 「見かけ失敗」の主因

| 原因 | 件数 | 説明 |
|---|---|---|
| **urgency ラベル不一致** | 12 件 | LLM が `low` を返すのに metadata は `normal` 期待、など。ラベル定義が主観的すぎる（spec §16-3 で運用ルール化） |
| **apology 過剰検出** | 5 件 | 「お手数」「ご不便」は通常敬語として混入。これを apology と扱うか定義要 |
| **カテゴリ誤分類** | 5 件 | tech↔alternative↔cad の境界、complaint↔shipment の境界 |
| **complaint_smell False!=True** | 1 件 | case_13（配送遅延 3 日）は LLM が complaint を立てなかった |

### 4-3. LLM Judge が指摘した実質的な品質課題

判定コメントから抽出した重要な発見:

#### 🔴 P0（致命に近い）

1. **case_21 (CAD)** — 内部 CDN URL `https://cad.internal.example.com/...` を顧客本文に直接記載
   - Judge コメント: 「内部 CDN URL は送信禁止相当で即 1。内部メモの『外部公開可否確認要』を完全無視」
   - **対応**: cad ノードでは URL を内部メモのみに、本文は「担当者よりご案内」テンプレに変更する必要

#### 🟡 P1（品質低下要因）

2. **DB 値の本文未掲載**: 在庫数・営業日・配達予定日・追跡番号を本文に記載せず曖昧化（Judge: info_completeness 2.95）
   - 例: case_01「在庫数記載なし」、case_03「14 日が営業日か暦日か明示欠如」、case_16「DB 上の追跡番号・配送業者・配達予定日が本文に未記載」
   - **対応**: draft プロンプトに「DB 引き当て値（在庫数・納期日数・配達日・追跡番号）は数値で本文に必ず明記」と強制ルール追加

3. **ベテランらしさ不足**（Judge: persona_fit 2.86）
   - 例: case_03「型番読み込みや使用環境逆質問が皆無」、case_06「逆質問の中身が空虚」
   - **対応**: editorial.perspective に「型番分解の具体例（M6=ネジ径 / 20=長さ / N=ニッケル）」を注入、tech カテゴリのプロンプトに使用条件項目（温度・荷重・雰囲気・連続/間欠）の例示

4. **case_09 alt_discontinued でクレーム匂い False なのにお詫び混入**
   - Judge コメント: 「クレーム匂い False なのに冒頭謝罪を入れた点が減点」
   - **対応**: draft プロンプトで「complaint_smell=False の場合は apology 文言禁止」を明示

5. **case_22 英語パスの情報漏れ**
   - Judge: 「型番読解・逆質問・納期確認が皆無、転送一文のみで実質的な応答なし」
   - **対応**: 英語パスでも extract / category / DB 引き当ては並走させ、内部メモには日本語で詳細を残す（オペが英語担当へ転送する際の参考情報）

#### 🟢 P2（軽微）

6. **reflect の max_tokens=300 が小さすぎる**: 2 件で JSON 切り詰めパース失敗 → rule-based fallback（実害は small だが Reflection が機能していない）
7. **case_01 が 138 秒**: 初回 API レイテンシ外れ値の可能性。安定後の P95 は 25 秒以下に収まる見込み
8. **「ご確認のほど〜」の重複**: case_10 で末尾テンプレ語が重複（draft プロンプトの構成見直し）

---

## 5. 優先度付き改善提案（v1 → v2）

### 優先度 高（v2 必須、致命系）

1. **CAD URL の本文掲載廃止**: cad ノードでは URL を `internal_memo` のみに、`customer_body` は「CAD データは担当者よりご案内いたします」テンプレに変更
2. **draft プロンプトに「DB 値積極記載」強制ルール追加**: info_completeness 2.95 → 4.0 を狙う
3. **complaint_smell=false 時の apology 文言禁止をプロンプトで明示**
4. **reflect の max_tokens 300 → 800**: 切り詰めパース失敗を解消

### 優先度 中（v2 推奨、品質強化）

5. **editorial.perspective の強化**: 型番分解の具体例（SHA-M6-20-N → ネジ径/長さ/表面処理）を注入
6. **tech カテゴリのプロンプト強化**: 逆質問項目の具体例示（使用温度・荷重・雰囲気・連続/間欠 等）
7. **英語パスでの extract / classify / DB 引き当て並走**: 内部メモに日本語詳細を残す
8. **complaint と shipment の境界ルール化**: 業務担当と合意の上で「クレーム調 + 注文番号」をどちらに倒すか決定

### 優先度 低（v3 候補）

9. **urgency ラベル定義の明文化**: 業務側で low/normal/high の境界条件を合意
10. **apology rule の柔軟化**: 「お手数」「ご不便」を通常敬語として除外（eval 基準側）
11. **reflect_iter のカウント方法修正**: 増分タイミング見直し
12. **case_01 のレイテンシ外れ値調査**: 初回 LLM API call の warmup を要するなら preheat ロジック検討

---

## 6. v2 で測りたいメトリクス（追加）

- **DB 値の本文掲載率**（在庫数・納期日数・追跡番号の数値が本文に出ている割合）→ 目標 90%+
- **逆質問の質**（tech カテゴリで使用条件項目が言及される割合）→ 目標 80%+
- **CAD ノードでの URL 顧客送出件数** → 目標 0 件
- **complaint カテゴリ精度**（複合的な問い合わせをどう判定するか合意後）

---

## 7. spec.md §16 として上げた未確定項目（追加分）

§16-3 / §16-3-A に追記済み:

- urgency ラベル定義（業務担当と合意）
- 「お手数」「ご不便」を apology 扱いにするか
- complaint × shipment の境界
- CAD URL の顧客送出可否（IT セキュリティ / 法務確認）
- draft の「DB 値積極記載」ポリシー（在庫数の漏洩リスク vs 利便性）
- dry-run / real の運用境界（CI = dry-run、リリース判定 = real）

§16 累計: 業務 4 + 評価 8 + 実装 7 + 運用 1 = **20 項目**。

→ **`/agent-deploy` の前に `/agent-discover` を追加モードで呼ぶ** ことを強く推奨。

---

## 8. dry-run vs real の運用境界（スキル側に明示なし）

agent-eval スキル本体には「dry-run はいつ使うか」が明示されていないため、本プロジェクトでの運用ルール案を以下に示す:

| フェーズ | モード | 理由 |
|---|---|---|
| 開発中の動作確認 / CI | **dry-run** | コスト 0、構造検証専用。pass 判定は category/SKU/order/template 含有のみで OK |
| eval イテレーション（v1 / v2 / v3 ...）| **real LLM**（22 件 ≈ $0.4 + Judge $0.2）| 質的判定込みで LLM Judge も併走 |
| シャドー / β デプロイ判定 | **real LLM** + 手動レビュー | spec §13-4 品質ゲート参照 |
| 本番昇格判定 | **real + 1 ヶ月シャドー** | 実トラフィックでの誤回答ゼロ確認 |

---

## 9. 動作させたコマンド（再現用）

```bash
cd workspace/cs_triage_agent/v1/scripts

# dry-run（コスト 0）
python eval/run_eval.py
# → eval/results/run_*_dry/ に summary.md と result.json

# 実 LLM（コスト発生、要 ANTHROPIC_API_KEY）
python eval/run_eval.py --real
# → eval/results/run_*_real/ に保存

# LLM Judge（要 real eval 結果）
python eval/judge.py --result-dir eval/results/run_2026-05-08_152440_real
# → eval/results/run_*_real/judge_*.json

# 一部 case のみ
python eval/run_eval.py --real --only case_15_complaint_repeat case_16_complaint_urgent
```

---

## 10. 次のアクション

選択肢:

### A. /agent-evolve で v2 を試行
P0/P1 の改善を実装した v2 を切る:
- CAD URL 顧客送出廃止
- draft の DB 値積極記載ルール
- complaint=false 時の apology 禁止
- reflect max_tokens 拡張
- editorial.perspective の型番分解例追記

### B. /agent-discover を追加モードで呼んで §16 を解消
業務側合意を先に固める:
- urgency 定義
- complaint × shipment 境界
- CAD URL ポリシー
- DB 値記載ポリシー
- 運用境界（dry-run / real）

### C. /agent-deploy へ進む
本 v1 を「合格基準を満たす範囲」と割り切って運用設計に入る:
- コスト・速度・SKU 抽出・クレーム再現は目標達成
- カテゴリ正解率 / Judge スコアは v2 改善対象として運用後にチューニング

→ **推奨: B → A の順**（業務合意なしに v2 を作っても再ぶれする）。
