# evolve レポート — CS Triage Agent v1 → v2

**実行日**: 2026-05-08
**スキル**: `/agent-evolve`
**実行者**: agent-builder
**ベースライン**: `cs_triage_agent/v1/`（reports/eval_report.md 参照）

---

## 1. 動機

v1 eval（22 件 / 実 LLM）で発見した致命/品質低下要因を解消する v2 を実装し、回帰チェックを行う。

### v1 で発見した主要課題（eval_report.md §5 P0/P1）

| 優先度 | 課題 | v1 実測 |
|---|---|---|
| 🔴 P0 | case_21 で内部 CDN URL `https://cad.internal.example.com/...` を顧客本文に直接記載 | Judge 2.20/5（numerical_accuracy 1）|
| 🟡 P1 | DB 引き当て値（在庫数・納期・追跡 URL）を本文に明記せず曖昧化 | Judge info_completeness 2.95/5 |
| 🟡 P1 | complaint_smell=False のケースで apology 文言を入れすぎ | apology 偽陽性 5 件（実は eval 側の「お手数」誤分類）|
| 🟡 P1 | reflect の max_tokens=300 で JSON 切り詰めパース失敗 2 件 | rule-based fallback で実害は small |
| 🟡 P1 | ベテランらしさ（型番分解スキル）が本文に出ていない | Judge persona_fit 2.86/5 |
| 🟡 P1 | tech カテゴリの逆質問が空虚 | Judge コメント多数 |
| 🟢 P2 | 英語パスで型番抽出 / 内部メモ詳細化なし | Judge 2.20/5（業務に必要な情報が日本語で残っていない）|

---

## 2. 実装した変更（v1 → v2）

ノード分割・モデル選定・設計パターンは v1 と同じ。プロンプト / コードのみを修正。

| # | 変更点 | 影響ファイル | 種別 |
|---|---|---|---|
| 1 | **draft プロンプトに「DB 値積極記載」ルール追加**: 在庫数・納期日数・配達予定日・追跡番号は数値で本文に必ず明記。曖昧化禁止 | `src/draft.py`, `config/cs_triage.yaml` | プロンプト強化 |
| 2 | **complaint_smell=False 時の apology 禁止を明示**: 「申し訳」「お詫び」「ご迷惑」を本文禁止、「お手数」程度に留める | `src/draft.py`, `config/cs_triage.yaml` | プロンプト強化 |
| 3 | **CAD カテゴリ専用ルール: URL 顧客送出禁止**: 本文は「担当者より別途ご案内」テンプレ、URL は internal_memo のみ | `src/draft.py` | プロンプト強化 |
| 4 | **editorial.perspective に型番分解の具体例追加**: SHA-M6-20-N → M6/20/N、BLR-25-SUS304 → SUS304、PIN-D5-L20 → D5/L20 等 | `config/cs_triage.yaml` | ペルソナ強化 |
| 5 | **tech カテゴリで使用条件項目の例示**: 使用温度 / 薬品環境 / 荷重 / 精度グレード等から 2〜3 項目を逆質問 | `src/draft.py`, `config/cs_triage.yaml` | カテゴリ別プロンプト |
| 6 | **reflect の max_tokens 300 → 800**: v1 の JSON 切り詰めパース失敗を解消 | `src/reflect.py` | ハイパーパラメータ |
| 7 | **英語パス（transfer_en_node）で extract / 内部メモ詳細化**: regex で型番・注文番号を抽出し、internal_memo に日本語で残す | `agent.py` | 機能追加 |

`scripts/eval/run_eval.py` の apology 判定キーワードから「お手数」を除外（通常敬語の誤分類解消）。v1 にもバックポート。

---

## 3. 評価方法

v1 と同一の 22 件データセット（main 20 + edge 2）+ 同一設定で実行。コードベース評価 + LLM Judge（5 次元 1-5 採点）。

| 項目 | v1 | v2 |
|---|---|---|
| Agent コスト | $0.3713 | $0.4319 |
| Judge コスト | $0.1715 | $0.2152 |
| **合計** | **$0.5428** | **$0.6471** |
| 平均所要時間 | 18.9 秒 | 18.7 秒 |

> 注: v1 の eval スコアは「お手数」修正後（rescore.py で再採点）の値。v2 と同条件比較。

---

## 4. コードベース評価（v1 vs v2）

