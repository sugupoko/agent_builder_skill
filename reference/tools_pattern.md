# ツール設計パターン（Tool Use）

ReAct エージェントが使うツールの設計指針。最小権限・キャッシュ・エラー設計が3点セット。

---

## ツール設計の原則

### 1. 最小権限

各ツールは「必要最小限のアクセス」のみ持つ。

```python
@tool
def search_news(query: str, lang: str = "ja", max_items: int = 5) -> str:
    """Google News から検索する（読み取りのみ）"""
    ...
```

❌ 避けるべき: 全DB アクセス可、全ファイルアクセス可、メール送信可、など強すぎるツール。

---

### 2. キャッシュ（重複呼び出し防止）

ReAct はループの中で同じ URL/クエリを何度も叩きやすい。プロセス内キャッシュ必須。

```python
_CACHE = {}

@tool
def search_news(query, lang="ja", max_items=5):
    """Google News から検索する。同一クエリは2回叩かない（キャッシュ済みを返すだけ）"""
    key = (query.strip().lower(), lang, max_items)
    if key in _CACHE:
        return _CACHE[key] + "\n\n_(cache hit)_"
    result = fetch_actually(query, lang, max_items)
    _CACHE[key] = result
    return result

def clear_tool_cache():
    _CACHE.clear()
```

エージェント実行開始時に `clear_tool_cache()` でリセット。

---

### 3. エラー設計

ツール失敗時の挙動を明示する:

```python
@tool
def fetch_url(url: str, max_chars: int = 1500) -> str:
    """URLからテキストを取得する。失敗したらエラー文字列を返す（例外を投げない）"""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        text = clean_html(r.text)
        return text[:max_chars]
    except requests.HTTPError as e:
        return f"(HTTP error {e.response.status_code}: {url})"
    except requests.Timeout:
        return f"(timeout: {url})"
    except Exception as e:
        return f"(fetch error: {e})"
```

LLM が「失敗したから別のツールを使う」「失敗を最終回答に書く」など対応できるよう、**失敗を文字列として返す**のが ReAct パターンの定石。

---

## docstring の書き方（重要）

LLM はツールを `docstring` を読んで使う。明確に書く:

```python
@tool
def search_news(query: str, lang: str = "ja", max_items: int = 5) -> str:
    """Google News から最新ニュースをキーワード検索する。
    
    背景情報（過去の経緯、関連する競合動向、関連法規制）を調べたいときに使う。
    同一クエリを連続で叩かないこと（キャッシュ済みの結果を返すだけになる）。
    
    Args:
        query: 検索クエリ。例: "○○社 提携"
        lang: "ja" (日本語) または "en" (英語)
        max_items: 取得件数上限 (1〜10)
    """
```

ポイント:
- 1行目で機能を端的に
- いつ使うかを明示（LLM が判断材料にする）
- 落とし穴・制約を docstring に書く（「2回叩かない」など）
- 各引数の意味・例を添える

---

## ツールのループ制御

ReAct が同じツールを延々と呼ぶのを防ぐ。

### LangGraph の recursion_limit

```python
agent.invoke(
    {"messages": [HumanMessage(content=user_msg)]},
    config={"recursion_limit": 40},  # 最大40ステップ
)
```

ステップ = ノード遷移の回数。ReAct は1回の判断で2ステップ消費する（agent → tool → agent）。

### プロンプトでも明示

```python
prompt = """
あなたは...のリサーチアナリストです。
ツールを使って**最大5回まで**調査し、最後に Markdown で結果をまとめてください。

**ツール使用ルール**:
- 同一クエリ・URL を繰り返し叩かない
- 5回呼び出した時点で必ず最終出力を返す
"""
```

prompt + recursion_limit の二段構え。

---

## 出力制御

ツールが返す情報をコントロールして、LLM が変なものを使わないようにする:

### 例: search_news の出力フォーマット

```python
def format_results(entries):
    return "\n".join(
        f"- [{e.published}] {e.title}\n  {e.link}"
        for e in entries
    )
```

LLM がパース可能な統一フォーマットで返す。

### 例: 取得サイズの制限

```python
@tool
def fetch_url(url, max_chars=1500):
    """URLからテキスト取得（最大3000文字）"""
    n = max(200, min(3000, max_chars))
    text = clean_html(fetch(url))
    return text[:n]
```

長文の HTML を全部 LLM に渡すとトークン爆発。**ハードリミットを設定**。

---

## ツールの分割粒度

「1つのツールで何でもできる」より「**1つの目的に1つのツール**」。

❌ Bad:
```python
@tool
def universal_tool(action: str, params: dict) -> str:
    """何でもできるツール。actionで分岐"""
```

LLM が `action` の値を毎回判断する必要があり、間違いが起きやすい。

✓ Good:
```python
@tool
def search_news(query, lang, max_items): ...

@tool
def search_pubmed(query, max_items): ...

@tool
def fetch_url(url, max_chars): ...
```

LLM はツール名から用途を推測する。**用途ごとに分けた方が誤呼び出しが減る**。

---

## news_searcher での実例

```python
# src/agent_tools.py

from langchain_core.tools import tool

_NEWS_CACHE = {}
_PUBMED_CACHE = {}
_FETCH_CACHE = {}


@tool
def search_news(query: str, lang: str = "ja", max_items: int = 5) -> str:
    """Google News から最新ニュースをキーワード検索する。"""
    n = max(1, min(10, max_items))
    key = (query.strip().lower(), lang, n)
    if key in _NEWS_CACHE:
        return _NEWS_CACHE[key] + "\n\n_(cache hit)_"
    # ...実装...


@tool
def search_pubmed(query: str, max_items: int = 5) -> str:
    """PubMed で論文を検索する。"""
    # ...


@tool
def fetch_url(url: str, max_chars: int = 1500) -> str:
    """指定URLから本文テキストを取得する。"""
    # ...


def clear_tool_cache():
    _NEWS_CACHE.clear()
    _PUBMED_CACHE.clear()
    _FETCH_CACHE.clear()


ALL_TOOLS = [search_news, search_pubmed, fetch_url]
```

