"""ノード 4: retrieve — DB 引き当て（疑似 Tool Use）。

カテゴリに応じてコードがツールを呼び分ける。LLM の Tool 直呼びは v1 では採用しない
（誤呼び出し防止）。並列化はせず逐次実行（モック前提のため）。実 DB 接続後は
asyncio.gather で並列化を検討。
"""
from __future__ import annotations

from .db_client import (
    lookup_cad_url,
    lookup_discontinued,
    lookup_inventory,
    lookup_price,
    lookup_shipment,
)
from .logger import logger
from .state import TriageState


def retrieve_node(state: TriageState) -> dict:
    logger.info("[4/7] retrieve")
    skus = state.get("skus", []) or []
    order_nos = state.get("order_nos", []) or []
    category = state.get("category", "other")
    customer_id = state.get("customer_id")

    retrieved: dict = {}
    errors: list = []

    if category in ("inventory", "alternative"):
        inv = {sku: lookup_inventory(sku) for sku in skus[:10]}
        disc = {sku: lookup_discontinued(sku) for sku in skus[:10]}
        retrieved["inventory"] = inv
        retrieved["discontinued"] = disc
        if category == "inventory":
            retrieved["price"] = {sku: lookup_price(sku, customer_id) for sku in skus[:10]}
        # 廃番なら後継品も引き当てる
        for sku, d in disc.items():
            if d.get("ok") and d.get("is_discontinued") and d.get("successor_sku"):
                successor = d["successor_sku"]
                retrieved.setdefault("inventory", {})[successor] = lookup_inventory(successor)

    if category == "shipment":
        retrieved["shipment"] = {ono: lookup_shipment(ono) for ono in order_nos[:5]}

    if category == "cad":
        retrieved["cad"] = {sku: lookup_cad_url(sku) for sku in skus[:10]}

    # tech / billing / complaint / other はカテゴリ単独で DB 引き当てなし

    for kind, results in retrieved.items():
        for key, r in results.items():
            if not r.get("ok"):
                errors.append(f"{kind}:{key}:{r.get('reason')}")

    logger.info("  => kinds=%s errors=%d", list(retrieved.keys()), len(errors))
    return {"retrieved_data": retrieved, "retrieve_errors": errors}
