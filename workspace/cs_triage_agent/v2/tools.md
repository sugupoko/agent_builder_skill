# ツール仕様: CS Triage Agent v1

> `retrieve` ノード内でカテゴリに応じてコードが呼び分ける関数群。
> 形式上は LangChain `@tool` でデコレートしておくが、v1 では LLM の直接呼び出しを禁止し、ノード内のディスパッチ関数経由で呼ぶ（誤呼び出し防止）。

---

## 設計原則（再掲）

- **最小権限**: 各ツールは 1 種類の DB / API のみ参照。書き込み権限なし
- **キャッシュ**: 同一引数の二重呼び出しを禁止（プロセス内 dict、リクエスト終了時クリア）
- **エラー設計**: 例外を投げず、`{"ok": False, "reason": "..."}` 形式で失敗を返す
- **出力サイズ制限**: 戻り値 JSON は 2 KB 以内に収まるように整形（過剰な fields を返さない）

---

## 1. `lookup_inventory(sku: str) -> dict`

**機能**: 在庫 DB から指定型番の在庫数と最短出荷日を取得する。

**入力**:
- `sku` (str): 型番（regex で抽出済み・正規化済み）。例: `"SHA-M6-20-N"`

**出力**:
```python
{
    "ok": True,
    "sku": "SHA-M6-20-N",
    "stock_qty": 1234,           # 在庫数
    "ship_eta_days": 1,          # 最短出荷日（営業日）
    "warehouse": "TKO-01",       # 出荷拠点コード（内部メモ用）
    "as_of": "2026-05-08T10:00:00+09:00",
}
```

**失敗時**:
```python
{"ok": False, "reason": "sku_not_found", "sku": "..."}    # マスタ未登録
{"ok": False, "reason": "db_unavailable"}                 # DB 接続失敗
```

**権限**: 在庫 DB の `inventory_view`（read-only ビュー）のみ。書き込み・削除権限なし。

**キャッシュ**: `(sku,)` をキーに dict キャッシュ。1 リクエスト内で同 SKU が複数回問い合わせされても 1 回のクエリで済む。

**呼び出し制限**: 1 リクエストあたり最大 10 SKU まで（メール 1 通の典型最大値）。

**実装メモ（v1 はモック）**:
```python
# scripts/eval/mock_db/inventory.yaml
SHA-M6-20-N:
  stock_qty: 1234
  ship_eta_days: 1
  warehouse: TKO-01
SHA-M8-25-N:
  stock_qty: 0
  ship_eta_days: 14
  warehouse: TKO-02
```

---

## 2. `lookup_price(sku: str, customer_id: Optional[str] = None) -> dict`

**機能**: 価格 DB（ERP API 経由）から指定型番の単価を取得。取引先 ID があれば取引先別価格を返す。

**入力**:
- `sku` (str): 型番
- `customer_id` (Optional[str]): 取引先コード（Salesforce ケースから取得）

**出力**:
```python
{
    "ok": True,
    "sku": "SHA-M6-20-N",
    "list_price_jpy": 250,       # 標準小売価格
    "customer_price_jpy": 220,   # 取引先別価格（customer_id 指定時）
    "currency": "JPY",
    "as_of": "2026-05-08T10:00:00+09:00",
}
```

**失敗時**:
```python
{"ok": False, "reason": "sku_not_in_price_db"}
{"ok": False, "reason": "erp_api_timeout"}
{"ok": False, "reason": "auth_failed"}
```

**権限**: ERP API の `read:price` スコープ（OAuth, Okta SSO 経由）のみ。

**呼び出し制限**: 1 リクエストあたり最大 10 SKU まで。

**v1 でのスコープ**: customer_id 連携は spec.md §16-1 「DB スキーマ」に絡むため後回し可。標準小売価格のみ返す形で v1 は仕上げる。

---

## 3. `lookup_shipment(order_no: str) -> dict`

**機能**: 出荷 DB から注文番号に対する出荷状況・追跡 URL を取得。

**入力**:
- `order_no` (str): 注文番号（regex で抽出済み）。例: `"ORD-2026-12345"`

