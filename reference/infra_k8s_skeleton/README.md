# K8s インフラ雛形

> AI エージェントを Kubernetes に乗せる際の雛形。プロジェクト固有部分 (`<placeholder>`) を埋めて使う。
>
> 実例: `workspace/cs_triage_agent/v1/scripts/infra/`

## 構成

```
infra_k8s_skeleton/
├── k8s/
│   ├── deployment.yaml         # agent-worker (HPA 含む)
│   ├── webhook-receiver.yaml   # FastAPI で trigger 受信
│   ├── redis.yaml              # queue + cache
│   ├── cronjob-health-check.yaml  # 5 分毎ヘルスチェック
│   └── secrets.example.yaml    # シークレット雛形 (実際は Vault)
├── docker/
│   ├── Dockerfile              # production image
│   └── docker-compose.yml      # ローカル動作確認用
└── cron/
    └── health_check.sh         # ヘルスチェックスクリプト
```

## 使い方

1. このディレクトリをプロジェクトの `scripts/infra/` にコピー
2. プレースホルダを置換:
   - `<namespace>` → 実 namespace
   - `<image_registry>` → 実 image registry
   - `<agent_name>` → エージェント名
   - `<port>` → 公開ポート (デフォルト 8000)
3. `secrets.example.yaml` を `secrets.yaml` にコピーして編集 (Git にはコミットしない)
4. `kubectl apply -f k8s/`

## 構成の根拠

- **agent-worker は 3〜5 で HPA**: queue depth 50 超でスケールアウト
- **webhook-receiver は HA で 2 台**: 即時応答 (200ms 以内) を保証
- **Redis は単一インスタンス + PVC**: 初期は十分、スケール時に Sentinel/Cluster へ
- **CronJob ヘルスチェック 5 分毎**: liveness probe を補完、外形監視

詳細設計: `reference/long_running_pattern.md` (長時間タスクの場合は Temporal 検討)
