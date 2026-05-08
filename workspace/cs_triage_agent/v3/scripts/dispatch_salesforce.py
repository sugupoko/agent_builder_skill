"""Salesforce AI_Draft__c カスタムオブジェクトへの書き込み。

deploy フェーズで SE 工藤が実装する想定のスケルトン。
agent.py の assemble ノード後に呼ばれる前提。

TODO（SE 工藤）:
    - simple-salesforce 等の SDK で OAuth クライアントを実装
    - retries=3, exponential backoff 2/4/8s
    - レコード重複防止: case_id__c でユニーク制約
    - 失敗時はキューに退避（Redis）+ Slack #cs-system-alerts へ通知
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# from simple_salesforce import Salesforce  # TODO: 本番では実装

# Vault または K8s Secret から注入される想定
SF_USERNAME = os.environ.get("SF_USERNAME", "")
SF_PASSWORD = os.environ.get("SF_PASSWORD", "")
SF_TOKEN = os.environ.get("SF_TOKEN", "")
SF_OAUTH_CLIENT_ID = os.environ.get("SF_OAUTH_CLIENT_ID", "")
SF_OAUTH_CLIENT_SECRET = os.environ.get("SF_OAUTH_CLIENT_SECRET", "")
SF_INSTANCE_URL = os.environ.get("SF_INSTANCE_URL", "")
SF_API_VERSION = os.environ.get("SF_API_VERSION", "v60.0")


class SalesforceDispatcher:
    """AI_Draft__c カスタムオブジェクトへの書き込み。"""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._client = None  # TODO: SF クライアント初期化

    def _connect(self):
        """OAuth で SF に接続。"""
        if self.dry_run:
            return None
        # TODO: 実装
        # self._client = Salesforce(
        #     username=SF_USERNAME, password=SF_PASSWORD, security_token=SF_TOKEN,
        #     instance_url=SF_INSTANCE_URL, version=SF_API_VERSION
        # )
        raise NotImplementedError("SE 工藤: simple-salesforce で OAuth 接続を実装")

    def write_draft(self, final_output: dict) -> dict:
        """AI_Draft__c レコードを作成（または更新）する。

        Args:
            final_output: agent の assemble ノードの出力 JSON
        Returns:
            {"ok": True, "sf_id": "<AI_Draft__c の Id>"} または失敗時は {"ok": False, "reason": "..."}
        """
        meta = final_output.get("meta", {}) or {}
        flags = final_output.get("flags", {}) or {}
        record = {
            "Case_Id__c": final_output.get("case_id"),
            "Customer_Body__c": final_output.get("customer_body", ""),
            "Internal_Memo__c": final_output.get("internal_memo", ""),
            "Missing_Info_Json__c": json.dumps(
                final_output.get("missing_info", []) or [], ensure_ascii=False
            ),
            "Meta_Json__c": json.dumps(meta, ensure_ascii=False),
            "Category__c": meta.get("category", "other"),
            "Urgency__c": meta.get("urgency", "normal"),
            "Complaint_Smell__c": bool(meta.get("complaint_smell", False)),
            "Cost_Usd__c": float(meta.get("cost_usd", 0.0)),
            "Used_Models__c": ",".join(meta.get("used_models", []) or []),
            "Ai_Generated_At__c": meta.get("ai_generated_at"),
            "Flags_Json__c": json.dumps(flags, ensure_ascii=False),
        }

        if self.dry_run:
            print("[dry-run] would write to Salesforce:")
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return {"ok": True, "sf_id": "DRY_RUN_FAKE_ID"}

        # TODO: 実装
        for attempt in range(3):
            try:
                if self._client is None:
                    self._connect()
                # res = self._client.AI_Draft__c.create(record)
                # return {"ok": True, "sf_id": res["id"]}
                raise NotImplementedError("SE 工藤: client.AI_Draft__c.create を実装")
            except Exception as e:
                if attempt == 2:
                    return {"ok": False, "reason": f"sf_write_failed: {e}"}
                time.sleep(2 ** attempt * 2)


def main():
    """CLI: agent の final_output JSON を読んで SF に書く（テスト・手動再実行用）。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-json", required=True, type=Path,
                    help="agent.py が output/ に書いた final_<case_id>.json")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="既定で dry-run（実 SF 書き込みなし）")
    ap.add_argument("--real", action="store_true",
                    help="実 SF 書き込み（要 ANTHROPIC キー以外の SF キー）")
    args = ap.parse_args()

    final = json.loads(args.final_json.read_text(encoding="utf-8"))
    dispatcher = SalesforceDispatcher(dry_run=not args.real)
    result = dispatcher.write_draft(final)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