エージェント実行開始時に `clear_tool_cache()` を呼ぶ:
```python
def main():
    reset_usage()
    clear_tool_cache()
    # ...
```

---

## ツール後処理（前置き除去）

ReAct の最終応答にありがちな「ここまで調査して〜」「以下にまとめます」を除去:

```python
_PREAMBLE_PATTERNS = [
    r"^これで十分な情報[^\n]*\n+",
    r"^以下に(?:調査結果|まとめ|結果)[^\n]*\n+",
    r"^これまでに収集した情報[^\n]*\n+",
    r"^最終(?:レポート|出力)を(?:作成|構成)[^\n]*\n+",
]

def strip_preamble(text):
    for pat in _PREAMBLE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.MULTILINE)
    return text.strip()
```

プロンプトで「前置きは禁止」と書いても LLM はしばしば書いてしまう。**プロンプト + 後処理の二段構え**で除去。

---

## ⚠️ 正規表現抽出の貪欲マッチの罠 (実体験ベース)

エンティティ抽出 (型番・注文番号等) で正規表現を使うとき、**貪欲マッチで意図しないものを拾う**ことが頻出する。

### 失敗事例 (cs_triage_agent v1)

```python
# ❌ 当初の SKU regex
SKU = r'\b[A-Z]{2,5}-[A-Z]?M?\d{1,4}(?:-\d{1,4})?(?:-[A-Z0-9]+)?\b'

# 想定: SHA-M6-20-N をマッチ ✓
# 実際: 注文番号 ORD-2026-12346 全体もマッチ ✗
#       (末尾オプション (?:-[A-Z0-9]+)? が -12346 を貪欲に取る、
#        [A-Z0-9] には数字が含まれるため)
```

問い合わせ本文に注文番号があるだけで「ORD-2026-12346 を SKU として処理」してしまい、無駄な DB lookup が走る + LLM が混乱する。

### 修正パターン: 否定先読み + 末尾オプションの厳密化

```python
# ✅ 修正後
SKU = r'\b(?!(?:ORD|INV|ECP|REF|REQ|FAX|TEL)-)[A-Z]{2,5}-[A-Z]?M?\d{1,4}(?:-\d{1,4})?(?:-[A-Z]+\d*)?\b'
#         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^                              ^^^^^^^^^^^^^
#         否定先読みで明示的に除外                                          数字単独サフィックスを許可しない
```

### 根本対策の選択肢

1. **否定先読み (今回採用)**: シンプル、特定の prefix だけ除外。スケールしない可能性
2. **whitelist アプローチ**: 既知の prefix (`SHA|BLR|SUS...`) のみマッチ。安全だがメンテ必要
3. **post-filter**: regex で抽出後、DB ヒット存在確認で絞り込む。意味的に正しいが実装コスト

実プロジェクトでは段階的に: (1) で発進 → 誤マッチ事例が増えたら (3) へ移行が現実的。

### テスト固定の重要性

regex の挙動は微修正で別物になりやすい。**バグ事例を「既知の挙動」として単体テスト固定**しておくと、修正時の回帰検知に便利:

```python
def test_known_issue_order_id_matches_sku_regex():
    """v1 で誤マッチする (バグ)。v2 で修正したらアサーションを反転する。"""
    skus = extract_skus("注文番号 ORD-2026-12346 を確認", PATTERNS)
    assert any(s.startswith("ORD-") for s in skus), "誤マッチ (既知)"

# 修正後は:
def test_order_id_not_matched_as_sku():
    skus = extract_skus("注文番号 ORD-2026-12346 を確認", PATTERNS)
    assert not any(s.startswith("ORD-") for s in skus), "修正済"
```

→ 詳細: `workspace/cs_triage_agent/v1/scripts/eval/tools/test_entities.py`

---

## ⚠️ regex を YAML から読む設計の罠

「テスト時にハードコードした regex と本番 YAML の regex がズレる」事故が頻発。

### 失敗例

```python
# ❌ ダメ: テストで regex をハードコード
SKU_PATTERNS = [
    {"name": "X", "regex": r"\b[A-Z]{2,5}-..."},  # 古いまま
]

def test_extract():
    skus = extract_skus(text, SKU_PATTERNS)
    ...
```

YAML 側 regex を修正しても、テストはハードコード版を使うので「テスト全パス」なのに本番で失敗する。

### 正解: テストも YAML から読む

```python
# ✅ 正解
from src.config import load_config

_CFG = load_config(Path(__file__).parent.parent.parent / "config" / "..yaml")
SKU_PATTERNS = _CFG["sku_patterns"]
```

→ regex の修正が production と test に同時反映される。

---

## まとめ

- **最小権限**: ツールは必要最小限のアクセスのみ
- **キャッシュ**: 同一引数の2回叩きを禁止
- **エラー設計**: 例外を投げず、失敗を文字列として返す
- **明確な docstring**: LLM の判断材料、いつ使うか・落とし穴を書く
- **ループ制御**: recursion_limit + プロンプトで「最大N回」
- **出力サイズ制限**: max_chars でトークン爆発防止
- **分割粒度**: 1目的に1ツール
- **後処理**: 前置き除去で出力をクリーンに
- **regex の貪欲マッチ罠**: 否定先読み or whitelist or post-filter で対処
- **regex は YAML から読む**: テストとプロダクションの drift 防止
