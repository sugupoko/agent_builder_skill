---
name: agent-eval
description: agent-prototype で動くものができた後、評価データセットを使って品質・コスト・所要時間を測定し、改善案を出すスキル。LayerX 流の評価駆動開発（Eval-driven）を実践する。「精度を上げたい」「コストを抑えたい」「品質を客観評価したい」と言われたら呼ぶ。10〜20件の代表的データから始めて、リリース後にユーザーフィードバックで拡充するサイクル。
---

# agent-eval — 評価駆動改善

## いつ使うか

- `/agent-prototype` で動くものができた直後
- 「精度・カバレッジ・コストを客観的に測りたい」とき
- 改善イテレーションを回すたび

## 入力

- `workspace/<project>/vN/scripts/`（動くプロトタイプ）
- `workspace/<project>/vN/eval/dataset/`（10〜20件の評価データ）

## 出力

| ファイル | 内容 |
|---|---|
| `eval/results/run_<timestamp>.json` | 個別実行結果 |
| `eval/results/summary.md` | 評価サマリと前バージョンとの比較 |
| `reports/eval_report.md` | 改善提案・優先度付き |
| `spec.md` | 評価指標の現状値を更新 |

## 進め方

### Step 1: 評価データセットの準備（最初の1回のみ）

`reference/eval_driven_dev.md` に従う。**最初は十数個で始めて OK**。

```
eval/dataset/
├── case_001/
│   ├── input.json or input.md
│   ├── expected.md or expected.json
│   └── metadata.yaml      ← 期待される指標値
├── case_002/
└── ...
```

`metadata.yaml` の例:
```yaml
name: 競合A社の決算記事を含む週
expected:
  must_include_entities: [A社, 決算, $XXX]
  must_not_include_entities: [無関係なゲーム会社]
  min_citations: 5
  min_action_items: 3
```

### Step 2: 評価関数の用意

評価軸3つを組み合わせる:

#### A) コードベース評価（速い・確実）
```python
def eval_codebase(output: str, expected: dict) -> dict:
    score = {}
    # 必須語の含有率
    score["must_include_rate"] = sum(
        1 for k in expected["must_include_entities"] if k in output
    ) / len(expected["must_include_entities"])
    # 出典数
    score["citation_count"] = len(re.findall(r"\[\d+\]", output))
    # アクション項目数
    score["action_count"] = len(re.findall(r"\*\*(今週中|来週まで|来月まで)\*\*", output))
    return score
```

#### B) LLM as a judge（柔軟、強く推奨）
別の Claude に「この出力は editor.persona の視点から見て妥当か」を評価させる。
**雛形: `reference/llm_judge_skeleton.py` をコピーして使う**。

#### B-1) 5 次元採点が標準
- persona_fit / tone_appropriate / info_completeness / numerical_accuracy / ng_phrase_avoidance
- 1-5 で採点 (3 を平均)、judge model は **Sonnet 推奨** (Haiku は判定が甘い)

#### B-2) judge への入力は「完全」が原則
**実プロジェクト (cs_triage_agent) で発見した教訓**:
- 評価対象の本文を 200 文字に切り詰めて保存していた → judge が「文章途中で切断」と誤判定し全件低評価
- DB ルックアップ結果を渡していなかった → 「DBなしで捏造」と誤判定
- これらを修正したら overall スコアが 2.46 → 3.52 に大幅改善

→ **judge には full body + 全コンテキスト (DB結果・ペルソナ・ルール) を渡す**。
→ `result_*.json` には `actual_body` (フル) と `actual_db_results` を保存する設計に。

#### B-3) LLM Judge は破滅的バグ検出にも有効
LLM Judge が極端に低スコア出力をしたら、それは agent のバグサイン:
- judge.py 自体のバグ (本文切り詰め等) を検出した実例あり
- agent.py の make_llm 引数バグ (revise 経路でのみ発火) を検出
- 単純 pass/fail を超えた品質判定が可能

#### C) 人間レビュー（最終的な真実）
LLM as a judge の結果を業務担当者が抜き取りでスポットチェック。

### Step 3: 評価を回す

```bash
python scripts/eval_run.py --version v1 --dataset eval/dataset/
```

各ケースで:
- エージェントを実行
- 出力を保存
- 3軸の評価を計算
- コスト・所要時間を記録

### Step 4: 集計

`eval/results/summary.md`:
```markdown
# v1 評価サマリ

## 全体メトリクス
- 必須語含有率: 平均 78% (目標 80%)
- 出典数: 平均 5.4 (目標 3+)
- アクション項目数: 平均 4.1 (目標 3+)
- コスト: 平均 $0.34 / 件 (目標 $0.50以下) ✓
- 所要時間: 平均 2:45 / 件 (目標 5分以内) ✓

## ケース別

| ケース | 必須語 | 出典 | アクション | コスト | 合否 |
|---|---|---|---|---|---|
| case_001 | 90% | 7 | 5 | $0.32 | ✓ |
| case_002 | 65% | 4 | 3 | $0.30 | ✗ |
| ... | | | | | |

## 失敗パターン分析
- case_002: 必須語の「○○」が拾えなかった → キーワード設計の問題
- case_005: 出典が3件しか付かなかった → プロンプトでの引用制約強化必要
```

### Step 5: 改善提案

`reports/eval_report.md` に優先度付きで:

```markdown
# 改善提案 v1 → v2

## 優先度 高
1. キーワード「○○」の追加（case_002 の解決）
2. プロンプトに「出典は最低5件」と明記（case_005 の解決）

## 優先度 中
3. 編集者ペルソナの perspective に「△△」を追記
4. blocklist に「□□」を追加

## 優先度 低
5. リランキングのスコア重みを調整
```

### Step 6: spec.md を更新

評価指標の「現状値」を埋め、改善目標との差分を明示。

## 重要な原則

### 大規模データセットは不要

LayerX も「代表的な十数個」と言っている。**まず評価サイクルを回すこと**が最優先。
データセットはリリース後にユーザーフィードバックで拡充。

### 3軸の組み合わせ

| 軸 | 速度 | 信頼性 | 用途 |
|---|---|---|---|
| コードベース | ◎ | ○ | 必須要件のチェック（数値・固有名詞・形式） |
| LLM as a judge | ○ | △ | 文体・視点・読みやすさ |
| 人間レビュー | × | ◎ | 最終判定・抜き取り |

最初は LLM as a judge を入れず、コードベース + 人間レビューだけで始めても OK。

### 評価データはバージョン管理

`eval/dataset/` を Git に入れて、エージェントの変更で過去ケースが回帰していないか確認できるようにする（モデル名・コミットハッシュも結果に記録）。

### コストも指標

精度だけ見ると「とにかく深掘り」「とにかく多段検索」になりがち。
**コスト・所要時間も評価軸に入れる**ことで現実的なバランスが取れる。

## 次のステップ

評価が安定したら:

```
/agent-deploy
```

運用設計（cron / 配信先 / 監視 / コスト上限）に進む。
評価指標を CI に組み込んで回帰検出にも使える。