**出力**:
```python
{
    "ok": True,
    "order_no": "ORD-2026-12345",
    "status": "shipped",              # pending / shipped / delivered / canceled
    "ship_date": "2026-05-07",
    "carrier": "yamato",
    "tracking_no": "12345-67890",
    "tracking_url": "https://example.com/track/12345-67890",
    "estimated_delivery": "2026-05-09",
    "delay_reason": null,             # 遅延時のみ str
}
```

**失敗時**:
```python
{"ok": False, "reason": "order_not_found"}
{"ok": False, "reason": "db_unavailable"}
```

**権限**: 出荷 DB の `shipment_view`（read-only ビュー）のみ。

**キャッシュ**: `(order_no,)` で 1 リクエスト内のみキャッシュ。

**呼び出し制限**: 1 リクエストあたり最大 5 注文番号まで。

---

## 4. `lookup_discontinued(sku: str) -> dict`

**機能**: 廃番マスタから指定型番の廃番状況・後継品を取得。

**入力**:
- `sku` (str): 型番

**出力**:
```python
{
    "ok": True,
    "sku": "SHA-M6-20-N",
    "is_discontinued": True,
    "discontinued_date": "2025-12-31",
    "successor_sku": "SHA-M6-20-N2",  # 後継品（無ければ null）
    "reason": "材質変更（旧品 SS400 → 新品 SUS304）",
    "compatibility_note": "寸法・取付互換あり、耐食性向上",
}
```

**失敗時**:
```python
{"ok": False, "reason": "sku_not_in_discontinued_master"}
{"ok": False, "reason": "db_unavailable"}
```

**権限**: 廃番マスタの read-only ビュー。

**呼び出し制限**: 1 リクエストあたり最大 10 SKU まで。

**注意**: `is_discontinued: False` でも「ok: True」で返す（型番が現役品である情報も価値）。

---

## 5. `lookup_cad_url(sku: str) -> dict`

**機能**: CAD ライブラリ（社内ファイルサーバ）から指定型番の図面 URL を返す。

**入力**:
- `sku` (str): 型番

**出力**:
```python
{
    "ok": True,
    "sku": "SHA-M6-20-N",
    "cad_url": "https://cad.internal.example.com/sha/m6-20-n.step",
    "pdf_url": "https://cad.internal.example.com/sha/m6-20-n.pdf",
    "format": ["STEP", "PDF"],
    "size_kb": 245,
}
```

**失敗時**:
```python
{"ok": False, "reason": "cad_not_available"}
{"ok": False, "reason": "fileserver_unreachable"}
```

**権限**: 社内ファイルサーバの read-only。URL を返すだけで実ファイル配信は行わない（オペが顧客に URL を渡す方式）。

**v1 でのスコープ**: ファイル中身の OCR / 解析は不要。URL を返すだけ。

---

## 6. `mask_pii(text: str) -> tuple[str, dict[str, str]]`

**機能**: 顧客個人情報をプレースホルダに置換し、復元用マップとともに返す。

**入力**:
- `text` (str): 生のメール本文（PII 含む可能性）

**出力**:
- `(masked_text: str, pii_map: dict[str, str])`
  - `masked_text`: PII をプレースホルダに置換した文字列
  - `pii_map`: `{"[NAME_001]": "田中太郎", "[PHONE_001]": "090-1234-5678", ...}`

**マスキング対象**（v1 のスコープ）:
- 顧客氏名（姓名 / 姓のみ / 「○○様」「○○さん」のパターン）
- 電話番号（090/080/070/03/06 等の日本国内形式 + 国際形式 +81）
- メールアドレス
- 住所（都道府県 + 市区町村 + 番地のパターン）
- 会社名（spec.md §16-3 で「部分マスクか完全マスクか」未確定 → v1 では会社名はマスクしない）

**実装上の注意**:
- 同一文字列は同一プレースホルダにする（`田中太郎` が 2 回出てきたら両方 `[NAME_001]`）
- プレースホルダの形式: `[<TYPE>_<INDEX>]`、TYPE ∈ {NAME, PHONE, EMAIL, ADDR}
- マスキング前に **必ず regex のテストケースを 30 件以上** で検証（誤マスキング・取りこぼしともに監査対象）

