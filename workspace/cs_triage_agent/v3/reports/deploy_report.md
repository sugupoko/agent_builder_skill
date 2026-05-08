# deploy レポート — CS Triage Agent v3

**実行日**: 2026-05-08
**スキル**: `/agent-deploy`
**実行者**: agent-builder
**前提**: v3 で致命ミス 0 / urgency 100% / SKU 100% / クレーム再現率 100% を達成。Judge 低下は Judge プロンプト未整合が主因（v3.1 で対処予定）

---

## 1. 入力

| ファイル | 概要 |
|---|---|
| `v3/spec.md` | 17 章構成の仕様書（致命ミス 0 達成、§16-3-A 運用境界含む）|
| `v3/reports/eval_report.md` | v1 eval 結果 |
| `v3/reports/evolve_v3_report.md` | v3 evolve 結果（致命ミス解消） |
| `data/hearing_round_2.md` | 第 2 回ヒアリング 5 ペルソナ × 23 項目合議 |

---

## 2. 成果物

```
v3/
├── reports/
│   ├── deploy_design.md         運用設計書（17 章）
│   └── deploy_report.md         本レポート
├── ops/
│   ├── runbook.md               障害対応ランブック（8 シナリオ + 緊急停止）
│   └── rollout_plan.md          段階的ロールアウト（Phase 0-3 + 本番安定）
├── scripts/
│   ├── dispatch_salesforce.py   AI_Draft__c 書き込みスケルトン（SE 工藤引き継ぎ）
│   ├── dispatch_slack.py        クレーム通知 / システムアラート Slack
│   ├── monitor.py               日次 / 月次コスト・品質モニタ
│   ├── webhook_receiver.py      FastAPI webhook 受信スケルトン
│   └── .github/workflows/ci.yml dry-run CI + 致命ミス 0 件アサート
└── spec.md                      §16-4 / §16-4-A / §17 を update
```

合計: 4 つのスケルトン Python + CI YAML + 3 つのドキュメント + spec 更新。

---

## 3. 設計の主要ポイント

### 3-1. トリガー: SF webhook（イベント駆動）

- SF Case 新規作成 → Apex Trigger → Outbound Message → webhook-receiver
- HMAC 認証 + idempotency（case_id でレコード重複防止）
- 期待 QPS: 平均 1 件/秒、ピーク 3 件/秒

### 3-2. 配信先: SF カスタムオブジェクト `AI_Draft__c`

round-2 §D-3 で工藤合意済の 12 フィールド構成。オペは SF UI から AI ドラフトタブを開いて確認 → 編集 → 送信。**AI が顧客に直接送信することは絶対にない**（spec §11-2 / 法務 大野合意）。

### 3-3. クレーム時 Slack 通知

`#cs-complaint-alerts` に SV へドラフトプレビュー + SF リンクを通知。`#cs-system-alerts` にシステム障害通知。

### 3-4. 段階的ロールアウト（4 フェーズ、合計 1.5 ヶ月想定）

1. **Phase 0: 準備** — インフラ / SF / Slack / Datadog セットアップ + オペ教育（1 週間）
2. **Phase 1: シャドー** — agent 出力をファイル蓄積、SF 書き込みなし。SV が週次レビュー（1 ヶ月）
3. **Phase 2: パイロット** — オペ 5 名で SF 書き込み ON、日次フィードバック（2 週間）
4. **Phase 3: 全展開** — 5→10→20→30 名と段階拡大（1 週間）→ 本番安定運用 30 日

### 3-5. Cutover 基準（クリティカルエラー 0 件は絶対条件）

| 基準 | 閾値 |
|---|---|
| **クリティカルエラー** | **0 件**（PII 漏洩 / 内部 URL 顧客送出 / 致命的誤回答）|
| 人間との agreement 率 | ≥ 85%（カテゴリ判定）|
| LLM Judge overall | ≥ 4.0（v3.1 修正後）|
| オペ採用率 | ≥ 60%（Phase 2→3）/ ≥ 50%（Phase 3→本番）|
| クレーム見逃し | 0 件 |

「85% agreement だがクリティカルエラー 1 件」**でも cutover NG**。1 件でも発生したら期間延長。

### 3-6. dry-run vs real LLM の運用境界（round-2 §B-5 合意済）

| フェーズ | モード |
|---|---|
| CI（毎 push）| dry-run（コスト 0）|
| PR マージ前 | dry-run + 失敗 5 件のみ real（PR ラベル `eval-real` 時のみ、$0.15）|
| リリース判定 | real 全件 + LLM Judge 全件（$0.6）|
| シャドー運用 | real のみ（Slack 通知あり、SF 書き込みなし）|
| 本番 | real + Slack + SF 書き込み |

---

## 4. CI ガード設定（`.github/workflows/ci.yml`）

