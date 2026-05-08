"""既存の result.json を再採点する（API 消費なし）。

run_eval.py の評価ロジックを更新したあとに、過去 run の result.json から
再採点して summary.md を再生成する用途。

使い方:
    python scripts/eval/rescore.py --result-dir <run_dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR.parent))

from eval.run_eval import _aggregate, _score_case, _write_summary_md  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-dir", type=Path, required=True)
    args = ap.parse_args()

    result_path = args.result_dir / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])

    rescored: list = []
    for c in cases:
        meta = c.get("meta", {})
        run = c.get("run", {})
        score = _score_case(meta, run)
        rescored.append({"case_id": c["case_id"], "meta": meta, "run": run, "score": score})

    summary = _aggregate(rescored)
    payload["summary_rescored"] = summary
    payload["cases_rescored"] = rescored
    payload["rescored_at"] = datetime.now().isoformat(timespec="seconds")

    out_json = args.result_dir / "result_rescored.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # rows for summary
    rows = rescored
    _write_summary_md(args.result_dir, summary, rows, payload.get("dry_run", False),
                      args.result_dir / "result_rescored.json")
    # rename to summary_rescored.md
    src = args.result_dir / "summary.md"
    dst = args.result_dir / "summary_rescored.md"
    if src.exists():
        src.rename(dst)

    print(f"[done] rescored: {out_json}")
    print(f"[done] passed_total={summary['passed_total']}/{summary['n_cases']} "
          f"critical_miss={summary['critical_misses']}")


if __name__ == "__main__":
    main()
