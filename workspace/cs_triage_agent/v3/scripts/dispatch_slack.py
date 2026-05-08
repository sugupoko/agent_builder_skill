"""Slack 通知（クレーム匂い検出時 + システムアラート）。

deploy フェーズで SE 工藤が実装する想定のスケルトン。
agent.py の assemble ノード後（complaint_smell=True 時）または monitor.py から呼ばれる。

TODO（SE 工藤）:
    - slack_sdk.WebClient で実装
    - レート制限対応（429 で 1 秒待機 + リトライ）
    - 長文は thread に分割
    - bot scope: chat:write のみ（最小権限）
"""
from __future__ import annotations

import json
import os
from typing import Optional

# import slack_sdk  # TODO: 本番で import

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
COMPLAINT_CHANNEL = os.environ.get("SLACK_COMPLAINT_CHANNEL", "#cs-complaint-alerts")
SYSTEM_CHANNEL = os.environ.get("SLACK_SYSTEM_CHANNEL", "#cs-system-alerts")
SF_INSTANCE_URL = os.environ.get("SF_INSTANCE_URL", "https://example.my.salesforce.com")


def _format_complaint_message(final_output: dict) -> dict:
    """クレーム検出時の Slack メッセージ Block 構築。"""
    meta = final_output.get("meta", {}) or {}
    case_id = final_output.get("case_id", "?")
    sf_url = f"{SF_INSTANCE_URL}/lightning/r/Case/{case_id}/view"
    body_preview = (final_output.get("customer_body", "")[:300] or "(本文なし)")
    return {
        "channel": COMPLAINT_CHANNEL,
        "text": f"⚠️ クレーム検出: {case_id}",   # fallback for notification
        "blocks": [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": f"⚠️ クレーム検出: {case_id}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*カテゴリ:* {meta.get('category')}"},
                {"type": "mrkdwn", "text": f"*緊急度:* {meta.get('urgency')}"},
                {"type": "mrkdwn", "text": f"*SKU:* {', '.join(meta.get('skus', []) or [])}"},
                {"type": "mrkdwn", "text": f"*注文番号:* {', '.join(meta.get('order_nos', []) or [])}"},
            ]},
            {"type": "section", "text": {
                "type": "mrkdwn",
                "text": f"*ドラフトプレビュー:*\n```{body_preview}```"
            }},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "SF で開く"},
                 "url": sf_url, "style": "primary"},
            ]},
        ],
    }


def _format_system_alert(severity: str, message: str, context: Optional[dict] = None) -> dict:
    """システムアラート Slack メッセージ。severity: P1/P2/P3/P4"""
    emoji = {"P1": "🚨", "P2": "⚠️", "P3": "ℹ️", "P4": "📊"}.get(severity, "ℹ️")
    return {
        "channel": SYSTEM_CHANNEL,
        "text": f"{emoji} [{severity}] {message[:80]}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": f"{emoji} [{severity}] cs-triage alert"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": f"```{json.dumps(context or {}, ensure_ascii=False, indent=2)[:1500]}```"}},
        ],
    }


class SlackDispatcher:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._client = None
        if not dry_run:
            # TODO: import slack_sdk; self._client = slack_sdk.WebClient(token=SLACK_BOT_TOKEN)
            pass

    def _post(self, payload: dict) -> dict:
        if self.dry_run:
            print(f"[dry-run slack] would post to {payload['channel']}:")
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:500])
            return {"ok": True, "ts": "DRY_RUN_FAKE_TS"}
        # TODO: 実装
        # try:
        #     resp = self._client.chat_postMessage(**payload)
        #     return {"ok": True, "ts": resp["ts"]}
        # except slack_sdk.errors.SlackApiError as e:
        #     return {"ok": False, "reason": str(e)}
        raise NotImplementedError("SE 工藤: slack_sdk.WebClient.chat_postMessage を実装")

    def notify_complaint(self, final_output: dict) -> dict:
        """complaint_smell=True 時に SV へ通知。"""
        if not final_output.get("flags", {}).get("needs_supervisor"):
            return {"ok": True, "skipped": True}
        return self._post(_format_complaint_message(final_output))

    def notify_system_alert(self, severity: str, message: str, context: Optional[dict] = None) -> dict:
        """システム障害・コスト超過・PII 漏れ等のアラート。"""
        return self._post(_format_system_alert(severity, message, context))


def main():
    """CLI: 手動でテスト通知を送る用途。"""
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_complaint = sub.add_parser("complaint")
    p_complaint.add_argument("--final-json", required=True)
    p_alert = sub.add_parser("alert")
    p_alert.add_argument("--severity", choices=["P1", "P2", "P3", "P4"], required=True)
    p_alert.add_argument("--message", required=True)
    ap.add_argument("--real", action="store_true")
    args = ap.parse_args()

    d = SlackDispatcher(dry_run=not args.real)
    if args.cmd == "complaint":
        final = json.loads(open(args.final_json, encoding="utf-8").read())
        print(d.notify_complaint(final))
    else:
        print(d.notify_system_alert(args.severity, args.message))


if __name__ == "__main__":
    main()
