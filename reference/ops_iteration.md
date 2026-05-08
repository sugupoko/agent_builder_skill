# Ops 段階での継続改善サイクル

エージェントを本番運用に乗せた後の「継続的な性能改善」をどう回すか。

> 一言で: **バージョン駆動 + CHANGELOG + 回帰評価 + 本番ログからのケース昇格**

---

## 1. バージョン駆動の改善（最重要）

`workspace/<project>/` は **vN/ ディレクトリで完全分離**する。

```
my_project/
├── v1/
│   ├── spec.md            ← v1 の正
│   ├── design.md
│   ├── scripts/
│   │   └── agent.py
│   ├── config.yaml
│   ├── eval/
│   │   ├── dataset/
│   │   └── results/v1_*.json
│   └── reports/
├── v2/
│   ├── spec.md            ← v2 の正（v1 からコピーして変更）
│   ├── design.md
│   ├── scripts/agent.py   ← v1 から派生したコード
│   ├── eval/
│   │   ├── dataset/       ← v1 と同じ + 新規ケース
│   │   └── results/
│   │       ├── v1_baseline.json   ← v1 を再実行した結果
│   │       └── v2_changes.json    ← v2 を実行した結果
│   └── reports/
└── CHANGELOG.md           ← プロジェクト全体の変更記録
```

### なぜ分けるか

- **v1 と v2 を同時に動かせる** → A/B 比較
- v2 で問題が出ても v1 に即戻せる
- 各バージョンが「自己完結」しているので新メンバーが理解しやすい
- 監査要件（「2026年4月時点ではどう動いていたか」）に対応できる

### バージョンを切る判断基準

| 変更の種類 | 新バージョンを切る？ |
|---|---|
| YAML のキーワード追加・blocklist 修正 | ❌ vN 内で更新（軽微な調整） |
| プロンプトの微調整（数語程度） | ❌ vN 内で更新 |
| プロンプトの構造変更（新セクション追加等） | ✅ vN+1 |
| ノード追加・削除 | ✅ vN+1 |
| 設計パターン変更（Reflection 追加等） | ✅ vN+1 |
| モデル変更（Sonnet→Haiku 等） | ✅ vN+1 |
| YAML スキーマ変更 | ✅ vN+1 |

---

## 2. CHANGELOG.md（修正記録）

プロジェクトルートに置き、**変更の動機 → 内容 → 結果** を記録する。

```markdown
# CHANGELOG

## v2 (2026-06-15)

### 変更の動機
- v1 評価で「曖昧表現の検出漏れ」が10件中3件。検出率 70% で目標 90% に未達
- 業務担当（鈴木）から「『ご相談』『柔軟に』が見逃されている」とフィードバック

### 変更内容
- `review` ノードのプロンプトに「曖昧表現リスト（具体的に挙げる）」を追加
- 検出する曖昧表現を YAML の `forbidden_phrases` で定義可能に
- ノード追加: `forbidden_check` （コードベースで `forbidden_phrases` を機械チェック）

### 変更前後の評価結果（v1 vs v2）

| 指標 | v1 | v2 | 目標 | 判定 |
|---|---|---|---|---|
| 必須セクション網羅率 | 100% | 100% | 100% | OK |
| 不足情報指摘数 | 平均 5.2 | 平均 6.8 | 3-7 | OK |
| 曖昧表現検出率 | 70% | 95% | 90% | ✅ 改善 |
| 1件あたりコスト | $0.087 | $0.092 | <$1.0 | OK |
| 既存ケースの回帰 | - | 0件 | 0件 | OK |

### コスト影響
- $0.087 → $0.092（+5%）
- 月間 $0.15 増加（30件想定）
- 検収トラブル削減効果と比較して許容範囲

### 関連
- 業務担当との議論メモ: docs/discussion_2026-06-10.md
- v1 評価レポート: v1/reports/eval_report.md

---

## v1 (2026-05-06)

### 変更の動機
- 初版。営業からの自由記述依頼を構造化仕様書に変換するエージェントを構築

### 変更内容（初版なので「実装内容」）
- 7ノード構成: intake → classify → draft → review → gaps → checklist → compose_save
- LLM 使い分け: Sonnet（draft）+ Haiku（その他）
- editorial.persona 注入（PMO 田中の判断スキル）

### 評価結果
（10件の評価データセットでの初回測定）

| 指標 | 値 | 目標 |
|---|---|---|
| 必須セクション網羅率 | 100% | 100% |
| 不足情報指摘数 | 平均 5.2 | 3-7 |
| 1件あたりコスト | $0.087 | <$1.0 |

### 既知の課題（v2 で対応予定）
- 曖昧表現の検出率が低い（70%）
- 過去仕様書 DB との連携なし
```

