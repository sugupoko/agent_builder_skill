# 評価駆動開発（Eval-driven Development）

> 出典: [LayerX 評価駆動開発記事](https://tech.layerx.co.jp/entry/2024/12/12/191131)

LLM アプリで「動くものを作ったが、これでいいのか分からない」状態を避けるために、**評価サイクルを最初から回す**。

---

## 核心: データセットは少数で OK

LayerX の指針は「**ユースケースごとに代表的な少数（十数個）のデータセットを作成する**」。
最初から大量のデータは不要。**評価プロセス自体を回し始めることが最優先**。

---

## 3軸の評価方法

| 軸 | 速度 | 信頼性 | コスト | 用途 |
|---|---|---|---|---|
| **コードベース** | ◎ | ○ | 0 | 必須要件のチェック（数値・固有名詞・形式） |
| **LLM as a judge** | ○ | △ | 中 | 文体・視点・読みやすさの評価 |
| **人間評価** | × | ◎ | 高 | 最終判定・複雑なニュアンス |

最初は **コードベース + 人間評価（抜き取り）** で十分。
LLM as a judge は精度の見極めが必要なので、慣れてから導入。

---

## 評価サイクルの流れ

```
Step 1: 代表ケース 10〜20件 を業務担当と作成
  ↓
Step 2: 各ケースに「期待される出力」「必須要件」をメタデータとして付与
  ↓
Step 3: コードベース評価関数を書く（30分）
  ↓
Step 4: エージェントを各ケースで実行
  ↓
Step 5: 結果を集計、合否判定
  ↓
Step 6: 失敗ケースを分析 → 改善 → Step 4 に戻る
  ↓
Step 7: リリース後、ユーザーフィードバックでデータセット拡充
```

---

## データセットの作り方

### ファイル構造

```
eval/dataset/
├── case_001/
│   ├── input.json or input.md          ← 入力データ
│   ├── expected.md or expected.json    ← 期待される出力（参考）
│   └── metadata.yaml                    ← 評価指標
├── case_002/
└── ...
```

### metadata.yaml の例

```yaml
name: "競合A社の決算記事を含む週"
description: "決算ニュースが多く、ベンダー監視が機能するかをテスト"

expected:
  must_include_entities:
    - "A社"
    - "決算"
    - "$348M"
  must_not_include_entities:
    - "無関係なゲーム会社"
  min_citations: 5
  min_action_items: 3
  max_cost_usd: 0.50
  max_duration_seconds: 300

tags: [decimal_news, vendor_watch, hot_week]
```

### ケースの選び方

代表性を意識して以下の組み合わせを揃える:

- **典型ケース**: 普段の典型的な入力（5件）
- **エッジケース**: 件数が極端に多い/少ない週、テーマ外ノイズが多い週（5件）
- **失敗パターン**: 過去にうまくいかなかったパターン（3件）
- **新パターン**: リリース後に拾えなかったケース（拡充）

---

## コードベース評価の実装

`reference/eval_skeleton.py` の雛形:

```python
import re
from pathlib import Path
import yaml

def evaluate_output(output: str, metadata: dict) -> dict:
    """出力に対してコードベース評価を実行する。"""
    expected = metadata["expected"]
    score = {}
    
    # 必須語の含有率
    must_include = expected.get("must_include_entities", [])
    if must_include:
        hits = sum(1 for k in must_include if k in output)
        score["must_include_rate"] = hits / len(must_include)
    
    # 禁止語のチェック
    must_not = expected.get("must_not_include_entities", [])
    score["must_not_violations"] = sum(1 for k in must_not if k in output)
    
    # 出典数
    score["citation_count"] = len(set(re.findall(r"\[(\d+)\]", output)))
    
    # アクション項目数
    score["action_count"] = len(re.findall(
        r"\*\*(?:今週中|来週まで|来月まで)\*\*", output
    ))
    
    # 合否判定
    score["passed"] = (
        score["must_include_rate"] >= 0.8
        and score["must_not_violations"] == 0
        and score["citation_count"] >= expected.get("min_citations", 0)
        and score["action_count"] >= expected.get("min_action_items", 0)
    )
    return score
```

---

## LLM as a judge

別の Claude に「評価者」役をさせる。

```python
JUDGE_PROMPT = """次のレポートを編集者の視点から評価してください。

評価軸:
1. 具体性 (1-5): 数字・固有名詞が含まれているか
2. 視点の独自性 (1-5): 「業界が活況」のような汎用表現がないか
3. アクション可能性 (1-5): 読者がすぐ動ける内容か
4. 整合性 (1-5): 出典と本文が矛盾していないか

各軸を 1-5 で採点し、根拠とともに JSON で返してください。

レポート:
{report}

出力フォーマット:
```json
{
  "specificity": 4,
  "uniqueness": 3,
  "actionability": 5,
  "consistency": 4,
  "comments": ["..."]
}
```"""

def llm_judge(report: str) -> dict:
    response = llm.invoke(JUDGE_PROMPT.format(report=report))
    return parse_json(response.content)
```

注意:
- 評価モデルは生成モデルより**安いモデル**を使う（Haiku など）
- 評価結果も人間でスポットチェックし、評価モデルの精度を担保

---

## 評価結果の集計

`eval/results/summary.md`:

```markdown
# v2 評価サマリ (vs v1)

## 全体メトリクス

| 指標 | v1 | v2 | 目標 |
|---|---|---|---|
| 必須語含有率 | 78% | 85% | 80%+ |
| 出典数（平均） | 4.2 | 5.4 | 3+ |
| アクション項目数 | 3.1 | 4.0 | 3+ |
| コスト（平均） | $0.34 | $0.32 | $0.50- |

## 改善内容
- v1→v2: editorial.rules に「数字を必ず含める」を追加
- v1→v2: blocklist に低信頼ソース 5 件追加

## 残課題
- ケース #5: 海外論文の引用がまだ少ない
- ケース #8: ベンダー監視で誤マッチ 1 件
```

---

## CI への組み込み

評価を CI に入れて回帰検出:

```yaml
# .github/workflows/eval.yml
name: agent eval
on: [push]
jobs:
  eval:
    steps:
      - run: pip install -r requirements.txt
      - run: python scripts/eval_run.py --version v_pr --dataset eval/dataset/
      - run: python scripts/eval_compare.py v_main v_pr  # しきい値割れで失敗
```

---

## 落とし穴

### 評価データを少なすぎる状態で長期運用

10件で OK、というのは「最初は」の話。半年〜1年で 50〜100件まで拡充するのが理想。
ユーザーフィードバックを機械的に取り込む仕組み（Slack スレッドのリアクションなど）を作る。

### 評価指標を増やしすぎる

10個も指標があると「何が改善した/悪化したか」が分からなくなる。
**主要 3〜5 指標に絞る**。

### コスト指標を入れない

精度だけ追うと「とにかく深掘り」になる。**コスト・所要時間も評価軸に**。

### LLM as a judge の精度を過信する

LLM 評価者も間違える。最終的には**人間レビューがゴールデンスタンダード**。
LLM as a judge と人間評価の一致率を時々測定。

---

## ⚠️ judge への入力は「完全」に渡す (実体験ベースの最重要原則)

LLM as a Judge を実装する際の **最大の落とし穴**:

### 失敗パターン (cs_triage_agent v1 evolve で実証)
```python
# ❌ ダメな実装
result["actual_body_excerpt"] = body[:200]  # 200 文字切り詰め!
# (db_results は保存していない)
```
このとき LLM Judge は:
- 「文章が途中で切断されている」と全件で誤判定
- DB の値を見られないので「数値を捏造している」と誤判定
- → overall スコア 2.46 / 5 (実際の品質より大幅に低い評価)

### 正しい実装
```python
# ✅ 正解
result["actual_body"] = body                              # フル本文
result["actual_db_results"] = state.get("db_results", {}) # DB結果も保存
```
→ overall スコア 3.52 / 5 (実際の品質を正確に反映)

### 鉄則
- **本文は切り詰めない**: 200文字制限などしない、judge が文脈を読めるよう全文を渡す
- **コンテキストを欠落させない**: persona / perspective / rules / DB 結果 / カテゴリ等、agent が持っていた情報すべて
- **judge プロンプトの構築時に full context を組み立てる**: `reference/llm_judge_skeleton.py` 参照

---

## LLM as a Judge は破滅的バグ検出にも有効

単純な pass/fail テストでは見つからないバグを LLM Judge が検出した実例:

1. **judge.py の本文切り詰めバグ**: 全件 2.46/5 という異常な低スコアから発覚
2. **agent.py の make_llm 引数バグ**: revise 経路 (Lite モード時のみ発火) で例外
3. **プロンプト変更による品質劣化**: v2 で persona 強化したら -0.18 ポイント低下

→ プロンプト変更時は **必ず judge の次元別スコアで前後比較**する。

---

## 周辺ツール (2026 標準)

| ツール | 役割 | 我々の自作との関係 |
|---|---|---|
| **LangSmith** | LLM 特化 observability (トレース・トークン・プロンプト diff) | 本番運用時に Datadog と併用が標準 |
| **RAGAS** | RAG 評価フレームワーク (faithfulness / answer_relevancy 等) | RAG 系エージェントで自作 judge を補完 |
| **LangFuse** | OSS の LLM 観測性 | LangSmith の代替候補 |
| **Promptfoo** | プロンプト A/B 評価ツール | プロンプト改善時の比較に |

→ 自作 judge.py は十分有効だが、本番で大規模に運用するならこれらの導入を検討。

---

## まとめ

- **少数（十数件）から始める** — 最初から完璧なデータセットは要らない
- **3軸を組み合わせる** — コードベース / LLM as a judge / 人間
- **コストも指標に入れる** — 精度だけ追わない
- **CI に入れて回帰検出** — エージェントの変更で品質低下を即検知
- **リリース後に拡充** — ユーザーフィードバックを機械的に取り込む仕組み
- **judge には完全な情報を渡す** — 切り詰め・欠落は誤評価の元凶
- **LLM Judge をプロンプト変更前後で必ず比較** — 「合格件数」だけ見て成功と判断しない
