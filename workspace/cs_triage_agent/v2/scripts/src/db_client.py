"""社内 DB のモック実装。v1 は YAML フィクスチャから読む。

deploy フェーズで実 DB に差し替える際は、各 lookup_* 関数のシグネチャは保ったまま
内部実装だけを postgresql / ERP API 呼び出しに変える。

設計原則:
    - 例外を投げない。失敗は {"ok": False, "reason": "..."} で返す
    - 1 リクエスト内で同一引数なら結果をキャッシュ（プロセス内 dict）
    - clear_tool_cache() でリクエスト終了時にキャッシュリセット
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import load_yaml

_CACHE: dict = {}
_FIXTURES: dict = {}


def load_fixtures(mock_db_dir: Path | str) -> None:
    """モック DB のフィクスチャをメモリに読み込む。"""
    p = Path(mock_db_dir)
    _FIXTURES["inventory"] = load_yaml(p / "inventory.yaml")
    _FIXTURES["price"] = load_yaml(p / "price.yaml")
    _FIXTURES["shipment"] = load_yaml(p / "shipment.yaml")
    _FIXTURES["discontinued"] = load_yaml(p / "discontinued.yaml")
    _FIXTURES["cad"] = load_yaml(p / "cad.yaml")


def clear_tool_cache() -> None:
    _CACHE.clear()


def _cached(key: tuple, fn):
    if key in _CACHE:
        return _CACHE[key]
    result = fn()
    _CACHE[key] = result
    return result


def lookup_inventory(sku: str) -> dict:
    """在庫数 + 出荷可能日。失敗時は ok=False。"""
    def _fn():
        rec = _FIXTURES.get("inventory", {}).get(sku)
        if rec is None:
            return {"ok": False, "reason": "sku_not_found", "sku": sku}
        return {"ok": True, "sku": sku, **rec}
    return _cached(("inv", sku), _fn)


def lookup_price(sku: str, customer_id: Optional[str] = None) -> dict:
    """単価（取引先別）。customer_id 連携は v1 はスコープ外。"""
    def _fn():
        rec = _FIXTURES.get("price", {}).get(sku)
        if rec is None:
            return {"ok": False, "reason": "sku_not_in_price_db", "sku": sku}
        return {"ok": True, "sku": sku, **rec}
    return _cached(("price", sku, customer_id), _fn)


def lookup_shipment(order_no: str) -> dict:
    """出荷状況 + 追跡 URL。"""
    def _fn():
        rec = _FIXTURES.get("shipment", {}).get(order_no)
        if rec is None:
            return {"ok": False, "reason": "order_not_found", "order_no": order_no}
        return {"ok": True, "order_no": order_no, **rec}
    return _cached(("ship", order_no), _fn)


def lookup_discontinued(sku: str) -> dict:
    """廃番フラグ + 後継品。"""
    def _fn():
        rec = _FIXTURES.get("discontinued", {}).get(sku)
        if rec is None:
            return {"ok": True, "sku": sku, "is_discontinued": False}
        return {"ok": True, "sku": sku, "is_discontinued": True, **rec}
    return _cached(("disc", sku), _fn)


def lookup_cad_url(sku: str) -> dict:
    """CAD / 図面 URL。"""
    def _fn():
        rec = _FIXTURES.get("cad", {}).get(sku)
        if rec is None:
            return {"ok": False, "reason": "cad_not_available", "sku": sku}
        return {"ok": True, "sku": sku, **rec}
    return _cached(("cad", sku), _fn)
