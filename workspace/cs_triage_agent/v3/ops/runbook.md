# 障害対応ランブック — CS Triage Agent v3

> 本番運用時に発生しうる障害シナリオ別の対応手順。`reference/runbook_template.md` をベースに本プロジェクト固有の値で具体化。

---

## アラート Severity 分類

| Severity | 例 | 通知先 | 対応時間 |
|---|---|---|---|
| **CRITICAL (P1)** | PII 漏えい / 内部 URL 顧客送出 / DB 完全停止 / 月予算 95% 到達 | PagerDuty + 電話（オンコール SE）| 15 分以内 |
| **HIGH (P2)** | LLM API エラー率 > 5% / 主要機能不全 / クレーム見逃し疑い | Slack `#cs-system-alerts` + メール | 1 時間以内 |
| **MEDIUM (P3)** | 応答時間 P95 超過 / 月予算 80% 到達 / fallback 件数の倍増 | Slack `#cs-system-alerts` | 営業日中 |
| **LOW (P4)** | 統計指標の異常（採用率低下傾向 / 過検知率上昇）| Slack（静か）| 週次レビュー |

---

## ランブック 1: PII / 機密情報の漏えい疑い (CRITICAL)

### 検知
- メトリクス: `cs_triage.pii_warning_count{*} > 0`
- ログに `CRITICAL: PII unmaskable_pattern_at_pos_*` または `WARNING: pii_map __warning__`

### 対応手順
```bash
# 1. PagerDuty incident をオンコールが受信、Slack #cs-system-alerts で対象 case_id を確認
# 2. 該当ケースを SF で開き、AI_Draft__c の customer_body__c に PII が露出していないか目視確認
# 3. もし露出していたら：
#    a. SF の Apex 経由で AI_Draft__c のレコードを soft-delete（Apex script）
#    b. オペに「該当ドラフトを使うな」を Slack DM
#    c. すでに送信されていた場合、CS センター長へエスカレ + 顧客に謝罪メール手配
# 4. ログから検出失敗箇所を特定:
kubectl logs -n cs-triage-prod -l app=agent-worker --tail=500 | grep <case_id>
# 5. PII regex の不足を src/pii_mask.py に追加
# 6. tests/test_pii_mask.py に該当ケースのテストを追加
# 7. Helm デプロイ → 同一ケースで再実行確認
# 8. インシデント振り返り: 24 時間以内に postmortem 作成
```

### 復旧後の予防策
- [ ] 同パターンの別ケースが過去 24 時間に漏れていないか全件検索
- [ ] PII regex の網羅性を強化（姓辞書ベース等）
- [ ] 月次 PII 監査の精査閾値を見直し
- [ ] 法務 大野に状況報告 + 改正個情法的な届出要否確認

---

## ランブック 2: 内部 URL（CAD CDN 等）顧客送出 (CRITICAL)

### 検知
- メトリクス: `cs_triage.internal_url_in_body_count{*} > 0`（v3 で追加予定の検出）
- 法務監査 / オペ報告

### 対応手順
```bash
# 1. 該当ケースを SF で開き本文確認
# 2. もし送信前なら：AI_Draft__c の customer_body__c から URL を削除、
#    オペに「URL は内部 only、本文は『担当者よりご案内』テンプレに置換せよ」を指示
# 3. もし送信済なら：CS センター長へエスカレ + IT セキュリティ報告
# 4. config/cs_triage.yaml の db_value_policies.cad.cad_url が internal_memo_only に
#    なっているか確認
# 5. draft.py のプロンプト制約「CAD URL は internal_memo のみ」が機能しているか
#    debug ログで確認（`grep "url" draft response`）
# 6. 必要なら reflect.py に `forbidden_internal_phrases` として URL パターンを追加して
#    ハードチェック化
```

### 復旧後の予防策
- [ ] reflect.py に regex `r"https?://[^/]+\.internal\."` を `forbidden_internal_phrases` 同等で追加
- [ ] 同パターンの過去 30 日のケースを SF から SOQL で抽出して全件確認

