# コスト管理ガイド

LLM API のトークン課金は気を抜くと暴走する。設計段階から**段階的軽量モード + 上限ガード + 計測**を組み込む。

---

## 1. 計測: トークン使用量を全てのLLM呼び出しで集計

### LangChain での実装

```python
USAGE = {"input_tokens": 0, "output_tokens": 0,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

def accumulate_usage(messages):
    for m in messages:
        meta = getattr(m, "usage_metadata", None) or {}
        for k in USAGE:
            USAGE[k] += int(meta.get(k, 0) or 0)
```

各ノード末尾で `accumulate_usage(result["messages"])` を呼ぶ。
ReAct も `agent.invoke(...)` の戻り値の messages から拾える。

### コスト計算

```python
RATE_INPUT = 3.00 / 1_000_000        # USD per token
RATE_OUTPUT = 15.00 / 1_000_000
RATE_CACHE_WRITE = 3.75 / 1_000_000
RATE_CACHE_READ = 0.30 / 1_000_000

def compute_cost():
    return (
        USAGE["input_tokens"] * RATE_INPUT +
        USAGE["output_tokens"] * RATE_OUTPUT +
        USAGE["cache_creation_input_tokens"] * RATE_CACHE_WRITE +
        USAGE["cache_read_input_tokens"] * RATE_CACHE_READ
    )
```

実コストとは ±50% ほどズレうるので「目安」と認識する（API コンソールで定期確認）。

---

## 2. 段階的軽量モード

ユーザーが明示的に機能を絞れる CLI 引数を用意:

```python
ap.add_argument("--no-supplement", action="store_true",
                help="補強リサーチをスキップ")
ap.add_argument("--no-deep-dive", action="store_true",
                help="深掘りをスキップ")
ap.add_argument("--topics", type=int, default=2,
                help="深掘り件数")
ap.add_argument("--thinking", action="store_true",
                help="extended thinking を有効化（コスト増）")
```

| モード | 概算コスト |
|---|---|
| `--no-deep-dive --no-supplement` | $0.02〜0.05 |
| `--no-deep-dive` | $0.20〜0.40 |
| `--topics 1 --no-supplement` | $0.30〜0.40 |
| `--topics 1` (補強あり) | $0.30〜0.50 |
| `--topics 2 --thinking` | $1.00〜1.30 |

---

## 3. ツールキャッシュで重複叩きを防ぐ

ReAct が同じ URL/クエリを何度も叩くとトークン消費が線形に増える。

```python
_CACHE = {}

@tool
def search(query, max_items=5):
    key = (query.lower(), max_items)
    if key in _CACHE:
        return _CACHE[key] + "\n_(cache hit)_"
    result = actually_search(query, max_items)
    _CACHE[key] = result
    return result
```

agent_demo の実例（news_searcher）では news=8 / pubmed=5 / fetch_url=9 件のキャッシュヒットで実質コスト半減。

---

## 4. 単発実行の上限ガード

```python
ESTIMATED = compute_cost()
MAX_PER_RUN = float(cfg.get("max_cost_per_run_usd", 1.0))
if ESTIMATED > MAX_PER_RUN:
    logger.warning("コスト上限超過: $%.4f > $%.2f", ESTIMATED, MAX_PER_RUN)
    notify_slack(f"⚠️ コスト超過: ${ESTIMATED:.2f}")
    # 軽量モードで再実行 or 中断
```

---

## 5. 月間予算管理

`data/cost_log.csv` に1実行1行記録:

```csv
timestamp,run_id,theme,mode,input_tokens,output_tokens,cost_usd
2026-05-06T08:00:00,run_001,my_theme,full,153801,10335,0.6164
2026-05-13T08:00:00,run_002,my_theme,full,148000,9800,0.5910
```

集計スクリプト:

```python
import pandas as pd
df = pd.read_csv("data/cost_log.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"])
this_month = df[df["timestamp"].dt.to_period("M") == pd.Timestamp.now().to_period("M")]
print(f"今月の累計: ${this_month['cost_usd'].sum():.2f}")
print(f"月間予算 $30 中: {this_month['cost_usd'].sum() / 30 * 100:.0f}%")
```

80% 到達でアラート。

---

## 6. プロンプトキャッシュ（高度・任意）

Anthropic API の prompt caching を有効にすると、同じシステムプロンプトの再送が安くなる。

```python
client.messages.create(
    model="claude-sonnet-4-6",
    system=[{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"}  # ← これ
    }],
    messages=[...]
)
```

注意:
- 1024トークン以上のキャッシュ可能なブロック
- 5分の TTL
- キャッシュ書き込みは 1.25倍、読み込みは 0.1倍コスト
- 週次バッチには効きにくい（5分で消える）が、同一ユーザーが連続呼び出しする場面では強力

---

## 7. モデルの使い分け

すべてのノードで Sonnet/Opus は不要。安いモデルで済む場面を見つける:

| ノード | 推奨モデル | 理由 |
|---|---|---|
| 要約・編集 | Sonnet 4.6 | 文章品質が直接成果に影響 |
| 分類・選別 | Haiku 4.5 | 安く速い |
| LLM as a judge | Haiku 4.5 | 大量に走らせるため |
| Tool 引数の整形 | Haiku 4.5 | 軽量タスク |

`agent_demo.py` で複数モデルを使う:

```python
SUMMARIZE_MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = "claude-haiku-4-5-20251001"
```

---

## 8. コストが膨らむ典型パターン（要警戒）

| 症状 | 原因 | 対処 |
|---|---|---|
| ReAct が止まらない | recursion_limit 未設定 | config={"recursion_limit": 40} 必須 |
| 同じURLを何度も fetch | キャッシュ未実装 | tools にキャッシュ追加 |
| プロンプトが肥大化 | 全 messages を毎回送信 | 不要な context を切り詰める |
| Multi-Agent の議論ループ | 終了条件未定義 | 最大ラウンド数を設定 |
| Reflection の無限改善 | 上限未設定 | MAX_REFLECTIONS = 3 など |
| fetch_url の長文取得 | デフォルトで全文 | max_chars=1500 など制限 |

---

## まとめ

- **計測は必須**: 全ての LLM 呼び出しで usage_metadata を集計
- **段階的軽量モード**: 機能を切れる CLI 引数を用意
- **ツールキャッシュ**: 同一引数の2回叩きを禁止
- **上限ガード**: 単発・月間の両方
- **モデル使い分け**: Sonnet/Haiku の二段運用
- **典型パターンを避ける**: recursion_limit, max_chars, MAX_REFLECTIONS の3点セット必須