**失敗時**:
- 例外は投げず、マスキング困難な箇所はそのまま残し `pii_map` に「漏れ警告」を含める:
  ```python
  pii_map["__warning__"] = ["unmaskable_pattern_at_pos_45"]
  ```
- `assemble` ノードで `__warning__` がある場合は内部メモに「PII マスキング警告あり」を追記

**LLM API への送信ルール**:
- LLM API には **必ず masked_text のみ送信**。pii_map は送らない
- 例外: assemble ノードで unmask する直前まで pii_map は state 内のみで保持、ログには pii_map のハッシュ値だけ残す

---

## 7. ディスパッチ関数（参考）

ノード `retrieve` 内で、カテゴリ別にツールを呼び分ける疑似コード:

```python
def retrieve_node(state: TriageState, cfg: dict) -> dict:
    skus = state["skus"]
    order_nos = state["order_nos"]
    category = state["category"]

    retrieved = {}
    errors = []

    if category in ("inventory", "alternative"):
        retrieved["inventory"] = {sku: lookup_inventory(sku) for sku in skus[:10]}
        retrieved["discontinued"] = {sku: lookup_discontinued(sku) for sku in skus[:10]}
        if category == "inventory":
            retrieved["price"] = {sku: lookup_price(sku) for sku in skus[:10]}

    if category == "shipment":
        retrieved["shipment"] = {ono: lookup_shipment(ono) for ono in order_nos[:5]}

    if category == "cad":
        retrieved["cad"] = {sku: lookup_cad_url(sku) for sku in skus[:10]}

    # 失敗を集約
    for kind, results in retrieved.items():
        for key, r in results.items():
            if not r.get("ok"):
                errors.append(f"{kind}:{key}:{r.get('reason')}")

    return {"retrieved_data": retrieved, "retrieve_errors": errors}
```

> ⚠️ ディスパッチ関数は **LLM が呼ぶのではなくコードが呼ぶ**。LangGraph の通常ノード（StateGraph の `add_node`）として実装し、Tool Use の柔軟性は犠牲にして決定論性を取る。

---

## 8. ツール呼び出し制限のまとめ

| ツール | 最大呼び出し回数 / リクエスト | キャッシュキー |
|---|---|---|
| `lookup_inventory` | 10 | `(sku,)` |
| `lookup_price` | 10 | `(sku, customer_id)` |
| `lookup_shipment` | 5 | `(order_no,)` |
| `lookup_discontinued` | 10 | `(sku,)` |
| `lookup_cad_url` | 10 | `(sku,)` |
| `mask_pii` | 1 | （preprocess 内のみ）|

リクエストごとに `clear_tool_cache()` で全キャッシュリセット。

---

## 9. 単体テスト指針

各ツールに対して以下のテストケースを最低 3 件:

1. **正常系**: 既知の SKU / 注文番号 → 期待値どおりの dict
2. **マスタ未登録**: 存在しない SKU → `{"ok": False, "reason": "sku_not_found"}`
3. **DB 不通**: モックで例外を発生 → `{"ok": False, "reason": "db_unavailable"}`

regex 系は **YAML から regex を読み込む形式でテスト**（`tools_pattern.md` の罠を踏まないため）:

```python
# ❌ ハードコード禁止
SKU_PATTERNS = [{"name": "X", "regex": r"\b[A-Z]{2,5}-..."}]

# ✅ 正解
from src.config import load_config
_CFG = load_config(Path(__file__).parent.parent.parent / "config" / "cs_triage.yaml")
SKU_PATTERNS = _CFG["sku_patterns"]
```

→ regex の修正が production と test に同時反映される。

### 既知の罠（リファレンス v1 から学んだ教訓）

- **貪欲マッチで注文番号を SKU として取得してしまう**: `[A-Z0-9]+` のサフィックスが `-12346` を貪欲に取る
- 対策: 否定先読み `(?!(?:ORD|INV|REF|REQ|FAX|TEL)-)` で明示的に除外
- 単体テストで「既知バグ」と「修正済み」の両方を固定（修正時の回帰検知）

→ 詳細は `reference/tools_pattern.md` §「正規表現抽出の貪欲マッチの罠」
