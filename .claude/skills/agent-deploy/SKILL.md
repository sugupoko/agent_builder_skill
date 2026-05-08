---
name: agent-deploy
description: 評価が安定した動くエージェントを実運用に乗せるためのスキル。cron / Webhook トリガー、配信先（Slack / SMTP / Confluence）、コスト上限、人間レビュー工程、監視・アラート、リトライ・フォールバック、シークレット管理を設計する。「本番運用したい」「業務に組み込みたい」と言われたら呼ぶ。
---

# agent-deploy — 運用設計

## いつ使うか

- `/agent-eval` で品質基準をクリアした
- 「本番運用したい」「定期的に走らせたい」「業務に組み込みたい」とき

## 入力

- `workspace/<project>/vN/scripts/`（評価済みのエージェント）
- `workspace/<project>/vN/reports/eval_report.md`

## 出力

| ファイル | 内容 |
|---|---|
| `reports/deploy_design.md` | 運用設計書 |
| `scripts/cron.txt` または `scripts/github_actions.yml` | スケジュール定義 |
| `scripts/dispatch_*.py` | 配信先別の送信スクリプト |
| `scripts/monitor.py` | コスト・失敗の監視 |
| `spec.md` | 運用条件を反映 |

## 進め方

### Step 1: トリガー設計

| 方式 | 適合場面 | 注意点 |
|---|---|---|
| **cron** | 定期実行（週次/日次） | 失敗時の通知設計が必須 |
| **GitHub Actions** | コードと一緒に管理したい | パブリックリポジトリは API キー注意 |
| **Webhook** | イベント駆動（Slack コマンド等） | レート制限、認証 |
| **手動 + ボタン** | 配信前レビューありの運用 | 業務担当の操作性 |

### Step 2: 配信先の実装

#### A) ファイル出力のみ（プロトタイプ用）
すでに動いている。`output/` に Markdown 出力。手動で社内ポータルにコピペ。

#### B) Slack
```python
import slack_sdk
client = slack_sdk.WebClient(token=os.environ["SLACK_BOT_TOKEN"])
client.chat_postMessage(channel="#weekly-news", text=mail_body, mrkdwn=True)
```

注意:
- ボットスコープ: `chat:write`
- 長文は thread に分割
- Markdown は Slack mrkdwn フォーマットに変換が必要

#### C) SMTP（メール）
```python
import smtplib
from email.mime.text import MIMEText
msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = subject
msg["From"] = from_addr
msg["To"] = to_addr
with smtplib.SMTP_SSL(server, port) as s:
    s.login(user, pwd)
    s.send_message(msg)
```

注意:
- 業務メールサーバの認証（SSO/OAuth/SMTP AUTH）
- SPF/DKIM 設定
- 配信失敗のバウンス処理

#### D) Confluence / Notion
公式 API 経由。ページ作成 or 既存ページ更新。
- 認証: API トークン
- ページ階層の設計
- 過去レポートのアーカイブ運用

### Step 3: 配信前レビュー工程（重要）

業務メルマガとして配信する場合、**人間レビューを挟む**のが安全:

```
生成 → output/ にドラフト保存 → Slack に「レビュー待ち」通知
   → 業務担当が承認/差し戻し → 承認なら配信、差し戻しなら再生成
```

LangGraph の `human_in_the_loop` パターンを使うか、シンプルにファイル出力後に手動承認ボタン経由で配信スクリプトを走らせる。

### Step 3.5: シャドーモード → 本番への Cutover Criteria (2026 年標準)

業務影響のある AI エージェントは **シャドーモード期間 (1ヶ月程度) を経てから本番投入**するのが業界標準パターン。

#### 標準的な cutover 基準

| 基準 | 標準閾値 | 計測方法 |
|---|---|---|
| 人間との agreement 率 | **≥ 85%** (フロア) | シャドー期間のサンプル抜き取り |
| クリティカルエラー | **0 件** (絶対条件) | PII 漏れ / 致命的誤回答 / 危険指示等 |
| 主要指標 | プロジェクト固有目標達成 | eval_report.md §5 で定義 |
| オペ採用率 | 60%+ | パイロット期間の集計 |

「85% agreement だがクリティカルエラー 1 件」**でも cutover NG**。
クリティカルエラー条件は別途定義し、**1 件でも発生したらシャドー継続**。

#### Phase 構成 (推奨)

```
Phase 0: 準備    — インフラ構築 (1 週間)
Phase 1: シャドー — 送信せずドラフトのみ生成 (1 ヶ月)
Phase 2: パイロット — 5〜10 名で限定運用 (2 週間)
Phase 3: 全展開   — 段階的拡大 (1 週間で全社員)
```

各 phase に明確な卒業基準とロールバック条件。

#### Post-cutover の人間レビュー継続

本番投入後も **5-10% サンプル + 高リスクケース全件** を人間レビュー継続するのが標準。
理由: drift 検知、新パターン発見、品質保証。

→ 詳細: `reference/ops_iteration.md` §「Post-cutover の継続レビュー」