---

## 3. 回帰評価（Regression Eval）

vN+1 を作ったら、必ず **vN と同じデータセットで両方を実行**して結果を比較する。

### スクリプト例

```python
# scripts/eval_compare.py
import json
from pathlib import Path

def load_results(version):
    return json.loads((Path(f"v{version}/eval/results/summary.json")).read_text())

def compare(v_old, v_new):
    old = load_results(v_old)
    new = load_results(v_new)
    diff = {}
    for key in ["must_include_rate", "citation_count", "cost_usd"]:
        diff[key] = {
            "old": old["summary"][f"avg_{key}"],
            "new": new["summary"][f"avg_{key}"],
            "delta": new["summary"][f"avg_{key}"] - old["summary"][f"avg_{key}"],
        }
    return diff

if __name__ == "__main__":
    diff = compare("1", "2")
    print(json.dumps(diff, indent=2, ensure_ascii=False))
    
    # 退化検出
    if diff["must_include_rate"]["delta"] < -0.05:
        print("⚠️ 必須語含有率が 5% 以上低下")
        exit(1)
```

### CI に組み込む

GitHub Actions で `scripts/eval_compare.py` を走らせて、しきい値割れで PR をブロックする。
退化があった変更は merge できない。

---

## 4. 本番ログからの新ケース発掘

実運用ログから「うまくいかなかったケース」を**月次で評価データセットに昇格**する。

### 実装パターン

```python
# scripts/promote_logs_to_dataset.py
"""本番ログから問題があったケースを抽出して eval/dataset/ に追加する。"""

import json
from pathlib import Path

def find_problem_cases(logs_dir: Path):
    """ユーザーが手動修正したケース、エラーが出たケースを抽出。"""
    problems = []
    for log in logs_dir.glob("*.log"):
        # メタデータから判定（要は実行時に問題ありフラグを記録しておく）
        meta_path = log.parent / log.name.replace(".log", ".meta.json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("manual_correction") or meta.get("review_score", 5) < 3:
            problems.append(meta)
    return problems

def promote(case_id, meta):
    """ケースを eval/dataset/ に昇格させる。"""
    target = Path(f"eval/dataset/case_{case_id}/")
    target.mkdir(parents=True, exist_ok=True)
    # 入力データをコピー（個人情報はマスク）
    masked_input = mask_pii(meta["input"])
    (target / "input.md").write_text(masked_input)
    (target / "metadata.yaml").write_text(yaml.dump({
        "name": f"本番昇格: {meta['summary']}",
        "promoted_from_log": meta["log_id"],
        "expected": {
            "min_citations": ...,
            ...
        }
    }))
```

### 個人情報マスキング

```python
import re
PII_PATTERNS = [
    (r"\b[\w.]+@[\w.]+\.\w+\b", "<EMAIL>"),
    (r"\b\d{2,4}-\d{2,4}-\d{4}\b", "<PHONE>"),
    (r"〒?\d{3}-\d{4}", "<POSTAL>"),
    # 業務固有の固有名詞も追加
]
def mask_pii(text):
    for pat, replacement in PII_PATTERNS:
        text = re.sub(pat, replacement, text)
    return text
```

### 月次運用

```
月初: scripts/promote_logs_to_dataset.py 実行
  ↓
新規ケース 5件追加
  ↓
全ケースで現バージョンを再評価
  ↓
退化があれば対応、なければ次のサイクルへ
```

---

## 5. コスト・品質の月次トレンド

`data/ops_dashboard.csv` に月次集計を蓄積:

```csv
month,version,n_runs,avg_cost_usd,total_cost_usd,must_include_rate,manual_correction_rate
2026-05,v1,28,0.089,2.49,0.78,0.21
2026-06,v2,32,0.092,2.94,0.94,0.07
```

トレンドの読み方:
- `manual_correction_rate` の増加 → エージェントの精度低下、要調査
- `avg_cost_usd` の急増 → ループ暴走疑い、recursion_limit 確認
- `must_include_rate` の低下 → ソース変化（フォーマット変更等）の可能性

可視化（matplotlib / Looker / Streamlit）で見える化すると、**「いつから悪化したか」** が一発で分かる。

---

## 6. 改善ループの全体像

