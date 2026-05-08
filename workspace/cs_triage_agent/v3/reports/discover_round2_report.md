# discover Round-2 レポート — CS Triage Agent §16 解消

**実行日**: 2026-05-08
**スキル**: `/agent-discover` (Mode B: 追加ヒアリングモード、第 2 回)
**実行者**: agent-builder
**出力**:
- `data/hearing_round_2.md`（5 ペルソナ × 23 項目のラウンドテーブル議事録）
- `v2/spec.md` §16 全項目に取消線 + [解消: 第2回] 付与、§17 改訂履歴に追記
- 本レポート

---

## 1. 入力（解消対象）

| §16 セクション | 項目数 | 主な内容 |
|---|---|---|
| 16-1 業務上 | 4 | DB スキーマ / 多言語判定 / 添付扱い / コンテキスト依存質問 |
| 16-2 実装上 | 7 | DB SDK / SF カスタムフィールド / Slack / customer_id / PII regex / classify タイブレーク / missing_info 表示先 |
| 16-3 評価品質 | 7 | クレーム閾値 / PII 厳密さ / urgency 定義 / apology 扱い / complaint×shipment / CAD URL / DB 値ポリシー |
| 16-3-A 運用境界 | 1 | dry-run vs real |
| 16-3-B v3 evolve 由来（追加分）| 4 | DB 値カテゴリ別 / apology 共感許容 / unmask PII 露出 / 内部用語拡張 |
| **合計** | **23** | |

---

## 2. ヒアリング体制（5 ペルソナ）

| ペルソナ | 役割 | 主担当領域 |
|---|---|---|
| 富田（CS センター長）| KPI / 運用 / 業務カテゴリ境界 | A-1, A-2, A-3, A-4, A-5, A-7, A-10, B-5, C-1 |
| 中島（ベテランオペ）| 業務細目 / 内部用語 / DB 値の妥当感 | A-2, A-4, A-5, A-6, A-7, A-8, A-9 |
| 阿部（SV、新規）| クレーム閾値 / SV 通知集中対策 | A-4, A-5, A-6, A-7, C-1 |
| 大野（法務、新規）| PII / CAD URL / GDPR / 改正個情法 | B-1, B-2, B-3, B-4 |
| 工藤（SE、新規）| DB 接続 / SF / Slack / Okta / SDK | A-1, A-3, A-9, A-10, B-2, B-5, D-1〜D-5 |

合議形式: 4 章 × 60 分のラウンドテーブル。最終的に 23/23 項目を解消。

---

## 3. 解消結果（カテゴリ別の主な合意）

### 3-1. 業務カテゴリと判定ルール

| 項目 | 合意 |
|---|---|
| **多言語判定** | `langdetect` 導入、ja 確率 0.6 で日本語判定。短文+型番のみは ja 扱い |
| **添付ファイル** | 検出済の場合は internal_memo に手動確認依頼を必ず付与 |
| **ケース履歴遡及** | v3 で `lookup_case_history(case_id, days=30)` 新ツール追加。case_id × メールアドレス整合性チェック必須 |
| **complaint × shipment** | complaint_smell=True なら category=complaint 優先。retrieve では lookup_shipment 並走 |
| **urgency 定義** | high=至急/今日中/製造ライン停止/配送 24h 超 / normal=24h 以内 / low=お時間あるとき |
| **apology 範囲** | 禁止語=申し訳/お詫び/ご迷惑、許容語=お手数/ご不便/ご心配（共感は OK）|
| **DB 値カテゴリ別** | 在庫数=レベル感 / 納期=数値+営業日 / 配達=YYYY-MM-DD / 追跡=URL+業者名のみ / 価格=税抜単価 / 廃番=後継+理由 |
| **内部用語禁止辞書** | 8 語追加、reflect でハードチェック + 1 回再起草 |
| **rule-classify 優先順位** | `[complaint, alternative, shipment, billing, tech, cad, inventory, other]` |
| **missing_info 表示先** | SF `AI_Draft__c.missing_info_json__c`（Long Text 32k）|

### 3-2. PII / セキュリティ

| 項目 | 合意 |
|---|---|
| **PII マスキング厳密さ** | 顧客氏名は完全マスク（`[NAME_001]`）。部分マスクはグレー |
| **誤検知許容範囲** | recall 100% 必達、precision 80% で OK。v3 で単体テスト 50+ 件整備 |
| **unmask 後 PII の本文露出** | draft プロンプトには placeholder のまま渡し、Judge も placeholder のまま採点。assemble の最終段でのみ unmask |
| **CAD URL** | 内部 CDN URL は絶対送付禁止（v2 方針継続）。短期署名 URL は v4 |
| **dry-run vs real** | CI=dry-run / PR=dry-run+失敗 5 real / リリース=real 全件+Judge / シャドー=real |

