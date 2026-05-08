# 詳細設計書: Expense Review Agent v1

> 作成日: 2026-05-09 / 作成者: agent-decompose

---

## 1. システムコンテキスト図

```mermaid
graph TB
    Applicant[申請者]
    SubsysApp[楽楽精算]
    Hook[Webhook]
    subgraph CORE[コア]
        Agent[Expense Review Agent<br/>8 ノード]
    end
    subgraph EXT[外部 LLM]
        Claude[Claude Sonnet 4.6 / Haiku 4.5]
    end
    subgraph INTL[社内 DB]
        Emp[(社員マスタ)]
        Past[(過去申請 DB)]
    end
    subgraph Out[配信先]
        Free[freee 連携]
        Slack1[Slack #expense-feedback]
        Slack2[Slack #expense-audit]
        DD[Datadog]
    end
    Applicant --> SubsysApp --> Hook --> Agent
    Agent --> Emp & Past
    Agent --> Claude
    Agent --> SubsysApp
    Agent --> Slack1 & Slack2
    Agent --> Free
    Agent -.metrics.-> DD
```

---

## 2. データモデル

### 2-1. State (TypedDict)

```mermaid
classDiagram
    class ReviewState {
        +dict raw_payload
        +str application_id
        +dict masked_payload
        +dict pii_map
        +bool has_attachment
        +dict parsed_fields
        +float extract_confidence
        +list rule_violations
        +list rule_trace
        +list duplicate_candidates
        +list gray_judgments
        +float risk_score
        +str decision
        +list reasons
        +list suggested_fixes
        +str feedback_message
        +str internal_memo
        +bool reflect_pass
        +list reflect_issues
        +int reflect_iter
        +dict final_output
        +dict cfg
        +bool dry_run
        +bool lite_mode
    }
```

### 2-2. 出力 JSON スキーマ

```json
{
  "application_id": "RKR-2026-05-001234",
  "schema_version": "v1.0",
  "decision": "needs_fix",
  "risk_score": 0.35,
  "needs_human_review": false,
  "reasons": [
    "接待相手先の記入がありません",
    "1 人あたり 6,200 円で接待単価上限 5,000 円を超過しています"
  ],
  "suggested_fixes": [
    "相手先（会社名・役職・氏名）を摘要欄に追記してください",
    "上限超過理由（重要顧客との会食等）を申請理由に追記してください"
  ],
  "feedback_message": "お世話になっております。ご申請いただいた接待交際費 ...",
  "internal_memo": "接待単価超過 + 相手先空欄、申請者へ修正依頼。",
  "rule_trace": [
    {"rule_id": "receipt_required", "result": "pass"},
    {"rule_id": "counterparty_required", "result": "fail", "expected": true, "actual": false},
    {"rule_id": "hospitality_per_person_limit", "result": "fail", "limit": 5000, "actual": 6200}
  ],
  "meta": {
    "category": "hospitality",
    "amount_jpy": 18600,
    "participants_count": 3,
    "store_name": "銀座 寿司 はる",
    "receipt_date": "2026-05-05",
    "applicant_id": "EMP-2024-001",
    "duplicate_count": 0,
    "used_models": ["claude-sonnet-4-6"],
    "cost_usd": 0.0234,
    "lang": "ja"
  },
  "flags": {
    "duplicate_suspected": false,
    "ai_classify_failed": false,
    "ai_draft_failed": false,
    "pii_warning": false,
    "reflect_warning": false
  }
}
```

---

## 3. シーケンス図（主要ユースケース 5 本）

### 3-1. 正常系: auto_approve（出張交通費、規程内）

```mermaid
sequenceDiagram
    actor App as Applicant
    participant SubAp as 楽楽精算
    participant A as Agent
    participant Pol as Policy YAML
    participant Past as PastClaimDB
    participant LLM as Claude
    App->>SubAp: 経費申請（新幹線往復 18,000 円）
    SubAp->>A: webhook (raw_payload)
    A->>A: preprocess (mask) → extract (parse fields)
    A->>Pol: lookup_policy(travel)
    Pol-->>A: limit OK
    A->>A: validate_rules → 0 violations
    A->>Past: lookup_past_claims (no match)
    Past-->>A: no duplicates
    A->>LLM: classify_gray (lite mode)
    LLM-->>A: low risk
    A->>LLM: draft_decision (Haiku)
    LLM-->>A: decision=auto_approve
    A->>LLM: reflect (Haiku)
    LLM-->>A: pass
    A->>SubAp: write_back decision=auto_approve
```

