#!/usr/bin/env bash
# Expense Review Agent v1 — 動作確認用
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="config/expense_policy.yaml"
DATASET_DIR="eval/dataset"

MODE="--dry-run"
for arg in "$@"; do
  case "$arg" in
    --real) MODE="" ;;
  esac
done

for d in "$DATASET_DIR"/case_*/; do
  name=$(basename "$d")
  echo "=== $name ==="
  python agent.py --config "$CONFIG" --input "$d/input.json" --case-id "$name" $MODE || true
  echo
done
