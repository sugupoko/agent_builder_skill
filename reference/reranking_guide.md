# 取得情報のリランキング

ReAct や深掘りで「テーマ外の記事を選んでしまう」「サマリの要点が外れる」のを防ぐ。

---

## 問題

エージェントが「カテゴリ件数が多い順」で深掘り対象を選ぶと、テーマ外の記事を選んでしまうことがある。例:

```
カテゴリ「AI診断」に該当した13件のうち、
1位: 「子どもの精神疾患の遺伝研究」（テーマ外）  ← 件数順だとこれが1位になる
2位: 「○○社 Q1決算」（ど真ん中）
3位: ...
```

→ ReAct がテーマ外の1位を深掘りして、コストも品質も無駄になる。

---

## 解決: テーマ関連性スコアでリランキング

YAML に書かれた **sources.keywords + watchlist.entities.keywords** を全部集めて「テーマ語彙」とし、各記事のスコアを計算して並び替える。

```python
def collect_theme_keywords(cfg) -> list[str]:
    kws = set()
    for src in cfg.get("sources", []):
        for k in src.get("keywords", []):
            kws.add(k.lower())
    watchlist = cfg.get("watchlist", {})
    for ent in watchlist.get("entities", []):
        for k in ent.get("keywords", []):
            kws.add(k.lower())
    return sorted(kws)


def relevance_score(item, keywords) -> int:
    """タイトル一致 +2、snippet 一致 +1、カテゴリ一致 +1。"""
    title = (item.title or "").lower()
    snippet = (item.snippet or "").lower()
    category = (item.category or "").lower()
    score = 0
    for k in keywords:
        if not k:
            continue
        if k in title:
            score += 2
        elif k in snippet:
            score += 1
        if k in category:
            score += 1
    return score


def rerank(items, cfg) -> list:
    kws = collect_theme_keywords(cfg)
    if not kws:
        return items
    return sorted(
        items,
        key=lambda it: (relevance_score(it, kws),
                        it.published or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True
    )
```

---

## 適用箇所

### Step 1: fetch ノード末尾でリランク
```python
def fetch_node(state):
    items = collect_from_sources(...)
    items = dedup(items)
    items = rerank(items, state["cfg"])  # ← ここ
    return {"items": items}
```

### Step 2: deep_dive で score > 0 のみ対象
```python
def deep_dive_node(state):
    kws = collect_theme_keywords(state["cfg"])
    items = state["items"]
    
    # スコア順は fetch_node で済んでいる
    pick = []
    for item in items:
        if relevance_score(item, kws) <= 0:
            continue  # テーマ外をスキップ
        pick.append(item)
        if len(pick) >= n_topics:
            break
    
    if not pick:
        logger.warning("テーマ関連スコア > 0 の記事がない")
        return {"deep_dives": []}
    
    # 深掘り実行
    ...
```

### Step 3: summarize の入力もリランク後の上位 60 件
```python
def summarize_node(state):
    items = state["items"][:60]  # ← 上位60件のみ
    # ...
```

---

## 効果（実例）

news_searcher で適用前後を比較:

### 適用前
- 深掘り対象: 「子どものうつ・不安の遺伝経路」（テーマ外）
- 件名: テーマ汎用
- コスト: 同じ $0.34

### 適用後
- 深掘り対象: 「○○社 Q1決算」（ど真ん中）
- 件名: 「○○社 Q1売上+36%、△△は新市場へ侵食開始」
- 編集者の見立てが鋭い独自視点に

リランキングがあるだけで深掘り対象がテーマ忠実になり、出力品質が大幅に改善する。

---

## 重み付けのチューニング

デフォルトは「タイトル+2 / snippet+1 / category+1」だが、テーマによって調整:

```python
def relevance_score(item, keywords, weights={"title": 2, "snippet": 1, "category": 1}):
    score = 0
    for k in keywords:
        if k in item.title.lower():
            score += weights["title"]
        elif k in item.snippet.lower():
            score += weights["snippet"]
        if k in item.category.lower():
            score += weights["category"]
    return score
```

「タイトルだけで判定したい」なら `{"title": 3, "snippet": 0, "category": 1}` のように調整。

---

## 高度: TF-IDF や埋め込みベース

語彙ベースのスコアでは精度が頭打ちなら:

### TF-IDF
```python
from sklearn.feature_extraction.text import TfidfVectorizer

corpus = [it.title + " " + it.snippet for it in items]
vec = TfidfVectorizer().fit(corpus)
theme_vec = vec.transform([" ".join(theme_keywords)])
item_vecs = vec.transform(corpus)
similarities = item_vecs @ theme_vec.T
```

### 埋め込みベース（OpenAI/Cohere/sentence-transformers）
```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("all-MiniLM-L6-v2")
theme_emb = model.encode(" ".join(theme_keywords))
item_embs = model.encode([it.title for it in items])
similarities = (item_embs @ theme_emb).tolist()
```

ただし語彙ベースで十分な精度が出るケースが多い。**まず単純な語彙スコアから**始めて、不足を感じたら高度化。

---

## 落とし穴

### キーワードが少なすぎる
sources.keywords の合計が10語未満だとスコアの解像度が低くなる。
最低 30〜50 語が目安。

### スコア=0 で何も残らない
リランキング後に scope > 0 の記事が極端に少ない場合、キーワード設計が不足。
深掘りスキップ（fallback）でしのぎつつ、キーワードを追加。

### 動的に変わるテーマ語彙
業界トレンドで新しい用語が出てくる。**月次でキーワードを見直す**運用を。

---

## まとめ

- **テーマ関連性スコアで取得情報をリランキング**: タイトル+2、snippet+1
- **fetch ノード末尾で適用**: 後続の summarize / deep_dive 全部が恩恵を受ける
- **score > 0 で深掘り対象を絞る**: テーマ外への脱線を防止
- **語彙ベースで十分**: TF-IDF や埋め込みは必要に応じて導入
