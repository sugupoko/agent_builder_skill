#!/usr/bin/env bash
# CS Triage Agent v1 — 動作確認用スクリプト
# 使い方:
#   bash scripts/run.sh                     # dry-run で全 8 サンプルを処理
#   bash scripts/run.sh --real              # 実 LLM で 1 サンプル（sample_01）のみ
#   bash scripts/run.sh --real --all        # 実 LLM で全サンプル

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="config/cs_triage.yaml"
DATASET_DIR="eval/dataset"

MODE="--dry-run"
ALL=true
for arg in "$@"; do
  case "$arg" in
    --real) MODE="" ;;
    --all)  ALL=true ;;
    --one)  ALL=false ;;
  esac
done

if [[ "$ALL" == "true" ]]; then
  for f in "$DATASET_DIR"/sample_*.txt; do
    name=$(basename "$f" .txt)
    echo "=== $name ==="
    python agent.py --config "$CONFIG" --input "$f" --case-id "$name" $MODE || true
    echo
  done
else
  python agent.py --config "$CONFIG" --input "$DATASET_DIR/sample_01_inventory.txt" --case-id sample_01 $MODE
fi