毎 push / PR で以下を自動チェック:

1. **コンパイル**: 14 Python ファイル + 4 deploy スケルトン全部 `py_compile`
2. **グラフ生成**: `python agent.py --print-graph` が Mermaid を出力
3. **dry-run eval（22 件）**: 構造完走、コスト 0
4. **品質ゲート アサート**:
   - 致命ミス = 0 件
   - SKU 抽出再現率 ≥ 99%
   - クレーム検出再現率 ≥ 95%
   - 必須テンプレ語含有率 ≥ 99%
   - 禁止語違反 = 0 件

→ 1 つでも違反すると CI 失敗。プロンプト改修・YAML 編集の回帰検知が機能。

PR ラベル `eval-real` が付いた場合のみ、5 件の代表ケースで実 LLM 評価を追加実行（`ANTHROPIC_API_KEY` を GitHub Secrets から）。

---

## 5. SE 工藤への引き継ぎ事項

### 5-1. インフラ実装
- [ ] K8s namespace `cs-triage-prod` 作成 + RBAC
- [ ] webhook-receiver（FastAPI x 2 + uvicorn）の Helm chart
- [ ] agent-worker（Python 3.11 + Sonnet/Haiku）の Dockerfile + Helm chart
- [ ] Redis（queue + cache）の StatefulSet
- [ ] CronJob: `monitor.py --mode monthly`（月初 0:00）+ `audit_pii.py`（月初 1:00）

### 5-2. SF / 外部連携実装
- [ ] SF カスタムオブジェクト `AI_Draft__c`（12 フィールド、Apex Trigger）
- [ ] `dispatch_salesforce.py` の TODO（simple-salesforce での OAuth 接続）
- [ ] `dispatch_slack.py` の TODO（slack_sdk.WebClient で chat_postMessage）
- [ ] `webhook_receiver.py` の TODO（FastAPI + Redis enqueue）
- [ ] DB 実 SDK 化（src/db_client.py のモック → psycopg2 + ERP API requests）

### 5-3. シークレット投入（Vault）
- `/secret/cs-triage/anthropic-key`（90 日ローテーション）
- `/secret/cs-triage/slack-bot-token`（90 日）
- `/secret/cs-triage/sf-oauth`（180 日）
- `/secret/cs-triage/sf-webhook-secret`（90 日、HMAC 用）
- `/secret/cs-triage/db-conn`（Okta SSO 経由）
- `/secret/cs-triage/erp-token`（90 日）

### 5-4. Datadog 設定
- ダッシュボード: メトリクス 10 種（spec.md §10-2 / deploy_design §5-1）
- Monitor: severity 別アラート 4 種（runbook.md §アラート Severity 分類）
- log forwarding: K8s Pod stdout → Datadog Logs

---

## 6. CS センター長 富田 + SV 阿部への引き継ぎ事項

### 6-1. SLO / 予算 / 運用方針
- [ ] **SLO 値の最終決定**（99.5% vs 99.9%）
- [ ] **月予算上限の最終決定**（$3,000 維持 or 上方調整）
- [ ] **オペ採用率の cutover 閾値**（50% / 60% / 70%）
- [ ] **クレーム過検知の許容ライン**（月 20 件 / 50 件）

### 6-2. オペ教育
- [ ] 30 分セッション × 3 回の準備（CS センター長 主管）
- [ ] AI ドラフト操作マニュアル作成（SE 工藤協力）
- [ ] 「クレーム取消」ボタンの UI 仕様策定

### 6-3. シャドーモードの週次レビュー
- [ ] 毎週金曜 30 分の SV レビュー会のスケジューリング
- [ ] アンケート設計（Likert 5 段階で何項目か）

---

## 7. 法務 大野への引き継ぎ事項

- [ ] **PII マスキング不一致時の責任分担合意**（インシデント発生時にオペ / SE / 法務 のどこで責任）
- [ ] **月次 PII 監査の精査基準**（100 件サンプリングで漏洩 1 件発見時の対応フロー）
- [ ] **CAD URL の短期署名 URL 化検討**（v4 候補、IT セキュリティと協議）

---

## 8. 開発（agent-builder）への持ち越し（v3.1 候補）

`spec.md §16-4-A` に追記済の 5 項目:

1. draft.py max_tokens 1200 → 2400（case_02 の JSON 切り詰めフォールバック解消）
2. judge.py max_tokens 400 → 800（case_16 の Judge JSON 切り詰め解消）
3. judge.py プロンプトに v3 業務ルール（YAML 抜粋）を渡す
4. `apology.allowed` に「恐れ入ります」追加
5. tech ↔ alternative の境界ルール（決定論ロジック追加）

→ Phase 0 の **オペ教育セッションが始まる前**に v3.1 として完了させる（推定半日 + Judge 再採点 $0.2）。

