from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from config import config as app_config


@dataclass(frozen=True, slots=True)
class StreamLimit:
    steady_per_min: int
    burst_per_5s: int
    max_symbols: int
    resubscribe_buffer: int
    max_weight: float

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> "StreamLimit":
        return cls(
            steady_per_min=int(mapping.get("steady_per_min", 0)),
            burst_per_5s=int(mapping.get("burst_per_5s", 0)),
            max_symbols=int(mapping.get("max_symbols", 0)),
            resubscribe_buffer=int(mapping.get("resubscribe_buffer", 0)),
            max_weight=float(mapping.get("max_weight", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class StreamLimits:
    depth: StreamLimit
    trades: StreamLimit
    book_ticker: StreamLimit

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> "StreamLimits":
        return cls(
            depth=StreamLimit.from_mapping(mapping.get("depth", {})),
            trades=StreamLimit.from_mapping(mapping.get("trades", {})),
            book_ticker=StreamLimit.from_mapping(mapping.get("book_ticker", {})),
        )


def _default_limits_path() -> Path:
    base = Path(app_config.__file__).resolve().parent
    return base / "stream_limits.json"


def load_stream_limits(path: Path | None = None) -> StreamLimits:
    source = path or _default_limits_path()
    data: Dict[str, Any]
    if not source.exists():
        data = {}
    else:
        with source.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    return StreamLimits.from_mapping(data)