#### 実例
- `workspace/cs_triage_agent/v1/reports/deploy_design.md` §3-5 で実装例
- 業界事例: [Brightlume AI shadow mode rollout](https://brightlume.ai/blog/shadow-mode-rollouts-ai-agents-pilot-production)

### Step 4: コスト管理

#### 単発実行の上限
```python
ESTIMATED_COST = compute_cost()
if ESTIMATED_COST["cost_usd"] > MAX_PER_RUN:
    logger.warning("コスト超過: $%.4f > $%.2f", ESTIMATED_COST["cost_usd"], MAX_PER_RUN)
    # 失敗扱いにする / アラート / 軽量モードにフォールバック
```

#### 月間上限
別スクリプトで `data/cost_log.csv` に1実行1行記録 → 月初リセット → 月末アラート。

#### 軽量モードへのフォールバック
コスト超過時に `--no-deep-dive --no-supplement` で再実行する仕組みを用意。

### Step 5: 監視・アラート

| 監視項目 | 閾値 | アラート先 |
|---|---|---|
| 実行失敗 | 1回でも | Slack #alerts |
| 連続失敗 | 2回続けて | エンジニア携帯 |
| コスト超過 | $X 超過 | Slack |
| 月間予算 | 80% 到達 | Slack |
| 出力品質低下 | 評価指標が閾値割れ | Slack（次回評価タイミング） |
| 取得件数異常 | 平常時の50%未満 | Slack（ソース不通の早期発見） |

### Step 6: リトライ・フォールバック

```python
RETRY_COUNT = 3
for attempt in range(RETRY_COUNT):
    try:
        run_agent()
        break
    except RateLimitError:
        time.sleep(2 ** attempt * 30)  # exponential backoff
    except Exception as e:
        if attempt == RETRY_COUNT - 1:
            notify_slack(f"3回連続失敗: {e}")
            raise
```

ノードレベルのフォールバック:
- `supplement` 失敗 → そのまま続行（必須ではない）
- `deep_dive` 失敗 → そのまま続行
- `summarize` 失敗 → リトライ → ダメなら手動メッセージで通知して終了

### Step 7: シークレット管理

- API キーは `.env` （個人運用）→ `.env.production` + 環境変数注入
- CI/CD: GitHub Secrets / AWS Secrets Manager / 1Password
- ログにシークレットを出さない（`logging.Filter` で URL のクエリパラメータをマスク）
- 定期ローテーション（90日に1回）

### Step 8: deploy_design.md の生成

```markdown
# 運用設計書 v1

## 1. トリガー
- 方式: cron
- スケジュール: 毎週月曜 8:00
- 設定ファイル: scripts/cron.txt

## 2. 配信先
- Slack #weekly-news
- 配信前レビュー: 業務担当 ○○ が Slack ボタンで承認

## 3. コスト管理
- 1回上限: $0.50
- 月間予算: $30
- 超過時: 軽量モードに自動切替 + Slack 通知

## 4. 監視
- 実行ログ: logs/agent_*.log
- 失敗通知: Slack #alerts
- 月次レポート: data/cost_summary_<月>.md

## 5. リトライ
- レート制限: 3回 exponential backoff
- ノード失敗: supplement/deep_dive はスキップ可

## 6. シークレット管理
- ANTHROPIC_API_KEY: GitHub Secrets
- SLACK_BOT_TOKEN: GitHub Secrets
- ローテーション: 90日

## 7. 災害復旧
- 配信失敗時: 翌日手動再実行
- DB 破損時: バックアップ復元（毎日 0:00 に data/articles.db を data/backups/ に保存）
```

## 継続改善サイクル（最重要）

運用は始まりであって終わりではない。**バージョン駆動の改善サイクル**を回す:

```
本番運用
  ↓
ログ + フィードバック収集
  ↓
月次レビュー: eval/dataset/ に新規ケース追加
  ↓
v(N+1) の実装: workspace/<project>/v1/ → v2/
  ↓
CHANGELOG.md に「変更の動機・内容・結果」を記録
  ↓
回帰評価: vN と vN+1 を同じデータセットで比較
  ↓
退化なければデプロイ、あれば戻す
```

### 必須の運用ファイル

- `CHANGELOG.md` (プロジェクトルート): バージョン間の変更記録
- `vN+1/eval/results/`: 回帰チェック用の同データセット評価
- `data/ops_dashboard.csv`: 月次のコスト・品質トレンド

### 詳細

`reference/ops_iteration.md` を必ず読む。ここに:
- バージョンを切る判断基準
- CHANGELOG.md のテンプレ
- 回帰評価スクリプトの実装例
- 本番ログから新ケース昇格の方法
- 月次トレンドの可視化

---

## 設計の重要原則

### 段階的に運用に移行する

最初から全自動化しない:

1. **手動実行 + ファイル出力**: ローカルで `python agent.py` → ファイルを手でコピペ
2. **手動実行 + 自動配信**: 確認ボタン押すと配信
3. **自動実行 + 配信前レビュー**: cron で生成、業務担当が承認
4. **完全自動**: 評価が安定してから

### コストの見える化を最優先

「気がついたら $100 課金された」が一番怖い。各実行のコストをログに残し、月次で確認する仕組みを必ず入れる。

### 業務担当者が運用できる粒度に

YAML の編集だけで済むこと、Slack ボタンだけで承認できること、ログを見れば何が起きたか分かること。**エンジニアが毎週介入しなくていい状態**まで持っていく。

## 次のステップ

運用が回り始めたら:

```
/agent-evolve
```

マルチエージェント化・パラメータ探索・モデル比較などの構成変更を試みる（オプション）。
