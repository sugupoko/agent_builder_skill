# 跨実行の重複排除（SQLite）

週次バッチで「先週流れた記事が今週もまた流れる」のを防ぐ。SQLite でローカル DB を作る。

---

## 動機

リサーチ系エージェントは毎回同じソースを叩くため、過去に取り上げた記事が繰り返しヒットする。

- 例: 先週 ○○社 Q1決算ニュースを配信 → 今週も同じURLが拾われる
- 解決: ローカルDBに既出URLを記録、過去7〜90日以内のものは弾く

---

## 設計

### スキーマ

```sql
CREATE TABLE articles (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    norm_title    TEXT NOT NULL,         -- 正規化タイトル（ページ番号等を除いたもの）
    published     TEXT,
    source_name   TEXT,
    publisher     TEXT,
    category      TEXT,
    region        TEXT,
    snippet       TEXT,
    theme_slug    TEXT,                  -- どのテーマで取得したか
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX idx_norm_title ON articles(norm_title);
CREATE INDEX idx_first_seen ON articles(first_seen_at);
```

### 重複判定の2軸

- **URL 完全一致**
- **正規化タイトル一致**（「資料 16ページ」「資料 11ページ」を同案件としてマージ）

---

## 実装の核

```python
class ArticleDB:
    def __init__(self, path="data/articles.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
    
    def has_seen(self, url, norm_title, window_days=None):
        sql = "SELECT 1 FROM articles WHERE (url = ? OR norm_title = ?)"
        params = [url, norm_title]
        if window_days:
            cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
            sql += " AND first_seen_at >= ?"
            params.append(cutoff)
        return self.conn.execute(sql + " LIMIT 1", params).fetchone() is not None
    
    def mark_seen(self, item, theme_slug, norm_title):
        now = datetime.now(UTC).isoformat()
        self.conn.execute("""
            INSERT INTO articles (url, title, norm_title, ..., first_seen_at, last_seen_at)
            VALUES (...)
            ON CONFLICT(url) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """, (...))
```

---

## 正規化タイトル

「資料 16ページ」「資料 11ページ」など、**同案件の別ページ番号**を吸収するため:

```python
import re

def normalize_title(title):
    s = title.strip().lower()
    # ページ番号・回数表記などを除く
    s = re.sub(r"\d+\s*ページ|page\s*\d+|第\s*\d+\s*回|\(\d+/\d+\)", "", s)
    # 末尾の「 - 出版社名」を除く
    s = re.sub(r"\s*-\s*[A-Za-zぁ-んァ-ヶ一-龥0-9・\.\-_ ]{1,40}$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
```

---

## fetch_node に組み込む

```python
def fetch_node(state):
    cfg = state["cfg"]
    items = collect_from_sources(cfg["sources"])
    items = smart_dedupe(items)  # 同実行内の重複排除
    
    if cfg.get("dedup_across_runs", True):
        db = ArticleDB(cfg.get("db_path", "data/articles.db"))
        slug = cfg.get("slug", "default")
        window = cfg.get("dedup_window_days")
        new_items = []
        skipped = 0
        try:
            for it in items:
                norm = normalize_title(it.title)
                if db.has_seen(it.url, norm, window_days=window):
                    skipped += 1
                    continue
                db.mark_seen(it, theme_slug=slug, norm_title=norm)
                new_items.append(it)
            db.commit()
            logger.info("DB跨実行重複: %d件除外、新規 %d件", skipped, len(new_items))
        finally:
            db.close()
        items = new_items
    
    items = rerank(items, cfg)
    return {"items": items}
```

---

## YAML オプション

```yaml
dedup_across_runs: true       # 跨実行の重複排除を有効化
dedup_window_days: 90         # 直近90日のみ参照（古い記録は無視）
db_path: data/articles.db     # DB ファイルの場所
```

`dedup_window_days` は:
- `90` → 3ヶ月以上前の記録は気にしない（古い情報のリバイバルは新規扱い）
- `null` → 全期間（蓄積するほど厳しくなる）
- `0` → 無効（リアルタイム重複のみ）

---

## 補強リサーチで得た記事は除外する

ReAct で動的に取った補強リサーチの記事は、**毎回探させたい**ので DB チェック対象外にする:

```python
for it in items:
    if it.category == "補強リサーチ":
        new_items.append(it)
        continue
    # 通常記事のみ DB チェック
```

---

## 管理 CLI

```python
# db_cli.py
def cmd_stats(args):
    db = ArticleDB(args.db)
    s = db.stats()
    print(f"総件数: {s['total']}")
    print(f"テーマ別: {s['by_theme']}")
    print(f"最古: {s['oldest_first_seen']}")

def cmd_purge(args):
    db = ArticleDB(args.db)
    n = db.purge_older_than(args.days)
    print(f"{n}件削除")

def cmd_reset(args):
    Path(args.db).unlink()
```

```bash
python db_cli.py stats              # 件数を確認
python db_cli.py purge --days 180   # 半年前より古いのを削除
python db_cli.py reset              # 全消し（注意）
```

---

## DB の成長と管理

### 月間目安

- 100記事/週 × 4週 = 400記事/月
- 90日で 1200記事
- SQLite は数万件まで快適に動く

### 定期的なメンテナンス

`dedup_window_days = 90` を設定していれば、古い記録は判定に使われない。
さらに DB サイズが気になれば `purge_older_than(180)` で物理削除。

### バックアップ

毎日 `data/articles.db` を `data/backups/articles_<日付>.db` にコピー。
ファイル破損時はバックアップから復元。

---

## 落とし穴

### 同じ案件で URL が変わる
配信元が `?utm_source=...` などのパラメータを追加するとURLが変わって重複判定が効かなくなる。
URL 正規化（クエリパラメータの除去）も組み込むと安心:

```python
from urllib.parse import urlparse, urlunparse
def normalize_url(url):
    p = urlparse(url)
    return urlunparse(p._replace(query="", fragment=""))
```

### 正規化タイトルが過剰にマージ
似たタイトルだが別案件をマージしてしまうケース。`norm_title` の正規表現は控えめに、誤マージが多いなら緩める。

### マルチテーマで DB を共有
同じ DB を複数テーマで使うと、テーマAで見た記事がテーマBで弾かれる。
**テーマごとに DB ファイルを分ける** か、`has_seen` の判定に theme_slug を含める。

---

## まとめ

- **SQLite で跨実行の重複排除**: 軽量・標準ライブラリ・WAL で並行アクセスも安全
- **URL + 正規化タイトル の2軸**: 微妙な違いの同案件もマージ
- **window_days で時間制限**: 古い記録は無視
- **補強リサーチは除外**: 毎回新鮮さを保つ
- **管理CLI を用意**: stats / purge / reset