```
[本番運用]
    ↓
本番ログ蓄積 + 業務担当のフィードバック収集
    ↓
[月次レビュー]
    1. ログから問題ケースを抽出
    2. eval/dataset/ に新規ケース追加（個人情報マスク）
    3. 評価データセットで再測定
    4. 退化や品質低下を確認
    ↓
[改善設計] ← /agent-eval スキル
    1. 失敗パターン分析
    2. 改善案を優先度付きで列挙
    ↓
[v(N+1) の実装]
    1. workspace/<project>/v1/ → v2/ にコピー
    2. CHANGELOG.md に「変更の動機」を書く
    3. 変更を適用
    4. 同じデータセットで評価（v1 vs v2）
    5. CHANGELOG.md に「変更前後の評価結果」を書く
    6. 回帰チェック (eval_compare.py)
    ↓
[デプロイ判断]
    - 退化がない → v2 を本番に
    - 退化あり → 戻す or 再修正
    ↓
[本番運用に戻る]
```

---

## 7. CLAUDE.md / spec.md とどう連携するか

- `CLAUDE.md` (プロジェクトルート): バージョン運用ルール・回帰評価ルールを記載
- `vN/spec.md`: そのバージョンの「正」。**vN+1 を切るときは spec.md の差分が CHANGELOG.md の根拠になる**
- `CHANGELOG.md` (プロジェクトルート): バージョン間の変更履歴・評価結果

各 vN/ の `reports/` に:
- `eval_report.md`: そのバージョンの評価結果サマリ
- `improve_report.md`: 次バージョンへの改善提案
- `deploy_report.md`: 運用設計書

---

## 8. アンチパターン

### ❌ 全部 v1 のまま改善
- spec.md が古いまま、コードだけ修正される
- 「いつ何が変わったか」が追えない
- ロールバック不能

### ❌ CHANGELOG なしの口頭伝承
- 「なぜそう変えたか」が忘れ去られる
- 同じ間違いを繰り返す
- 新メンバーが理解できない

### ❌ 退化を確認せずデプロイ
- v2 で改善した一方、別の指標が悪化
- 数週間後に発覚して大騒ぎ

### ❌ 評価データセットの放置
- リリース時の10件のまま、半年たっても10件
- 業界の変化に追従できない

---

## 9. ツール推奨

| 機能 | ツール |
|---|---|
| バージョン管理 | Git（vN ブランチ or vN タグ） |
| 評価ダッシュボード | Streamlit / Looker / Metabase |
| 月次トレンド可視化 | matplotlib + cron |
| 個人情報マスキング | Presidio (Microsoft) / 自前正規表現 |
| 本番ログ集約 | CloudWatch / Datadog / Sentry |

---

## Post-cutover の継続レビュー (2026 年標準)

シャドーモード卒業 → 本番投入後も、**ある割合で人間レビューを継続する**のが業界標準。

### サンプリング方針 (推奨)

| カテゴリ | サンプル率 | 用途 |
|---|---|---|
| 通常ケース | 5-10% ランダム | drift 検知、品質低下の早期発見 |
| 高リスクケース (例: クレーム匂い検知) | **100%** | 見逃しコスト最大化 |
| 新カテゴリ・新ルール導入直後 | 100% (1 ヶ月) → 段階低減 | 新機能の品質確認 |
| 月予算 80% 超過時 | 100% | コスト最適化のヒント収集 |

### Drift 検知メトリクス (本番ログから月次集計)

```python
# 月次バッチで以下を計算
metrics = {
    "classify_confidence_p50": ...,  # 中央値が 0.7 を割ったら品質劣化サイン
    "self_check_failure_rate": ...,  # 5% 超えたら設定見直し
    "draft_confidence_low_rate": ...,  # 10% 超えたらモデル変更検討
    "operator_acceptance_rate": ...,  # 70% 割ったら原因究明
    "fallback_rate": ...,  # DB / LLM 障害頻度
}
```

drift サインを発見したら新ケースを `eval/dataset/` に昇格して再評価。

### Post-cutover レビューを「サボらない」工夫

1. **Slack ボット化**: 毎週月曜にランダム 5 件の case_id を SV に Slack で送る
2. **記録を集計可能に**: SV の評価 (5段階) を CRM のカスタムフィールドに保存
3. **月次レビュー会議の固定アジェンダに組み込む**: 「先月のドラフト評価 5段階分布」
4. **退化が見つかった時の再評価フロー**: 該当 case を `eval/dataset/` に追加 → 既存版 vs 新版で再 judge

→ 詳細実装例: `workspace/cs_triage_agent/v1/reports/deploy_design.md` §11

---

## まとめ

- **vN/ ディレクトリで完全分離** — 改善のたびに新バージョン、ロールバック可能
- **CHANGELOG.md に動機・内容・結果** — 後で「なぜそうなったか」を追える
- **回帰評価を CI に組み込む** — 退化があれば自動検出
- **本番ログから新ケース昇格** — 月次でデータセット拡充
- **コスト・品質の月次トレンド** — 異常を早期発見
- **Post-cutover の継続レビュー** — 5-10% サンプル + 高リスク全件、drift 検知
