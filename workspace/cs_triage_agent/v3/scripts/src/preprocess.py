"""ノード 1: preprocess — PII マスキング / 言語判定 / 添付検出。

完全に決定論コード。LLM 呼び出しなし。
"""
from __future__ import annotations

import re

from .logger import logger
from .pii_mask import mask_pii
from .state import TriageState

_HIRAGANA_KATAKANA = re.compile(r"[ぁ-んァ-ヶー]")
_ATTACH_MARKER = re.compile(
    r"(添付|attached|attachment|別添|別紙|see\s+attached)", re.IGNORECASE
)


def _detect_lang(text: str) -> str:
    if _HIRAGANA_KATAKANA.search(text):
        return "ja"
    if re.search(r"[A-Za-z]{4,}", text):
        return "en"
    return "other"


def _detect_attachment(text: str) -> bool:
    return bool(_ATTACH_MARKER.search(text))


def preprocess_node(state: TriageState) -> dict:
    logger.info("[1/7] preprocess")
    raw = state.get("raw_text", "")
    masked, pii_map = mask_pii(raw)
    lang = _detect_lang(raw)
    has_attach = _detect_attachment(raw)
    logger.info(
        "  => lang=%s pii=%d attachment=%s",
        lang, len([k for k in pii_map if not k.startswith("__")]), has_attach,
    )
    return {
        "masked_text": masked,
        "pii_map": pii_map,
        "lang": lang,
        "has_attachment": has_attach,
    }
