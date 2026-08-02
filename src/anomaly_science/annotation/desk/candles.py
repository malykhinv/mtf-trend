"""OHLCV cache and window extraction for level-desk charts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.desk.candidates import json_candidate_row


@dataclass(slots=True)
class OhlcvWindowService:
    cache_dir: Path
    tf_minutes: dict[str, int]
    _cache: dict[tuple[str, str], dict[str, np.ndarray]] = field(default_factory=dict)

    def frame(self, symbol: str, tf: str) -> dict[str, np.ndarray]:
        key = (symbol, tf)
        if key not in self._cache:
            minutes = self.tf_minutes.get(tf)
            if minutes is None:
                raise ValueError(f"unknown tf {tf!r}")
            base = load_ohlcv_parquet(self.cache_dir / f"{symbol}.parquet")
            self._cache[key] = base if minutes == 1 else resample_ohlcv_np(base, minutes)
        return self._cache[key]

    def event_payload(self, row: pd.Series, *, tf: str | None = None) -> dict[str, Any]:
        symbol = str(row.symbol)
        source_tf = str(row.tf)
        selected_tf = str(tf or source_tf)
        frame = self.frame(symbol, selected_tf)
        ts = frame["timestamp"]
        a = int(np.searchsorted(ts, int(row.review_start_ms), side="left"))
        b = int(np.searchsorted(ts, int(row.review_end_ms), side="right"))
        event = json_candidate_row(row.replace({np.nan: None}).to_dict())
        event["source_tf"] = source_tf
        event["tf"] = selected_tf
        marker_cols = [c for c in row.index if c.endswith("_ms") or c.endswith("_price")]
        event["marker_columns"] = {c: event.get(c) for c in marker_cols if event.get(c) is not None}
        return {"event": event, "candles": _frame_slice(frame, a, b),
                "data_start_ms": int(ts[0]) if len(ts) else None,
                "data_end_ms": int(ts[-1]) if len(ts) else None}

    def candles_range(self, symbol: str, tf: str, start_ms: int, end_ms: int,
                      max_bars: int = 6000) -> dict[str, Any]:
        """Arbitrary [start_ms, end_ms] slice of a symbol's cached history, for
        lazy loading more candles when the user pans the chart. Capped to max_bars
        and annotated with the full cache bounds so the client stops at the edges."""
        frame = self.frame(symbol, tf)
        ts = frame["timestamp"]
        a = int(np.searchsorted(ts, int(start_ms), side="left"))
        b = int(np.searchsorted(ts, int(end_ms), side="right"))
        b = max(b, a + 1)
        if b - a > max_bars:  # keep the edge nearest the pan direction bounded
            b = a + max_bars
        return {
            "candles": _frame_slice(frame, a, b),
            "data_start_ms": int(ts[0]) if len(ts) else None,
            "data_end_ms": int(ts[-1]) if len(ts) else None,
        }

    def trade_payload(
        self,
        *,
        trade: dict[str, Any],
        result_annotation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        symbol = str(trade["symbol"])
        tf = str(trade["tf"])
        minutes = self.tf_minutes.get(tf, 1)
        frame = self.frame(symbol, tf)
        ts = frame["timestamp"]
        fill_ms = int(trade["fill_time_ms"])
        exit_ms = int(trade["exit_time_ms"])
        pad = max(48 * minutes * 60_000, 6 * 3_600_000)
        a = int(np.searchsorted(ts, fill_ms - pad, side="left"))
        b = int(np.searchsorted(ts, exit_ms + pad, side="right"))
        b = max(b, a + 1)
        trade_id = str(trade["trade_id"])
        return {
            "event": {
                "event_id": trade_id,
                "symbol": symbol,
                "tf": tf,
                "pump_pct": trade.get("ignition_rise"),
                "pump_over_sleep_vol": trade.get("pump_over_sleep_vol"),
                "pump_over_sleep_trades": trade.get("pump_over_sleep_trades"),
                "marker_columns": {},
            },
            "trade": trade,
            "result_annotation": result_annotation,
            "candles": _frame_slice(frame, a, b),
        }


def _frame_slice(frame: dict[str, np.ndarray], start: int, stop: int) -> dict[str, list[Any]]:
    return {
        "timestamp": frame["timestamp"][start:stop].astype(int).tolist(),
        "open": frame["open"][start:stop].astype(float).tolist(),
        "high": frame["high"][start:stop].astype(float).tolist(),
        "low": frame["low"][start:stop].astype(float).tolist(),
        "close": frame["close"][start:stop].astype(float).tolist(),
        "quote_volume": frame["quote_volume"][start:stop].astype(float).tolist(),
    }
