"""FastAPI webhook-receiver: Salesforce ケース新規作成イベントを受け取り、
agent-worker に非同期でジョブを投入する。

deploy フェーズで SE 工藤が K8s にデプロイする想定のスケルトン。
HMAC 認証 + idempotency + 即時 200 応答（SF 側のタイムアウト回避）が要件。

TODO（SE 工藤）:
    - Redis に enqueue（agent-worker が consume）
    - prometheus_client / statsd で metrics publish
    - HMAC 検証（SF Outbound Message のシークレット）
    - リトライ・dead letter queue 設計
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# from fastapi import FastAPI, Request, Header, HTTPException
# from redis import Redis
# import uvicorn

SF_WEBHOOK_HMAC_SECRET = os.environ.get("SF_WEBHOOK_HMAC_SECRET", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
QUEUE_NAME = os.environ.get("QUEUE_NAME", "cs_triage_jobs")

# Stub for skeleton: will be replaced with actual FastAPI app
# app = FastAPI(title="cs-triage webhook-receiver")
# redis = Redis.from_url(REDIS_URL)


def verify_hmac(body: bytes, signature: str) -> bool:
    """SF Outbound Message の HMAC-SHA256 検証。"""
    if not SF_WEBHOOK_HMAC_SECRET:
        return False
    expected = hmac.new(
        SF_WEBHOOK_HMAC_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# TODO: SE 工藤、以下を実装
"""
@app.post("/webhook/sf-case")
async def receive(req: Request, x_sf_signature: str = Header(None)):
    body = await req.body()
    if not verify_hmac(body, x_sf_signature or ""):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = json.loads(body)
    case_id = payload["case_id"]
    raw_text = payload["raw_text"]
    customer_id = payload.get("customer_id", "")

    # idempotency: case_id をキーに deduplication
    if redis.exists(f"job:done:{case_id}"):
        return {"ok": True, "status": "already_processed"}

    job = {
        "case_id": case_id,
        "raw_text": raw_text,
        "customer_id": customer_id,
        "received_at": datetime.utcnow().isoformat(),
    }
    redis.lpush(QUEUE_NAME, json.dumps(job))
    return {"ok": True, "status": "queued"}


@app.get("/healthz")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""


def main():
    """CLI: HMAC 検証だけのスタンドアロン実行（テスト用途）。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", required=True, type=Path)
    ap.add_argument("--signature", required=True)
    args = ap.parse_args()
    body = args.payload.read_bytes()
    if verify_hmac(body, args.signature):
        print("HMAC OK")
        sys.exit(0)
    else:
        print("HMAC NG")
        sys.exit(1)


if __name__ == "__main__":
    main()
