from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
import strategy.hourly_asia_pump.short_edge as short_edge_module
from strategy.hourly_asia_pump.short_edge import (
    ShortContextProfile,
    ShortExecutionModel,
    ShortGeometry,
    ShortNextPressureProfile,
    ShortSignalProfile,
    ShortTriggerPressureProfile,
    build_hourly_asia_pump_short_edge_artifacts,
)


class _FakePreparer:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        return self._frames.get((symbol, timeframe.value), pd.DataFrame()).copy()


def _build_symbol_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    bars_5m: list[dict[str, object]] = []
    bars_1m: list[dict[str, object]] = []
    for month in range(1, 5):
        start = pd.Timestamp(year=2025, month=month, day=1, hour=0, minute=0, tz="UTC")
        ohlc_rows = [
            (100.0, 100.4, 99.8, 100.1),
            (100.1, 112.0, 99.8, 110.4),   # anomaly
            (110.4, 111.0, 106.0, 106.6),  # weak next bar
            (109.6, 109.8, 104.0, 104.8),  # third open near top
            (104.8, 105.0, 98.4, 99.1),
            (99.1, 100.0, 97.4, 98.0),
            (98.0, 98.6, 97.5, 98.1),
        ]
        for idx, (open_price, high_price, low_price, close_price) in enumerate(ohlc_rows):
            timestamp = start + pd.Timedelta(minutes=idx * 5)
            bars_5m.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1000),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": 1_000_000.0 if idx == 1 else 140_000.0,
                }
            )
            for minute_idx in range(5):
                minute_ts = timestamp + pd.Timedelta(minutes=minute_idx)
                bars_1m.append(
                    {
                        "timestamp": int(minute_ts.timestamp() * 1000),
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": 40_000.0,
                    }
                )
    return pd.DataFrame(bars_5m), pd.DataFrame(bars_1m)


def _build_base_events_csv(path: Path, symbols: list[str], frames_by_symbol: dict[str, pd.DataFrame]) -> None:
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        frame = frames_by_symbol[symbol].reset_index(drop=True)
        for row_index in range(1, len(frame), 7):
            timestamp_ms = int(frame.iloc[row_index]["timestamp"])
            timestamp = pd.to_datetime(timestamp_ms, unit="ms", utc=True)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": "5m",
                    "row_index": row_index,
                    "timestamp_ms": timestamp_ms,
                    "timestamp_utc": timestamp.isoformat(),
                    "date_utc": timestamp.strftime("%Y-%m-%d"),
                    "hour_utc": int(timestamp.hour),
                    "trigger_open": float(frame.iloc[row_index]["open"]),
                    "trigger_high": float(frame.iloc[row_index]["high"]),
                    "trigger_low": float(frame.iloc[row_index]["low"]),
                    "trigger_close": float(frame.iloc[row_index]["close"]),
                    "trigger_volume": float(frame.iloc[row_index]["volume"]),
                    "trigger_return_pct": 0.103,
                    "trigger_range_pct": 0.122,
                    "range_atr": 8.5,
                    "body_atr": 7.8,
                    "volume_mult": 13.0,
                    "close_to_high_frac": 0.13,
                    "breakout_pct": 0.0,
                    "pre_base_range_pct_60m": 0.04,
                    "pre_base_drift_pct_60m": 0.02,
                    "pre_base_range_vs_trigger": 0.35,
                    "atr_window_minutes": 180,
                    "volume_window_minutes": 720,
                    "breakout_lookback_minutes": 60,
                    "max_follow_minutes": 720,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_build_hourly_asia_pump_short_edge_artifacts(tmp_path: Path, monkeypatch) -> None:
    symbols = ["AAA/USDT:USDT", "BBB/USDT:USDT"]
    frames_5m: dict[str, pd.DataFrame] = {}
    preparer_frames: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in symbols:
        frame_5m, frame_1m = _build_symbol_frames()
        frames_5m[symbol] = frame_5m
        preparer_frames[(symbol, "5m")] = frame_5m
        preparer_frames[(symbol, "1m")] = frame_1m

    base_events_path = tmp_path / "trade_model_events.csv"
    output_dir = tmp_path / "short_edge"
    _build_base_events_csv(base_events_path, symbols, frames_5m)

    model = ShortExecutionModel(
        model_id="short_test",
        label="test",
        signal_profile=ShortSignalProfile("sig_weak", 0.05, 6.0, 7.0, 0.15),
        trigger_pressure_profile=ShortTriggerPressureProfile("tp_none", 0.0, 1.0),
        next_pressure_profile=ShortNextPressureProfile("np_soft", 0.15, 0.50, 0.10, 0.65, 0.01),
        context_profile=ShortContextProfile("ctx_hot_drift", min_pre_base_drift_pct_60m=0.015),
        geometry=ShortGeometry("open3_s10_rr15", "third_open", None, 0, 0.10, 1.5, max_open_entry_from_high_frac=0.40),
        rule_text="test",
    )
    monkeypatch.setattr(short_edge_module, "_build_execution_models", lambda: [model])

    artifacts = build_hourly_asia_pump_short_edge_artifacts(
        base_events_path=base_events_path,
        output_dir=output_dir,
        commission_rate=0.0004,
        preparer=_FakePreparer(preparer_frames),
    )

    best_summary = pd.read_csv(artifacts["best_summary"])
    best_monthly = pd.read_csv(artifacts["best_monthly"])
    context = json.loads(Path(artifacts["context"]).read_text(encoding="utf-8"))

    assert len(best_summary) == 1
    assert "context_profile_id" in best_summary.columns
    assert not best_monthly.empty
    assert context["search_scope"]["same_rules_for_all_xx00"] is True
    assert Path(artifacts["report"]).exists()
    assert Path(artifacts["feature_buckets"]).exists()
