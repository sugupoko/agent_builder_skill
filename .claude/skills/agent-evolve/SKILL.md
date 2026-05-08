---
name: agent-evolve
description: 動いているエージェントの構成を探索的に改善する高度スキル。マルチエージェント化（Planner+Reviewer+Synthesizer）、モデル使い分け（Sonnet/Haiku）、プロンプトの自動改善、パラメータ探索などを試みる。Sakana AI の ShinkaEvolve（進化的アルゴリズムでエージェント設計を発見）の思想を取り入れる。「もう一段、品質を上げたい」「コストを大幅削減したい」と言われたとき呼ぶ。オプション・後回しで OK。
---

# agent-evolve — 探索的改善（オプション）

## いつ使うか

- `/agent-eval` で安定運用できているが、**さらに品質を上げたい・コストを削りたい**とき
- Multi-Agent 化や複雑な設計に踏み込む価値があるか検証したい
- 構成の「決め打ち」から脱却し、**探索的に最適解を見つけたい**

## いつ呼ばないか

- まだ単純な ReAct / 単純な LLM 呼び出しで動いていないとき
- 評価データセットが不十分なとき（探索の評価ができない）

---

## 進め方

### Approach 1: マルチエージェント化（Planner + Worker + Reviewer）

参考: Sakana AI ShinkaEvolve が発見した **「diverse expert personas + critical peer review + final synthesis」** の3段構成。

```python
# reference/multi_agent_skeleton.py 参照
def multi_agent_pipeline(state):
    # 1. Planner: タスクを分解
    plan = planner_agent(state["task"])
    
    # 2. Workers: 各サブタスクを並列実行（異なるペルソナ）
    drafts = [worker_agent(subtask, persona=p) 
              for subtask, p in plan]
    
    # 3. Reviewer: 各案をレビュー、改善指示
    critiques = reviewer_agent(drafts)
    
    # 4. Synthesizer: 最終統合
    final = synthesizer_agent(drafts, critiques)
    return final
```

向く場面:
- 単一視点では捉えきれない複雑な判断（例: 規制動向 + 市場動向 + 技術動向の統合）
- 各サブタスクに異なる専門性が必要

注意:
- コストは2〜5倍に膨らむ
- デバッグが難しくなる
- まず **3エージェント構成**から始める。それ以上は調整困難

---

### Approach 2: モデル使い分け（Sonnet × Haiku のハイブリッド）

すべてのノードで Sonnet/Opus は不要。役割で使い分け:

| ノード | モデル | 理由 |
|---|---|---|
| 要約・編集（出力に影響） | Claude Sonnet 4.6 | 文章品質が直接成果に影響 |
| 分類・選別 | Claude Haiku 4.5 | 安く速い |
| LLM as a judge | Claude Haiku 4.5 | 大量に走らせるため |
| Tool 引数の整形 | Claude Haiku 4.5 | 軽量タスク |
| Reflection の自己批評 | Claude Haiku 4.5 | 二回目以降の細部チェック |

```python
SUMMARIZE_MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = "claude-haiku-4-5-20251001"

summarizer = ChatAnthropic(model=SUMMARIZE_MODEL)
judge = ChatAnthropic(model=JUDGE_MODEL)
```

実測で 30〜60% コスト削減できることが多い。

---

### Approach 3: プロンプトの自動改善ループ

評価結果から「失敗パターン」を抽出し、プロンプトに追記する自動ループ:

```python
def auto_improve_prompt(current_prompt, failed_cases):
    """失敗ケースを LLM に分析させて、プロンプトに追記すべき制約を提案させる。"""
    analyzer = ChatAnthropic(model="claude-sonnet-4-6")
    analysis_prompt = f"""
現在のプロンプト:
{current_prompt}

失敗したケース:
{json.dumps(failed_cases, ensure_ascii=False)}

これらの失敗を防ぐため、現在のプロンプトに追記すべき指示を3つ提案してください。
"""
    response = analyzer.invoke([HumanMessage(content=analysis_prompt)])
    return current_prompt + "\n\n追加の指示:\n" + response.content
```

LayerX の評価駆動開発と組み合わせると強力。
ただし「LLM が LLM のプロンプトを書く」ループは過度に複雑になりがちなので、**最終的に人間がレビュー**する。

---

### Approach 4: パラメータ探索（temperature / max_tokens / recursion_limit）

評価データセットでパラメータをグリッドサーチ:

```python
import itertools

PARAM_GRID = {
    "model": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "temperature": [0, 0.5, 1.0],
    "max_tokens": [2000, 4000],
    "recursion_limit": [25, 40],
    "n_supplement_calls": [0, 1, 3],
}

best = None
for combo in itertools.product(*PARAM_GRID.values()):
    params = dict(zip(PARAM_GRID.keys(), combo))
    score = run_eval_with_params(params)
    if best is None or score > best.score:
        best = (params, score)
```

組合せ数が多いと評価コストが膨らむので、**2〜3パラメータに絞る**。

---

### Approach 4.5: DSPy によるプロンプト自動最適化 (2026 年標準)

**Stanford 発の DSPy** は、手書きプロンプトを programmatic に最適化するフレームワーク。

