# ツール仕様: Expense Review Agent v1

> 各 lookup ツールはコード内で呼び出す（疑似 Tool Use）。LLM が直接呼ぶことはない。

---

## 1. `lookup_employee(employee_id_masked: str) -> dict`

**機能**: 社員マスタから所属・等級・承認権限を取得。

**入力**:
- `employee_id_masked` (str): マスク済 社員 ID（preprocess で `[EMP_001]` に置換済）。実 ID を pii_map から復元してから呼び出す（assemble 直前）

**出力**:
```python
{
    "ok": True,
    "employee_id": "EMP-2024-001",
    "department": "営業部",
    "grade": "M2",                    # 一般 / M1 / M2 / 部長 / 本部長
    "approval_limit_jpy": 30000,      # 承認権限金額
    "manager_employee_id": "EMP-...",  # 上長
}
```

**失敗時**: `{"ok": False, "reason": "employee_not_found"}`

**権限**: 社員マスタの read-only ビュー。

**v1 でのスコープ**: モック YAML（`eval/mock_db/employees.yaml`）で代替。

---

## 2. `lookup_policy(category: str) -> dict`

**機能**: 経費規程 YAML から指定科目のルールを取得。

**入力**:
- `category` (str): "travel" / "hospitality" / "meeting" / "consumable" / "communication" / "training" / "misc"

**出力**:
```python
{
    "category": "hospitality",
    "per_person_limit_jpy": 5000,
    "counterparty_required": True,
    "participants_required": True,
    "approval_required_above_jpy": 30000,
    "receipt_required_above_jpy": 0,
}
```

**失敗時**: `{"ok": False, "reason": "unknown_category"}` → fallback として `misc` のポリシーを使う

**v1 でのスコープ**: `config/expense_policy.yaml` から直読み。

---

## 3. `lookup_past_claims(applicant_id: str, store_name: str, amount_jpy: int, days: int = 90) -> dict`

**機能**: 過去 N 日以内の同申請者・同店名・同金額の申請を検索。重複検出用。

**入力**:
- `applicant_id` (str): 申請者 ID（マスク済）
- `store_name` (str): 店名（OCR から）
- `amount_jpy` (int): 金額
- `days` (int, default 90): 検索対象期間

**出力**:
```python
{
    "ok": True,
    "matches": [
        {
            "claim_id": "CLAIM-2026-04-1234",
            "submitted_at": "2026-04-15",
            "store_name": "銀座 寿司 はる",
            "amount_jpy": 28000,
            "category": "hospitality",
            "decision": "auto_approve",
        },
        ...
    ],
    "match_count": 1,
}
```

**失敗時**:
```python
{"ok": False, "reason": "db_unavailable"}
{"ok": False, "reason": "search_timeout"}
```

→ DB 不通時は **needs_review に格上げ**（重複検出不能のため自動承認禁止）

**権限**: 過去申請 DB の read-only。申請者 ID で絞る前提。

**v1 でのスコープ**: モック YAML（`eval/mock_db/past_claims.yaml`）に 30 件程度のダミーを配置。

---

## 4. `mask_pii(payload: dict) -> tuple[dict, dict]`

**機能**: 申請者氏名・社員 ID・銀行口座等を placeholder に置換。

**入力**:
- `payload` (dict): 楽楽精算 webhook の生 JSON

**出力**:
- `(masked_payload, pii_map)`
  - `masked_payload`: payload の各 PII フィールドを `[NAME_001]` `[EMP_001]` に置換
  - `pii_map`: 復元用辞書

**マスキング対象**（v1 スコープ）:
- `applicant_name`（氏名）→ `[NAME_001]`
- `employee_id`（社員 ID）→ `[EMP_001]`
- 銀行口座関連（あれば）→ `[BANK_xxx]`
- 領収書 URL は対象外（外部送信しない）

**LLM API 送信ルール**:
- LLM API には **必ず masked_payload のみ送信**。pii_map は state 内のみ
- 例外: assemble で unmask する直前まで pii_map は state 内、ログには pii_map のハッシュのみ

---

## 5. `extract_receipt_fields(ocr_text: str) -> dict`

**機能**: 楽楽精算 OCR の生テキストから店名・金額・日付・人数を構造化抽出（補助）。

**入力**:
- `ocr_text` (str): OCR 結果テキスト（〜500 字想定）

**出力**:
```python
{
    "store_name": "銀座 すし はる",
    "amount_jpy": 28000,
    "receipt_date": "2026-05-01",
    "tax_rate": 0.10,
    "participants_hint": ["山田", "営業部"],   # 抽出できれば
    "extraction_confidence": 0.85,
}
```

**実装**: 第一段は regex（金額・日付）、不確実なら Haiku 4.5 で補完。OCR 抜粋テキストのみ送信、画像は送らない。

**失敗時**: フィールド欠損は許容、validate_rules で「必須項目欠如」として needs_fix に倒す。

---

## 6. ディスパッチ関数（参考）

`validate_rules` ノード内のコード:

```python
def validate_rules_node(state: ReviewState, cfg: dict) -> dict:
    parsed = state["parsed_fields"]
    cat = parsed.get("category")
    pol = lookup_policy(cat)
    violations = []
    rule_trace = []

    # ハードチェック群
    if not parsed.get("receipt_url"):
        violations.append({"rule_id": "receipt_required", "severity": "fix",
                           "message": "領収書添付が必要です"})
    rule_trace.append(("receipt_required", "checked"))

    days_old = (today - parsed["receipt_date"]).days
    if days_old > cfg["deadline_days"]:
        violations.append({"rule_id": "deadline_exceeded", "severity": "reject",
                           "message": f"領収書日付が {days_old} 日前で精算期限超過"})
    rule_trace.append(("deadline_exceeded", days_old <= cfg["deadline_days"]))

    if cat == "hospitality":
        per_person = parsed["amount_jpy"] / max(1, parsed.get("participants_count", 1))
        if per_person > pol["per_person_limit_jpy"]:
            violations.append({"rule_id": "hospitality_per_person_exceeded",
                               "severity": "review",
                               "message": f"接待単価 {per_person:,.0f} 円が上限超過"})
        if pol["counterparty_required"] and not parsed.get("counterparty"):
            violations.append({"rule_id": "counterparty_required", "severity": "fix",
                               "message": "接待相手先の記入が必要です"})
        rule_trace.extend([("hospitality_per_person", ...), ("counterparty_required", ...)])

    # ...他のカテゴリも同様

    return {"rule_violations": violations, "rule_trace": rule_trace}
```

---

## 7. ツール呼び出し制限のまとめ

| ツール | 最大呼び出し / リクエスト | キャッシュキー |
|---|---|---|
| `lookup_employee` | 1（申請者本人）+ 1（上長確認時） | `(employee_id_masked,)` |
| `lookup_policy` | 1 | `(category,)` |
| `lookup_past_claims` | 1（重複検出 1 回のみ）| `(applicant, store, amount, days)` |
| `mask_pii` | 1 | preprocess 内のみ |
| `extract_receipt_fields` | 1 | `(ocr_text_hash,)` |

リクエストごとに `clear_tool_cache()` で全リセット。

---

## 8. 単体テスト指針

各ツールに最低 3 件:

1. **正常系**: 期待通りの dict
2. **マスタ未登録**: `{"ok": False, "reason": "...not_found"}`
3. **DB 不通**: `{"ok": False, "reason": "db_unavailable"}`

YAML から regex / 規程を読む形式でテストする（プロダクションとのドリフト防止）。