### 3-3. クレーム閾値

| 項目 | 合意 |
|---|---|
| **クレーム匂い閾値** | recall 95%+ 必達、precision 60%+ 目標。v2 実測（recall 100% / precision 75%）は SV ライン許容 |

### 3-4. 実装・運用（deploy フェーズ持ち越し）

| 項目 | 担当 |
|---|---|
| 本番 DB スキーマ確定 | SE 工藤（deploy） |
| DB SDK 実装（psycopg2 + requests）| SE 工藤（deploy） |
| SF カスタムオブジェクト `AI_Draft__c` 作成 | SE 工藤（deploy） |
| Slack bot トークン / チャンネル | SE 工藤（deploy） |
| customer_id 取得経路（SF Account.Custom_Customer_Code__c）| SE 工藤（deploy） |

---

## 4. v3 を切るべきかの判断

### 判定基準（CLAUDE.md / ops_iteration.md）
> ノード追加・パターン変更・モデル変更 → 新バージョン
> キーワード追加・プロンプトの数語修正 → 同バージョン内更新

### 本ヒアリングの実装変更スコープ

| 変更レベル | 件数 | 主な内容 |
|---|---|---|
| **ノード追加** | 1 | `lookup_case_history` ツールを retrieve ノードに追加（条件付き呼出）|
| **パターン変更（プロンプトの大改修）**| 7 | DB 値カテゴリ別 / apology 範囲 / complaint 優先 / 内部用語禁止 / urgency ルール / category_priority / 添付通知 |
| **コード変更（src/）**| 4 | langdetect 導入 / draft で PII マスク維持 / lookup_case_history 実装 / reflect の禁止語ハードチェック |
| **メタデータ変更** | 1 | eval/dataset の urgency / apology を §A-5 / §A-6 ルールに揃え |
| **仕様変更（spec.md）** | 全章影響 | §5 / §6 / §10-3 / §11-3 / §16 を update |

→ **ノード追加 1 件 + プロンプト大改変 7 件のため、v3 を切る** のが適切。

→ deploy フェーズ持ち越しの 4 項目（DB / SF / Slack / customer_id）は **v3 のスコープ外**として `/agent-deploy` で対応。

---

## 5. v2 → v3 の具体的な実装計画（プロンプト・コード）

### 5-1. config/cs_triage.yaml

```yaml
# v3 で追加・変更
category_priority:           # NEW: rule-classify タイブレーク用
  - complaint
  - alternative
  - shipment
  - billing
  - tech
  - cad
  - inventory
  - other

forbidden_internal_phrases:  # NEW: reflect でハードチェック
  - 工場の都合
  - うちのシステム
  - 上が言うには
  - 弊社内事情
  - 資材の入荷
  - 対応中
  - 社内検討中
  - 営業に確認

urgency_rules:               # NEW: classify と eval メタの両方で参照
  high_keywords: ["至急", "今日中", "明日まで", "製造ライン停止"]
  high_complaint_smell: true
  high_delivery_delay_hours: 24
  low_keywords: ["お時間あるとき", "いずれ", "お時間のあるときに"]

apology:                     # NEW: rules で参照
  forbidden_when_no_complaint: ["申し訳", "お詫び", "ご迷惑"]
  allowed: ["お手数", "ご不便", "ご心配"]

db_value_policies:           # NEW: カテゴリ別 DB 値記載ルール
  inventory:
    stock_qty: level_only          # 「充足/部分/欠品」
    ship_eta_days: number_with_unit  # 「14 営業日」
    price: tax_excluded_number     # 税抜単価
  shipment:
    estimated_delivery: date       # YYYY-MM-DD
    tracking_url: url_with_carrier # URL + 業者名のみ。追跡番号は internal_memo
  alternative:
    successor_sku: with_reason     # 後継品 + 理由必須

editorial:
  rules: |
    - 推測で断定しない
    - DB 値はカテゴリ別ポリシー（db_value_policies）に従う
    - complaint=False なら apology.forbidden_when_no_complaint の語は禁止、apology.allowed は許容
    - CAD URL は本文禁止、internal_memo のみ
    - forbidden_internal_phrases に該当する語は禁止
    - 一文 60 文字以内、敬体・丁寧語
```

### 5-2. src/preprocess.py
- `langdetect` ライブラリ使用（既存の正規表現判定をフォールバックに残す）

### 5-3. src/draft.py
- **PII マスク維持**: draft プロンプトには placeholder のまま本文を生成させる
- **カテゴリ別 prompt 分岐**: `prompts/inventory.j2`, `prompts/complaint.j2` 等のテンプレートエンジン化検討（または draft.py の if 分岐を厚くする）
- DB 値カテゴリ別ポリシーをプロンプトに注入