| 指標 | v1 | v2 | Δ |
|---|---|---|---|
| Pass 数（main 20）| 6 | **7** | +1 |
| カテゴリ正解率 | 75.0% | 75.0% | 0 |
| クレーム再現率 | 100.0% | 100.0% | 0 |
| クレーム適合率 | 75.0% | 75.0% | 0 |
| SKU 抽出再現率 | 100.0% | 100.0% | 0 |
| 必須テンプレ語含有率 | 100.0% | 100.0% | 0 |
| 禁止語違反 | 0 件 | 0 件 | 0 |
| **apology_match_rate** | 85.0% | **95.0%** | **+10pp** |
| SV 引き継ぎ一致率 | 95.0% | 95.0% | 0 |
| 致命ミス | 2 件 | 2 件 | 0 |

→ **コードベース指標では微改善**（apology 一致率 +10pp、pass 数 +1）。クレーム→shipment の致命ミスは未解消（プロンプト変更だけでは解けない、業務側でカテゴリ境界合意が必要）。

---

## 5. LLM Judge 5 次元採点（v1 vs v2）

| 次元 | v1 | v2 | Δ |
|---|---|---|---|
| persona_fit（ベテランらしさ）| 2.86 | **3.09** | **+0.23** |
| tone_appropriate（敬体・断定回避）| 3.91 | 3.45 | -0.46 |
| info_completeness（DB 値の活用）| 2.95 | **3.41** | **+0.46** |
| numerical_accuracy（捏造なし）| 4.05 | **4.23** | **+0.18** |
| ng_phrase_avoidance（NG 表現回避）| 4.05 | 3.86 | -0.19 |
| **Overall** | **3.56** | **3.61** | **+0.05** |

→ **改善**: persona_fit / info_completeness / numerical_accuracy（狙い通り）
→ **回帰**: tone_appropriate / ng_phrase_avoidance（apology 抑制で硬すぎ + 一部曖昧化が残る）

---

## 6. ケース別大幅変動（|Δ| ≥ 0.4）

### 改善 5 件

| ケース | v1 | v2 | Δ | 改善要因 |
|---|---|---|---|---|
| **case_21 edge_cad** | 2.20 | 3.60 | **+1.40** | 🔴 P0 修正成功。内部 URL 隠蔽 + 型番解説追加 |
| case_01 inventory_basic | 3.00 | 4.00 | +1.00 | DB 値（在庫数・納期日数）を本文に明記 |
| case_05 tech_material_diff | 4.00 | 4.80 | +0.80 | 逆質問が具体的（使用温度・荷重・精度）|
| case_03 inventory_zero_stock | 3.40 | 4.00 | +0.60 | 型番分解 + 営業日明示 |
| case_04 inventory_with_qty | 3.40 | 4.00 | +0.60 | 価格・納期・在庫を数値で明示 |

### 回帰 4 件

| ケース | v1 | v2 | Δ | 回帰要因（Judge コメント抜粋）|
|---|---|---|---|---|
| case_15 complaint_repeat | 3.60 | 2.80 | -0.80 | 「在庫待ち→入荷待ち」と曖昧化、PII 電話番号が本文に残った（unmask の仕様通りだが）|
| case_18 billing_reissue | 3.60 | 2.80 | -0.80 | 「発行までの目安日数を『しばらく』と曖昧化」 |
| case_13 ship_delay | 3.40 | 2.80 | -0.60 | 「資材の入荷待ち」が内部用語に近い |
| case_17 complaint_silent | 4.20 | 3.60 | -0.60 | tone やや硬化 |

→ 回帰の主因は **「DB 値積極記載」ルールが billing / shipment / complaint で徹底されず**、依然として曖昧化が残っているケース。プロンプト強化が片手落ち。

---

## 7. 評価まとめ（採否判断）

### 採用すべき改善

✅ **採用**（v3 main に取り込み価値あり）:
1. CAD URL 顧客送出禁止ルール（**P0 修正、致命改善**）
2. editorial.perspective の型番分解例（persona_fit +0.23）
3. tech カテゴリの逆質問項目例示（具体性向上）
4. reflect max_tokens 800（JSON 切り詰め消失）
5. 英語パスでの内部メモ詳細化（オペ視点で価値）
6. eval の apology kw から「お手数」除外（偽陽性解消、評価精度向上）

### 慎重に再検討

⚠️ **要再設計**（v3 で改善版を作る価値あり）:
7. **「DB 値積極記載」ルール**: 効いた case では大改善（case_01 +1.00）だが、billing/shipment/complaint で徹底されず曖昧化が残る
   - 対応案: カテゴリ別に prompt を分岐し、`必ず本文に書け` を category-specific に明示
