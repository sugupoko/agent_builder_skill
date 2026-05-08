# 障害対応ランブック テンプレート

> AI エージェントの本番運用で発生しうる障害シナリオ別の対応手順。
> このテンプレを `workspace/<project>/v1/reports/deploy_report.md` の §ランブック にコピーして埋める。
> プロジェクト固有のキー (PII / DB 名 / API キー名等) を埋めること。

---

## アラート Severity 分類

| Severity | 例 | 通知先 | 対応時間 |
|---|---|---|---|
| **CRITICAL (P1)** | PII 漏えい / DB 完全停止 / 月予算 95% 到達 | PagerDuty + 電話 | 15 分以内 |
| **HIGH (P2)** | LLM API エラー率 > 5% / 主要機能不全 | Slack + メール | 1 時間以内 |
| **MEDIUM (P3)** | 応答時間 P95 超過 / 予算 80% 到達 | Slack | 営業日中 |
| **LOW (P4)** | 統計指標の異常 (品質劣化サイン) | Slack (静か) | 週次レビュー |

---

## ランブック 1: PII / 機密情報の漏えい (CRITICAL)

### 検知
- メトリクス: `<project>.pii_residual_critical{*} > 0`
- もしくは: ログに `CRITICAL: PII マスクトークン残存`

### 対応手順
```
1. PagerDuty incident をオンコールが受信
2. Slack #cs-alerts で対象 ID を確認
3. 該当ケースを開き、出力本文の `[ERROR: PII...]` マーカーを確認
4. 出力を破棄 / 再送信を阻止 (CRM の送信ボタン無効化)
5. ログから検出失敗箇所を特定:
   kubectl logs -n <namespace> -l app=<agent> --tail=500 | grep <case_id>
6. PII regex の不足を <pii_module>.py に追加
7. <test_pii>.py に該当ケースのテストを追加
8. デプロイ → 同一ケースで再実行確認
9. インシデント振り返り: 24 時間以内に postmortem 作成
```

### 復旧後の予防策
- [ ] 同パターンの別ケースが過去にも漏れていないか過去 24 時間のログ全件チェック
- [ ] PII regex の網羅性を strengthen (姓辞書ベース等)
- [ ] 月次監査ジョブで類似パターンを自動検出

---

## ランブック 2: LLM API ダウン (HIGH)

### 検知
- `<project>.fallback_llm_down{*} / processed_count > 0.05`
- Anthropic status page (https://status.anthropic.com) で障害確認

### 対応手順
```
1. Slack #<channel> でアラート確認 + status page 確認
2. fallback テンプレで agent は動き続ける状態を確認
3. 顧客側に「担当より折り返しご連絡」が送信されている事を確認
4. 担当業務者に「AI ドラフトが使えないので手動対応に切替」を Slack DM
5. 30 分以上続く場合: agent worker を一時停止
   kubectl scale --replicas=0 deployment/<agent>
   → webhook-receiver は queue に積み続ける
6. 復旧確認:
   curl -X POST -d '{"prompt":"ok"}' https://api.anthropic.com/...
7. 復旧後: kubectl scale --replicas=N
8. queue の積み残しを 1 時間以内に処理完了するか監視
```

### 予防策
- 別 LLM プロバイダの予備契約 (OpenAI / Google 等)
- カテゴリ別 fallback ルール (cs では「全て手動対応に転送」テンプレ等)

---

## ランブック 3: DB ダウン / 接続不能 (HIGH)

### 検知
- `<project>.fallback_db_down{*}` が連続 5 回以上発火

### 対応手順
```
1. DBA を呼ぶ (在庫 DB チーム / ERP API チーム等)
2. agent は fallback テンプレで動き続ける状態を確認
3. 担当業務者に「DB 停止中、テンプレ返答後に手動でフォロー」を周知
4. DB 接続テスト:
   redis-cli -h <db_host> ping
   psql -h <db_host> -c "SELECT 1"
5. 復旧確認後、agent から手動で適当な ID を再投入してテスト
```

### 予防策
- DB の health check を CronJob で 5 分毎
- レプリカへの自動 failover 設定

---

## ランブック 4: Queue 詰まり (MEDIUM)

### 検知
- `<project>.queue_depth{*} > 100` が 5 分継続

### 対応手順
```
1. HPA が worker を scale-up しているか確認:
   kubectl get hpa -n <namespace>
2. worker のレイテンシが伸びていないか Datadog で確認
3. LLM API レイテンシが原因なら、軽量モード切替を検討
   → 一時的に config を Lite 版に切替 (品質低下を許容)
4. queue の中身を確認 (異常な入力で詰まっていないか):
   redis-cli LRANGE <queue_name> 0 5
5. 異常入力なら deadletter queue に隔離
6. 1 時間以内に解消しなければ HIGH に昇格
```

### 予防策
- HPA の閾値見直し
- max worker 数を引き上げ
- 異常入力の事前 validation

---

## ランブック 5: 月予算到達 (MEDIUM → CRITICAL)

### 検知
- 80% (MEDIUM): `<project>.monthly_cost_pct_of_budget > 0.80`
- 95% (CRITICAL): 同上 > 0.95

### 80% 到達時の対応
```
1. Slack で関係者 (センター長 / SE) に通知
2. 残日数と件数 trend を Datadog で確認
3. 想定以上に伸びている場合:
   a) 軽量モード切替を検討 (Lite 版に config 切替)
   b) 不要なケースの除外ルールを追加
   c) Anthropic に予算追加申請 (営業ルート)
4. 95% 到達前に判断
```

### 95% 到達時の対応 (CRITICAL)
```
1. PagerDuty incident
2. 即座に軽量モード切替 (品質低下を許容)
3. または agent を一時停止 (件数を減らす)
4. 月締めまでの予測コストを再計算
5. 来月の予算交渉 / 構成変更計画
```

---

## ランブック 6: 品質劣化 (drift) (LOW → MEDIUM)

### 検知
- `<project>.classify_confidence{*} p50 < 0.7` が 1 日継続
- もしくは月次 LLM Judge スコアが前月比 -0.3 以上低下

### 対応手順
```
1. 週次レビューで原因究明
2. 過去 1 ヶ月の本番ログから低スコアケースを抽出
3. 評価データセットに追加 (`eval/dataset/sample_NNN.txt`)
4. 既存版で再評価し、低下が事実か確認
5. 改善案を v2 として実装 (vN+1)
6. v1 vs v2 で同一データセットで再評価
7. 改善が確認できたら段階的にロールアウト
```

### 予防策
- 月次品質レビュー会議の固定アジェンダ化
- LLM Judge スコアの月次 trend グラフを Datadog に
- 「劣化したら自動的に v(N-1) にロールバック」の自動化

---

## 共通: postmortem テンプレ

CRITICAL / HIGH レベルのインシデント発生時:

```markdown
# Postmortem: <インシデント名>

## 概要
- 発生時刻:
- 検知時刻:
- 復旧時刻:
- 影響範囲: (件数 / 顧客数 / 失われた金額等)
- Severity:

## タイムライン
| 時刻 | できごと |
|---|---|
| | |

## 根本原因 (Root Cause)
(なぜ起きたか)

## 検知が遅れた要因
(もっと早く検知できなかったか)

## 復旧が遅れた要因
(もっと早く復旧できなかったか)

## 再発防止策
1. 短期 (1 週間以内):
2. 中期 (1 ヶ月以内):
3. 長期 (四半期以内):

## アクション項目
- [ ] (担当者 / 期限)
```

---

## 実例

完全な実装例: `workspace/cs_triage_agent/v1/reports/deploy_report.md` §7