### 5-4. src/retrieve.py
- `lookup_case_history(case_id, days=30)` を新ツール追加
- complaint_smell=True のとき lookup_shipment も並走

### 5-5. src/reflect.py
- 禁止語ハードチェック（`forbidden_internal_phrases` 該当があれば reflect_pass=False）

### 5-6. src/db_client.py
- `lookup_case_history` モック実装（v3）+ 実装（deploy）

### 5-7. eval/run_eval.py
- urgency ルール、apology kws を §A-5 / §A-6 に揃える（既に「お手数」除外済 → 「ご不便」「ご心配」も除外）

### 5-8. assemble.py
- unmask は最終段（既存仕様、変更なし）

### 5-9. eval/dataset/case_NN/metadata.yaml
- urgency / apology 期待値を §A-5 / §A-6 ルールに揃える（特に case_05/06/08/11/13/16/17/18/20/21）

### 5-10. v3/spec.md
- §5 編集者の視点 / §6 執筆ルール を ヒアリング結果反映
- §10-3 セキュリティ（PII 完全マスク、CAD URL 禁止を明記）
- §11-3 変更管理（dry-run / real 運用境界、CI ルール）
- §16 を空にして再カウント開始

---

## 6. 期待される v3 改善（仮説）

| 指標 | v2 実測 | v3 期待 | 改善要因 |
|---|---|---|---|
| カテゴリ正解率 | 75.0% | **90%+** | category_priority + complaint 優先 |
| 致命ミス | 2 | **0** | complaint→shipment 解消 |
| 必須テンプレ語含有率 | 100% | 100% | 維持 |
| Judge persona_fit | 3.09 | **3.5+** | 型番分解の徹底（既出）+ 経験的逆質問の固定化 |
| Judge tone_appropriate | 3.45 | **3.9+** | apology 範囲の柔軟化（共感許容）|
| Judge info_completeness | 3.41 | **4.0+** | DB 値カテゴリ別ポリシー徹底 |
| Judge ng_phrase_avoidance | 3.86 | **4.2+** | 内部用語禁止ハードチェック |
| Judge overall | 3.61 | **4.0+** | 上記の合算 |
| 1 件コスト | $0.0196 | $0.022〜0.025 | プロンプト長増加で +10〜30%、許容範囲 |

---

## 7. §16 累計と今後

第 2 回ヒアリング後の §16 状況:
- 解消済: **23 項目**（ヒアリングで全件合意）
- 残存（v3 で発覚を見込む）: **未確定**（v3 prototype/eval 後に新規発覚を想定）

→ v3 進行中は §16 をリセットして再カウント開始。次回追加ヒアリング（第 3 回）は v3 eval 後を想定。

---

## 8. 次のアクション

```
/agent-evolve
```

入力:
- `v2/spec.md` + `data/hearing_round_2.md` + `v2/reports/evolve_report.md §8`
- 期待出力: v3 ディレクトリ（spec.md / scripts / config / eval メタデータ更新）+ `v3/reports/evolve_report.md`

実装スコープ（§5 で詳述）:
- プロンプト 7 種改修（YAML + draft.py）
- コード 4 種改修（preprocess: langdetect / draft: PII placeholder 維持 / retrieve: lookup_case_history / reflect: 禁止語ハードチェック）
- eval メタ更新 + 22 件再評価で v1/v2/v3 三元比較

予想評価コスト: agent $0.4 + Judge $0.2 = **$0.6**（v2 と同等）

---

## 9. ヒアリング Mode B のメタ振り返り

今回 23 項目を 1 回のラウンドテーブルで全件解消できた要因:

1. **§16 が「業務 / 実装 / 評価 / 運用」の 4 軸で整理されていたため、対応すべきペルソナを即座に決められた**
2. **eval_report / evolve_report に「業務側で要合意」と明記された項目だけが §16 に上がっていたため、技術的な選択肢混入が少なかった**
3. **5 ペルソナのラウンドテーブル形式により、相互依存項目（例: PII マスキング厳密さ × draft の PII 露出）が一気に整合的に決められた**

逆に、複数ペルソナをまたぐ討議をシミュレートできない実プロジェクトでは、§16 解消は **複数回のヒアリングに分散** する可能性が高い。スキル側（agent-discover Mode B）の「1 セッションで全部解消」という想定は **理想形**であり、実運用では半分程度ずつ消化していく形になる見込み。

---

## 10. 注意：本ヒアリングは合成データ

実プロジェクトでは、実在する CS センター長 / SV / 法務 / SE による直接ヒアリングが必要。
本リポジトリは合成データなので、ペルソナの判断はあくまで「想定される現実的な判断」を Claude が代弁したものに過ぎない。

deploy 前に、実在する関係者によるレビュー・補正が **必須**。