### 3-2. needs_fix: 接待相手先空欄 + 単価超

```mermaid
sequenceDiagram
    participant A as Agent
    participant Pol as Policy
    participant LLM as Claude
    A->>A: extract → category=hospitality, per_person=6,200, counterparty=空
    A->>Pol: lookup_policy(hospitality)
    Pol-->>A: limit=5,000, counterparty_required=true
    A->>A: validate_rules → 2 violations
    A->>LLM: classify_gray (Sonnet) — 単価超 + 空欄の理由を判断
    LLM-->>A: gray=可能性あり（重要顧客なら例外）
    A->>LLM: draft_decision → decision=needs_fix, suggested_fixes=[相手先記入, 理由追記]
    LLM-->>A: feedback=「お世話になっております ...」
    A->>LLM: reflect → 具体性 OK
    LLM-->>A: pass
    A->>A: assemble → 楽楽精算 + Slack #expense-feedback
```

### 3-3. needs_review: 重複疑い

```mermaid
sequenceDiagram
    participant A as Agent
    participant Past as PastClaimDB
    participant LLM as Claude
    A->>A: validate_rules (clean) → lookup_history
    A->>Past: lookup_past_claims (店名・金額・90日)
    Past-->>A: 1 件 hit (前月同店名同金額)
    A->>A: duplicate_suspected=true
    A->>LLM: classify_gray (重複の妥当性確認)
    LLM-->>A: 接待頻繁な顧客もありうる、要人手判断
    A->>LLM: draft_decision → decision=needs_review
    LLM-->>A: internal_memo=「{店名} {金額} 前月にも同申請、要重複確認」
    A->>A: assemble → 楽楽精算 + Slack #expense-audit
```

### 3-4. reject: 精算期限切れ

```mermaid
sequenceDiagram
    participant A as Agent
    participant Pol as Policy
    A->>A: extract → receipt_date=2026-02-28（73 日前）
    A->>A: validate_rules → deadline_exceeded (severity=reject)
    A->>A: lookup_history (skip — どうせ reject)
    A->>A: classify_gray (skip)
    A->>A: draft_decision (rule-based) → decision=reject, reasons=[期限切れ]
    A->>A: reflect (rule-based)
    A->>A: assemble → Slack #expense-feedback「精算期限を過ぎています...」
```

### 3-5. 失敗系: 過去申請 DB ダウン

```mermaid
sequenceDiagram
    participant A as Agent
    participant Past as PastClaimDB
    participant LLM as Claude
    A->>A: validate_rules (clean)
    A->>Past: lookup_past_claims
    Past--xA: db_unavailable
    A->>A: history_lookup_errors=[db_unavailable]
    A->>LLM: classify_gray
    LLM-->>A: ...
    A->>LLM: draft_decision → decision=needs_review
    LLM-->>A: internal_memo=「過去申請 DB 不通のため重複検出不能、人手確認」
```

---

## 4. アクティビティ図: 軽量モード起動条件

```mermaid
flowchart TD
    A[validate_rules + lookup_history 完了] --> B{rule_violations 0?}
    B -- No --> S[標準モード（Sonnet）]
    B -- Yes --> C{duplicate_count == 0?}
    C -- No --> S
    C -- Yes --> D{amount_jpy < 30,000?}
    D -- No --> S
    D -- Yes --> L[軽量モード（Haiku）]
    L --> E[classify_gray / draft / reflect 全て Haiku]
    S --> E2[全て Sonnet]
```

---

## 5. エラー処理・例外フロー

```mermaid
flowchart TD
    A[ノード実行] --> B{例外?}
    B -- No --> C[正常返却]
    B -- Yes --> D{致命?}
    D -- Yes --> E[CRITICAL log + needs_review 強制 + Slack #expense-audit]
    D -- No --> F{種別?}
    F -- LLM API --> G[3 retry / exponential]
    F -- DB lookup --> H[needs_review 格上げ + 内部メモに不通理由]
    F -- regex/parse --> I[WARNING log + 続行]
    G --> J{失敗?}
    J -- Yes --> H2[テンプレ定型 + ai_*_failed=True]
    J -- No --> C
```

---

## 6. ロギング・監視

### Datadog メトリクス