---

## ランブック 3: LLM API ダウン (HIGH)

### 検知
- メトリクス: `cs_triage.llm_api_5xx_rate > 5%` for 10min
- Anthropic status page (https://status.anthropic.com) で障害確認

### 対応手順
```bash
# 1. Slack #cs-system-alerts でアラート確認 + status page 確認
# 2. fallback テンプレ応答で agent は動き続ける状態を確認
#    - flags.ai_generation_failed=True で AI_Draft__c に書き込まれている
# 3. 顧客側に「在庫情報を確認中。担当より折り返し」テンプレが返っている事を確認
# 4. CS 担当者に「AI ドラフトが使えないので手動対応に切替」を Slack DM
# 5. 30 分以上続く場合: agent-worker を一時停止
kubectl scale --replicas=0 deployment/cs-triage-agent -n cs-triage-prod
# 6. Anthropic 復旧確認後に再起動
kubectl scale --replicas=3 deployment/cs-triage-agent -n cs-triage-prod
# 7. fallback で生成された AI_Draft__c を SF SOQL で抽出し、
#    SE 工藤がオペに「再生成希望」リストを連絡
```

### 復旧後の予防策
- [ ] Anthropic API のレート制限・タイムアウト設定を最新確認
- [ ] 軽量モード（Haiku）への自動切替閾値を `llm_api_5xx_rate > 2%` で発火するよう Datadog Monitor 追加（コスト削減 + Sonnet 障害時のバックアップ）

---

## ランブック 4: DB（在庫 / 出荷 / ERP）ダウン (HIGH)

### 検知
- メトリクス: `cs_triage.db_lookup_failure_rate{db=~".+"} > 5%` for 10min
- 直接接続テスト失敗

### 対応手順
```bash
# 1. どの DB が落ちているか確認
psql -h <db-host> -U <user> -c "select 1"   # 在庫DB
curl https://erp-api.internal.example.com/health  # 価格DB
# 2. fallback テンプレ「在庫情報取得失敗。担当より折り返し」で agent 継続中を確認
# 3. SE / DBA に DB 復旧依頼
# 4. 復旧後、SF SOQL で fallback 生成された案件を抽出 → オペに再対応依頼
# 5. 部分障害なら lookup_inventory のみ無効化等の運用判断
```

---

## ランブック 5: コスト超過 (CRITICAL/MEDIUM)

### 検知
- 月予算 80% 到達: MEDIUM、Slack `#cs-system-alerts`
- 月予算 95% 到達: CRITICAL、PagerDuty
- 1 件単発で `$0.10` 超過: HIGH、`#cs-system-alerts`

### 対応手順
```bash
# 80% / 95% 到達時
# 1. 月内残り処理件数 × 平均コストで月末予測を計算（scripts/monitor.py で自動化）
# 2. 95% で agent-worker を全件 lite_mode に強制切替
#    kubectl set env deployment/cs-triage-agent -n cs-triage-prod LITE_MODE_FORCED=true
# 3. 100% 到達で agent-worker を停止
#    kubectl scale --replicas=0 deployment/cs-triage-agent -n cs-triage-prod
# 4. CS センター長 + SE で月予算上方修正の判断 → 翌月から復活
```

---

## ランブック 6: クレーム見逃し疑い (HIGH)

### 検知
- SV 抜き取りレビューで「complaint_smell=False だが実はクレーム」と判明
- オペからのフィードバック（Slack `#cs-feedback`）

### 対応手順
```bash
# 1. SV が該当ケース ID を Slack #cs-system-alerts に投下
# 2. 開発担当が該当ケースの input.txt を取得（PII マスク後）
# 3. eval/dataset/case_<NN>_complaint_<topic>/ に追加
#    - input.txt: 元メール（マスク済み）
#    - metadata.yaml: complaint_smell=true 期待
# 4. eval を再実行して現状の v3 が見逃すか確認
#    python eval/run_eval.py --real --only case_<NN>
# 5. 見逃すなら:
#    a. complaint_keywords に新規キーワード追加（YAML 編集 + PR）
#    b. 必要なら classify プロンプトを微調整
# 6. 再評価で recall が改善するか確認 → デプロイ
# 7. 月次レビュー会で SV にフィードバック報告
```

---

## ランブック 7: 過検知（false positive）の急増 (MEDIUM)

### 検知
- メトリクス: `complaint_detected_count` 平常時の 1.5 倍 for 1 day
- SV から「Slack 通知が多すぎて捌けない」報告

### 対応手順
```bash
# 1. 過去 24 時間の complaint_smell=True ケースを SF SOQL で抽出
# 2. SV が抜き取り 20 件を「真クレーム / 過検知」に分類
# 3. precision を計算（真 / (真 + 過)）
# 4. precision < 60% なら:
#    a. complaint_keywords を見直し、過剰反応するキーワードを除外
#    b. classify プロンプトに「文脈確認」ルール追加
# 5. eval で recall が悪化していないことを確認
# 6. SV に「過検知率改善見込み」を Slack DM
```

---

## ランブック 8: 応答時間 P95 超過 (MEDIUM)

### 検知
- メトリクス: `cs_triage.latency_seconds.p95 > 30s` for 30min

### 対応手順
```bash
# 1. Datadog で latency 内訳を確認（preprocess / extract / classify / retrieve / draft / reflect / assemble のどこが遅いか）
# 2. ボトルネックが LLM 呼び出しなら:
#    a. recursion_limit / max_tokens の見直し
#    b. lite_mode 起動条件を緩めてカバレッジを広げる
# 3. ボトルネックが DB なら:
#    a. クエリ実行計画を確認
#    b. DB の負荷状況を SE / DBA に確認
# 4. webhook-receiver の queue depth が高い場合は agent-worker をスケールアウト
#    kubectl scale --replicas=5 deployment/cs-triage-agent -n cs-triage-prod
```

---

## 月次運用タスク

### 月初
- [ ] 前月のコストレポート確認（`scripts/monitor.py` 出力）
- [ ] PII 監査ランダム 100 件を実行（`scripts/audit_pii.py` SE 工藤主管）
- [ ] 月次レビュー会の準備（うまくいかなかったケース 10 件抽出）

### 月次レビュー会（CS センター長 + SV 2 名 + SE 1 名、60 分）
- [ ] eval メトリクストレンドの確認（Datadog）
- [ ] うまくいかなかったケース 10 件のレビュー
- [ ] eval/dataset/ への新規ケース昇格判断
- [ ] config の YAML 調整候補を議論

### 月末
- [ ] Vault シークレットの期限チェック（90 日 / 180 日）
- [ ] SLO 達成状況のサマリ作成 → CS 部内共有
- [ ] 月次レビュー結果を CHANGELOG.md にメモ

---

## 緊急停止手順

```bash
# 1. agent-worker 全停止
kubectl scale --replicas=0 deployment/cs-triage-agent -n cs-triage-prod

# 2. SF Apex Trigger を一時無効化（SF Setup から）
# 3. オペに「AI ドラフト機能停止中。手動対応に切替」を Slack 全体配信
# 4. ステータスページ更新（社内 Confluence）
# 5. インシデントチャンネル `#cs-incident-<date>` を Slack に作成して共有
```

---

## 連絡先

| 役割 | 担当 | 連絡 |
|---|---|---|
| CS センター長 | 富田 | Slack `@tomita` / 内線 1234 |
| SV | 阿部 | Slack `@abe` / 内線 5678 |
| 法務 | 大野 | メール `ohno@example.com` |
| SE オンコール | 工藤 | PagerDuty `cs-triage-oncall` |
| Anthropic サポート | - | https://status.anthropic.com / support@anthropic.com |