8. **「complaint=False 時 apology 禁止」ルール**: tone_appropriate -0.46 の主因
   - 対応案: complaint=False でも 状況に応じて 「ご不便かと存じます」程度の共感を許容する。完全禁止は硬すぎ

---

## 8. v3 に向けた追加 P0/P1（次のイテレーション）

| # | 課題 | 対応案 | 期待効果 |
|---|---|---|---|
| 1 | クレーム→shipment の致命ミス（case_15, 16）| 業務側で「クレーム調 + 注文番号」のカテゴリルールを合意。LLM プロンプトに「complaint_smell=True なら category も complaint」と強制 | 致命ミス -2 件 |
| 2 | DB 値積極記載が billing/shipment/complaint で徹底されない | カテゴリ別 prompt 分岐（`prompts/inventory.j2`, `prompts/complaint.j2` 等）| info_completeness 3.41 → 4.0+ |
| 3 | 「ご不便かと存じます」程度の共感を許容する apology rule の柔軟化 | rules を「申し訳・お詫びは禁止、共感的表現は許容」に修正 | tone_appropriate 3.45 → 4.0+ |
| 4 | unmask 後の PII（電話番号）が本文に残ることへの再考 | spec.md §16-3 に「draft prompt では PII を含めない」を追加（unmask は最終 assemble だが、LLM に出さなくてもよい部分）| Judge コメント由来の不快感解消 |
| 5 | shipment / billing で「内部用語に近い表現」が混入 | 禁止語辞書を拡張（「資材」「対応中」を NG 語化）| ng_phrase_avoidance 3.86 → 4.0+ |

---

## 9. spec.md §16 の更新

§16-2 / §16-3 に v2 由来の項目を追記:

- 「DB 値積極記載」のカテゴリ別徹底度（業務側で billing/complaint も同じルールでよいか確認）
- apology 文言の範囲（「ご不便かと存じます」を許容するか）
- unmask 後の PII を本文に残すかのポリシー（draft 時は LLM に渡さず、本文には含めない選択肢）
- shipment / billing カテゴリの「内部用語」拡張定義

§16 累計: 業務 4 + 評価 12 + 実装 7 + 運用 1 = **24 項目**。/agent-deploy 前に /agent-discover 追加モードで一気に解消推奨。

---

## 10. v3 の方向性（agent-evolve スキル §1〜5 と対応）

evolve スキルが提案する 5 アプローチのうち、本案件の v2 は **Approach 3（プロンプト改善）** + **Approach 4（パラメータ調整 max_tokens）** に該当。

### v3 候補

- **Approach 2（モデル使い分け）**: classify を Haiku 4.5 に下げて軽量モード起動率を上げる。コスト 30〜60% 減期待
- **Approach 1（Multi-Agent）**: 致命ミス（complaint→shipment）解消のため、`Classifier` と `Reviewer` を分離してクレーム検出を強化
- **Approach 4.5（DSPy）**: persona/tone のプロンプト最適化。手書きで詰めるとプロンプト肥大化の罠に陥るので、評価駆動で最適化

→ **推奨**: まず **§8 の P0/P1 を v3 で潰し**、その上で Multi-Agent / DSPy を検討。Multi-Agent はコストが膨らむため業務 ROI 合意が先。

---

## 11. 動作させたコマンド（再現用）

```bash
# v2 setup
cd workspace/cs_triage_agent/v2/scripts
python -m py_compile agent.py src/draft.py src/reflect.py

# v2 dry-run
python eval/run_eval.py
# → eval/results/run_*_dry/

# v2 実 LLM
python eval/run_eval.py --real
# → eval/results/run_*_real/

# v2 LLM Judge
python eval/judge.py --result-dir eval/results/run_2026-05-08_155330_real

# rescore（apology 修正後の v1/v2 統一比較）
python eval/rescore.py --result-dir eval/results/run_2026-05-08_155330_real
```

---

## 12. 次のアクション

選択肢:

### A. v3 を切って §8 の P0/P1 を潰す
- カテゴリ別 prompt 分岐（DB 値徹底、apology rule 柔軟化）
- 業務合意必要なものは並行で /agent-discover 追加モード

### B. /agent-discover 追加モードで業務側合意取り
- §16 が 24 項目たまっており、v3 を作る前に整理した方が効率良い

### C. /agent-deploy へ進む
- v2 までの成果（P0 修正済、コード指標目標達成、Judge 3.61）を「許容範囲」と割り切り運用設計
- 残課題は本番ログから優先度付けし直し

→ **推奨: B → A の順**（業務合意なしに v3 を作っても再ぶれする可能性）。