| メトリクス | 単位 | 用途 |
|---|---|---|
| `expense_review.processed_count` | counter | スループット（decision タグ）|
| `expense_review.latency_seconds` | histogram | P95 レイテンシ目標 15s |
| `expense_review.cost_usd_per_request` | gauge | 1 件コスト目標 $0.03 |
| `expense_review.decision_distribution` | gauge | auto_approve/fix/review/reject の比率 |
| `expense_review.duplicate_detected_count` | counter | 重複疑い検出件数 |
| `expense_review.rule_violation_count` | counter | ルール違反件数（rule_id タグ） |
| `expense_review.lite_mode_rate` | gauge | 軽量モード起動率 |
| `expense_review.reflect_iter_count` | histogram | 平均ループ回数 |
| `expense_review.pii_warning_count` | counter | PII マスキング警告 |
| `expense_review.fallback_template_count` | counter | テンプレフォールバック発火 |

---

## 7. 性能・コスト試算

### 7-1. レイテンシ予算（P95、標準モード）

```mermaid
gantt
    title 1 リクエスト処理時間（P95、標準モード）
    dateFormat X
    axisFormat %S秒
    section ノード
    preprocess (~50ms) :a1, 0, 50
    extract (~150ms) :a2, 50, 200
    validate_rules (~100ms) :a3, 200, 300
    lookup_history (DB ~300ms) :a4, 300, 600
    classify_gray (Sonnet ~3s) :a5, 600, 3600
    draft_decision (Sonnet ~5s) :a6, 3600, 8600
    reflect (Sonnet ~3s) :a7, 8600, 11600
    assemble (~100ms) :a8, 11600, 11700
    write_back (~200ms) :a9, 11700, 11900
```

→ 標準モード合計 **約 12 秒**（P95）。spec §10-2 の **15 秒以内** に収まる。
軽量モード: **約 5〜7 秒**。

---

## 8. セキュリティ要件

### 8-1. データの流れ（PII / 機密情報）

```mermaid
flowchart LR
    A[raw_payload<br/>申請者氏名・社員 ID 含む] --> B[mask_pii]
    B --> C[masked_payload + pii_map]
    C --> D[Claude API<br/>masked のみ送信]
    C --> E[各ノード]
    E --> F[assemble.unmask]
    F --> G[final_output<br/>PII 復元]
    G --> H[楽楽精算書き戻し / Slack]
    style A fill:#ffcccc
    style C fill:#ccffcc
    style D fill:#ccffcc
    style G fill:#ffcccc
```

- LLM API には **緑のみ送信**
- 領収書画像本体は LLM に送らない（OCR 抜粋のみ）

### 8-2. アクセス制御

| 項目 | アクセス可 | 認証 |
|---|---|---|
| 社員マスタ | read-only | Okta SSO サービスアカウント |
| 過去申請 DB | read-only | Okta SSO |
| 楽楽精算 API | 申請データ取得 / 判定書き戻し | API キー（Vault） |
| freee API | 連携書き戻し | OAuth |

---

## 9. テスト戦略

### 9-1. 単体テスト対象

| モジュール | テストケース例 | 件数目安 |
|---|---|---|
| `pii_mask.py` | 氏名 / 社員 ID の漏れ・誤マスク | 20 |
| `validate_rules.py` | 各 rule_id の pass / fail | 30+ |
| `lookup_*.py` | 正常 / マスタ未登録 / DB 不通 | 各 3 |
| `extract.py` | OCR 抜粋からの構造化、欠損ケース | 10 |
| `assemble.py` (unmask) | placeholder 全件復元 | 5 |

### 9-2. 評価データセット

- v1 prototype 完了時: 12 件
- v1 eval 完了時: 12 件 + 必要に応じて 5 件追加
- v2 リリース時: 30 件（本番ログから昇格）

---

## 10. 改訂履歴

| バージョン | 日付 | 変更 |
|---|---|---|
| v1.0 | 2026-05-09 | 初版（agent-decompose）|

---

## 付録: cs_triage_agent との比較

| 観点 | cs_triage_agent v1 | expense_review_agent v1 |
|---|---|---|
| ノード数 | 7 | 8（validate_rules を独立）|
| 主要パターン | Tool Use + Reflection | コード優先ハードチェック + Tool Use + Reflection |
| LLM の役割 | 起草 / 分類 / 自己レビュー | グレー判定 / フィードバック起草 / 自己レビュー |
| 致命リスク | クレーム見逃し | **不正 auto_approve** |
| 軽量モード起動 | カテゴリ判定（shipment/cad/billing）| ルール検査結果 + 重複なし + 金額閾値 |
| 月予算消化率 | 36%（$3,000 中 $1,092）| **3%**（$1,000 中 $29）|