---

## 9. spec.md §16 累計（deploy 後）

| セクション | 状態 | 件数 |
|---|---|---|
| 16-1 業務上 | 第 2 回ヒアリングで全件解消 | 4 / 4 |
| 16-2 実装上 | 第 2 回ヒアリングで全件解消 | 7 / 7 |
| 16-3 評価品質 | 第 2 回ヒアリングで全件解消 | 7 / 7 |
| 16-3-A 運用境界 | 第 2 回ヒアリングで解消 | 1 / 1 |
| 16-3-B v3 evolve 由来 | 第 2 回ヒアリングで全件解消 | 4 / 4 |
| **16-4 運用上**（**新規**）| **第 3 回追加ヒアリング待ち** | **8 件未解消** |
| **16-4-A v3.1 課題**（**新規**）| 開発持ち越し | **5 件未解消** |

→ 第 3 回追加ヒアリングは **shadow モード開始前** に必須。タイミングは Phase 0 の最終週（2026-05-19 想定）。

---

## 10. 想定タイムライン

| 期間 | フェーズ | 主担当 |
|---|---|---|
| 2026-05-08（本日）| v3 deploy 設計完了 | agent-builder |
| 2026-05-09〜10 | v3.1 修正（max_tokens / Judge プロンプト）+ 再評価 | agent-builder |
| 2026-05-12〜18 | Phase 0 準備（インフラ / SF / Slack / DD / オペ教育）| SE 工藤 + CS センター長 |
| 2026-05-19 | 第 3 回追加ヒアリング（§16-4 解消）| 全関係者 |
| 2026-05-21〜06-20 | Phase 1 シャドーモード（30 日）| 全関係者 |
| 2026-06-22〜07-05 | Phase 2 パイロット（14 日）| パイロット 5 名 |
| 2026-07-07〜07-13 | Phase 3 全展開（7 日）| 全オペ 30 名 |
| 2026-07-14〜08-13 | 本番安定運用（30 日）| 全関係者 |
| 2026-08-14 | 本番昇格判定 | CS センター長 |

→ 本日（2026-05-08）から本番安定確認完了まで **約 3 ヶ月**。

---

## 11. 動作確認

### dry-run 完走（API 消費なし）
```bash
cd workspace/cs_triage_agent/v3/scripts
python eval/run_eval.py
# → 19/22 pass / critical_miss 0 / cost $0
```

### CI シミュレーション（手動）
```bash
# 1. コンパイル全件
python -m py_compile agent.py src/*.py dispatch_*.py monitor.py webhook_receiver.py

# 2. グラフ生成
python agent.py --config config/cs_triage.yaml --print-graph

# 3. dry-run + 品質ゲートアサート
python eval/run_eval.py
python -c "
import glob, json
d = json.load(open(sorted(glob.glob('eval/results/run_*_dry'))[-1] + '/result.json'))
s = d['summary']
assert s['critical_misses'] == 0
assert s['sku_recall_avg'] >= 0.99
assert s['complaint_recall_overall'] >= 0.95
print('CI quality gates passed:', s)
"
```

### 配信スケルトン dry-run
```bash
python dispatch_salesforce.py --final-json output/final_sample_01.json
# → [dry-run] would write to Salesforce: {...}

python dispatch_slack.py alert --severity P3 --message "test alert"
# → [dry-run slack] would post to #cs-system-alerts: {...}
```

---

## 12. 次のアクション

### A. v3.1 修正（推奨、半日）
`spec.md §16-4-A` の 5 項目を解消し、Judge 再採点で品質確認。

### B. 第 3 回追加ヒアリング
`spec.md §16-4` の 8 項目を解消（SLO / 月予算 / 採用率閾値 等）。

### C. Phase 0 準備開始
インフラ実装 + オペ教育の段取り（SE 工藤 + CS センター長）。

→ **推奨: A → B → C の順**（v3.1 で品質を整え、第 3 回ヒアリングで運用合意、Phase 0 着手）。

→ あるいは並行: A は開発リード単独、B は CS 部主管、C は SE 工藤主管 のため **三者並行で進めれば 1 週間で Phase 1 シャドーモード開始可能**。

---

## 13. 制限事項

本リポジトリは **合成データ・ペルソナベース** で構築されている。実プロジェクトでは:

- 実在する CS センター長 / SV / 法務 / SE / オペによる **deploy_design.md / runbook.md / rollout_plan.md のレビュー必須**
- スケルトンコード（dispatch_*.py / webhook_receiver.py）は **実 SDK 実装が SE 担当**
- SF カスタムオブジェクト / Slack トークン / Vault シークレット / Datadog 設定は **本番環境を持つチームで実装**

deploy 直前（Phase 0 最終週）に必ず実関係者によるレビュー・補正が必要。
