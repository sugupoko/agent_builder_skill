"""YAML 設定ローダ。"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: Path | str) -> dict:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: Path | str) -> dict:
    cfg = load_yaml(path)
    cfg.setdefault("__path__", str(Path(path).resolve()))
    return cfg