```python
# 概念例 (実 API は dspy ライブラリに依存)
import dspy

class TaskSignature(dspy.Signature):
    """タスクを定義 (typed input/output)"""
    input_text: str = dspy.InputField()
    context: dict = dspy.InputField()
    output: str = dspy.OutputField(desc="出力の条件を書く")

agent = dspy.ChainOfThought(TaskSignature)

# compiler が評価データから最適 prompt を発見
optimizer = dspy.MIPROv2(metric=judge_score_fn)
compiled_agent = optimizer.compile(agent, trainset=eval_dataset)
```

向く場面:
- **プロンプト改善で品質を上げたいが、人手でルール追加すると逆効果になる場合**
- 評価データセット (10件以上) があり、目的関数 (judge スコア等) が定義できる
- typed signature で「入力・出力・条件」を明示できる構造化タスク

#### なぜ重要か (実体験ベース)

人手でプロンプトを iterate すると **「プロンプト肥大化の罠」** に陥りがち:
- ルール追加で品質向上を狙う → 逆に劣化
- 詳細事例: `AGENT_MINDSET.md` §「プロンプト改善の罠」

DSPy は評価で **勾配的に良い方向だけ採用**するのでこの罠を構造的に回避できる。

#### 落とし穴
- compile に時間とコストがかかる (評価×探索、最低 $10 規模)
- 最適化された prompt が人間に読めなくなることがある
- typed signature の設計が下手だと最適化効果が出ない

#### 標準的な組み合わせ
- **LangGraph で graph 構造 + DSPy で各ノードの prompt 中身を最適化**
- これが 2026 年の本番 LLM システムの主流パターン (`reference/design_patterns.md` 参照)

---

### Approach 5: 進化的探索（ShinkaEvolve 風）

Sakana AI の手法を簡易再現。パラメータやプロンプトを「個体」として扱い、評価スコアで選別、変異を加える:

```python
def evolutionary_search(initial_pop, evaluate_fn, generations=10):
    pop = initial_pop  # [{"prompt": str, "params": dict}, ...]
    for gen in range(generations):
        scores = [evaluate_fn(ind) for ind in pop]
        # 上位を残して、変異を加えて次世代
        top = sorted(zip(pop, scores), key=lambda x: -x[1])[:len(pop) // 2]
        next_pop = [t[0] for t in top]
        for parent, _ in top:
            child = mutate(parent)  # プロンプト変異・パラメータ調整
            next_pop.append(child)
        pop = next_pop
    return max(pop, key=evaluate_fn)
```

### 注意

これらは**実験的アプローチ**であり、安易に使うと:
- コストが急増（評価×組合せ）
- デバッグが難しくなる
- 業務担当者が触れなくなる

「**動くものができてから**」「**評価サイクルが回っているから**」適用するのが鉄則。

---

## 出力

| ファイル | 内容 |
|---|---|
| `reports/evolve_report.md` | 探索結果と推奨構成 |
| `experiments/` | 試行結果（パラメータごとのスコア） |
| `spec.md` | 採用案を反映 |

---

## evolve_report.md の例

```markdown
# Evolution Report v1 → v2

## 探索したアプローチ
1. Multi-Agent 化（Planner + Worker + Reviewer）
2. モデル使い分け（要約は Sonnet、選別は Haiku）
3. プロンプトの自動改善

## 結果（評価スコア × コスト）

| 構成 | 必須語含有率 | コスト | 所要時間 | 採用 |
|---|---|---|---|---|
| baseline (v1) | 78% | $0.34 | 2:45 | - |
| Multi-Agent | 85% | $0.95 | 5:30 | ✗ コスト高 |
| Model mix | 80% | $0.18 | 2:30 | ✓ コスト半減 |
| 自動プロンプト | 84% | $0.36 | 3:00 | ✓ 品質改善 |

## 採用構成（v2）
- Model mix: 要約=Sonnet、判定=Haiku
- 自動プロンプト改善: 月1で実行

## 棄却した構成
- Multi-Agent: 価値はあるがコスト2.8倍。本ユースケースではオーバースペック
```

---

## ShinkaEvolve / Sakana AI から学ぶ

ShinkaEvolve は **75世代でマルチエージェントscaffoldを発見**したという驚異的な事例。
本パックではそこまで自動化しないが、**「設計を決め打ちせず、評価で選ぶ」** という発想は取り入れる。

参考: https://sakana.ai/shinka-evolve/

---

## まとめ

- **オプション扱い** — 動くものができて、評価サイクルが回ってからやる
- **5つのアプローチ**: Multi-Agent / モデル使い分け / 自動プロンプト改善 / パラメータ探索 / 進化的探索
- **モデル使い分けは効果が大きい**: 30〜60% コスト削減のことも
- **コストと品質のトレードオフ**を必ず可視化
- **業務担当者が触れる粒度**を守る（複雑にしすぎない）

---

## 次のステップ

採用案で動かしてさらにイテレーション:

```
/agent-eval     ← 採用案で評価し直し
/agent-deploy   ← 運用に反映
```

または、別のユースケースに `/agent-discover` から着手。
