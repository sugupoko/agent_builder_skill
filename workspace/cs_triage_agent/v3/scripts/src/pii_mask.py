"""PII マスキング。LLM API 送信前に必ず通す。

v1 のスコープ:
    - 顧客氏名（漢字姓名 / 「○○様」「○○さん」のパターン）
    - 電話番号（日本国内 + 国際形式 +81）
    - メールアドレス
    - 住所（都道府県+市区町村+番地 ざっくりパターン）

会社名は spec.md §16-3 で「完全/部分」未確定のため v1 ではマスクしない。
"""
from __future__ import annotations

import re

# 単純に書き手名を抽出する基本パターン群。
# 注: 過検知より見逃しを警戒する。誤検知が出たら placeholder で残るだけだが、
# 漏れは LLM API に PII を送ることに直結するため致命的。
_NAME_PATTERN = re.compile(
    r"(?:[一-龥々]{1,4}[ 　]?[一-龥々]{1,4})(?=(?:様|さん|殿|先生)\b)"
)
_NAME_HONORIFIC = re.compile(
    r"((?:[一-龥々]{1,4}|[ぁ-んァ-ヶー]{1,8})(?:様|さん|殿|先生))"
)
_PHONE_JP = re.compile(
    # 0 の直前が数字またはハイフンでないことを要求（注文番号 ORD-2026-... の誤マッチ回避）
    r"(?:\+81[-\s]?|(?<![\d\-])0)\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}"
)
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_ADDRESS = re.compile(
    r"(?:東京都|北海道|(?:京都|大阪)府|[一-龥]{2,3}県)"
    r"[一-龥ぁ-ん0-9０-９ーー\-]+(?:市|区|町|村)"
    r"[一-龥ぁ-ん0-9０-９ーー\-]*"
)


def mask_pii(text: str) -> tuple[str, dict]:
    """生テキストを (masked_text, pii_map) に分割する。

    pii_map は placeholder → 原文の復元辞書。assemble ノードで unmask する。
    マスキング困難な箇所は __warning__ にリストとして集める。
    """
    pii_map: dict = {}
    counts = {"NAME": 0, "PHONE": 0, "EMAIL": 0, "ADDR": 0}
    warnings: list = []

    def replace_with(label: str, match: re.Match) -> str:
        original = match.group(0)
        # 同一文字列は同一 placeholder に統一
        for k, v in pii_map.items():
            if v == original:
                return k
        counts[label] += 1
        key = f"[{label}_{counts[label]:03d}]"
        pii_map[key] = original
        return key

    # 順序が重要: 長いマッチを先に消す
    text = _ADDRESS.sub(lambda m: replace_with("ADDR", m), text)
    text = _EMAIL.sub(lambda m: replace_with("EMAIL", m), text)
    text = _PHONE_JP.sub(lambda m: replace_with("PHONE", m), text)
    text = _NAME_HONORIFIC.sub(lambda m: replace_with("NAME", m), text)

    if warnings:
        pii_map["__warning__"] = warnings
    return text, pii_map


def unmask(text: str, pii_map: dict) -> str:
    """final_output 直前に PII を復元する。"""
    for k, v in pii_map.items():
        if k == "__warning__":
            continue
        text = text.replace(k, v)
    return text
