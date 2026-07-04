"""Bee-bite adapter for the strategy-neutral visualization package."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.bee_bite.spring import NEW_HIGH_EPS
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE
from anomaly_science.visualization import (
    CandleSeries,
    ChartSpec,
    HistogramSeries,
    HorizontalLevel,
    LineSeries,
    PointMarker,
    PriceZone,
    SwingPoint,
    render_chart,
)
from anomaly_science.visualization.theme import DEFAULT_THEME

DEFAULT_OUTCOMES = Path(".output/results/bee_bite_v1/spring_outcomes.parquet")
DEFAULT_CHART_DIR = Path(".output/results/bee_bite_v1/charts/lower_tests_le_2")


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(values).ewm(span=span, adjust=False).mean().to_numpy(float)


# A pivot needs a counter-move of at least max(2.5*ATR, 6% of the window's full
# price range) - marked at a structural scale, like a trader, not every wiggle.
SWING_REVERSAL_ATR = 2.5
SWING_MIN_RANGE_FRAC = 0.06


def _zigzag(high: np.ndarray, low: np.ndarray, reversal: float) -> list[tuple[int, float, str]]:
    """Alternating ZigZag pivots: a high is confirmed only after price drops
    `reversal` off it (and vice-versa), so pivots strictly alternate high/low
    and micro-wiggles are ignored - what a trader would actually mark."""
    n = len(high)
    if n < 2 or not (reversal > 0):
        return []
    pivots: list[tuple[int, float, str]] = []
    hi_idx, hi = 0, float(high[0])
    lo_idx, lo = 0, float(low[0])
    direction = 0
    for i in range(1, n):
        hh, ll = float(high[i]), float(low[i])
        if direction >= 0:
            if hh >= hi:
                hi, hi_idx = hh, i
            if hi - ll >= reversal:
                pivots.append((hi_idx, hi, "high"))
                direction = -1; lo, lo_idx = ll, i
                continue
        if direction <= 0:
            if ll <= lo:
                lo, lo_idx = ll, i
            if hh - lo >= reversal:
                pivots.append((lo_idx, lo, "low"))
                direction = 1; hi, hi_idx = hh, i
    return pivots


def _swings(
    timestamps: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[SwingPoint, ...]:
    atr = causal_atr(high=high, low=low, close=close, window=14)
    finite = atr[np.isfinite(atr)]
    atr_rev = SWING_REVERSAL_ATR * float(np.median(finite)) if len(finite) else 0.0
    range_rev = SWING_MIN_RANGE_FRAC * (float(high.max()) - float(low.min())) if len(high) else 0.0
    reversal = max(atr_rev, range_rev)
    return tuple(
        SwingPoint(int(timestamps[i]), price, kind)
        for i, price, kind in _zigzag(high, low, reversal)
    )


def _nearest_index(timestamps: np.ndarray, timestamp_ms: int) -> int:
    index = int(np.searchsorted(timestamps, timestamp_ms, side="left"))
    return max(0, min(index, len(timestamps) - 1))


def build_chart_spec(frame: pd.DataFrame, outcome: pd.Series) -> ChartSpec:
    """Translate one strategy row into generic visual primitives."""
    timestamps = frame["timestamp"].to_numpy(np.int64)
    pump_index = _nearest_index(timestamps, int(outcome.pump_time_ms))
    sweep_index = _nearest_index(timestamps, int(outcome.sweep_time_ms))
    entry_index = _nearest_index(timestamps, int(outcome.entry_time_ms))
    exit_index = _nearest_index(timestamps, int(outcome.exit_time_ms_wick_perehai))
    pump_start = max(0, pump_index - int(outcome.pump_duration) + 1)
    window_start = max(0, pump_start - 12)
    window_end = min(len(frame) - 1, max(exit_index, entry_index) + 12)
    window = frame.iloc[window_start:window_end + 1].reset_index(drop=True)
    ts = window["timestamp"].to_numpy(np.int64)
    o = window["open"].to_numpy(float)
    h = window["high"].to_numpy(float)
    low = window["low"].to_numpy(float)
    close = window["close"].to_numpy(float)

    consolidation = frame.iloc[pump_index + 1:pump_index + 31]
    range_low = float(consolidation["low"].min())
    range_high = max(float(consolidation["high"].max()), float(frame.iloc[pump_index]["high"]))
    stop = float(frame.iloc[sweep_index]["low"])
    entry = float(frame.iloc[entry_index]["open"])
    target = range_high * (1.0 + NEW_HIGH_EPS)
    if outcome.exit_reason_wick_perehai == "take_profit":
        exit_price = target
    elif outcome.exit_reason_wick_perehai == "initial_stop":
        exit_price = stop
    else:
        exit_price = float(frame.iloc[exit_index]["close"])
    outcome_label = "PEREHAI" if bool(outcome.new_high) else "NO PEREHAI"
    theme = DEFAULT_THEME
    return ChartSpec(
        candles=CandleSeries(ts, o, h, low, close),
        title=f"{outcome.symbol}  |  {outcome_label}  |  net {outcome.net_wick_perehai * 100:+.2f}%",
        subtitle_lines=(
            f"upper tests {int(outcome.upper_test_count)}  lower tests {int(outcome.lower_test_count)}",
            f"pump {outcome.pump_size * 100:.1f}%  held {outcome.held_ratio:.2f}  compression {outcome.range_compression:.2f}",
        ),
        lines=(
            LineSeries("EMA 9", _ema(close, 9), theme.ema_fast, 0.9, 0.85),
            LineSeries("EMA 20", _ema(close, 20), theme.ema_slow, 0.9, 0.78),
        ),
        swings=tuple(
            swing for swing in _swings(ts, h, low, close)
            if int(outcome.pump_time_ms) <= swing.timestamp_ms <= int(outcome.sweep_time_ms)
        ),
        levels=(
            HorizontalLevel(entry, theme.entry, "ENTRY", int(outcome.entry_time_ms)),
            HorizontalLevel(stop, theme.stop, "STOP", int(outcome.sweep_time_ms)),
            HorizontalLevel(target, theme.target, "PEREHAI", int(outcome.entry_time_ms)),
        ),
        zones=(
            PriceZone(
                range_low, range_high, int(outcome.pump_time_ms), int(outcome.exit_time_ms_wick_perehai),
                theme.zone_edge, theme.zone_face, "range", 0.13,
            ),
        ),
        markers=(
            PointMarker(int(outcome.pump_time_ms), float(frame.iloc[pump_index]["high"]), theme.swing_high, "high", "v"),
            PointMarker(int(outcome.sweep_time_ms), stop, theme.swing_low, "sweep", "^"),
            PointMarker(int(outcome.entry_time_ms), entry, theme.entry, "entry", "o"),
            PointMarker(int(outcome.exit_time_ms_wick_perehai), exit_price, theme.exit, "exit", "x"),
        ),
        histograms=(
            HistogramSeries("quote volume", window["quote_volume"].to_numpy(float), theme.ema_slow, 0.38, True),
            HistogramSeries("trade count", window["trade_count"].to_numpy(float), theme.swing_low, 0.30, True),
        ),
        metadata={"symbol": outcome.symbol, "entry_time_ms": int(outcome.entry_time_ms)},
    )


def render_profile_charts(
    *,
    outcomes_path: Path = DEFAULT_OUTCOMES,
    cache_dir: Path = DEFAULT_CACHE,
    output_dir: Path = DEFAULT_CHART_DIR,
) -> pd.DataFrame:
    outcomes = pd.read_parquet(outcomes_path)
    # Post-gate universe is already validity-filtered; render all of it for QA.
    selected = outcomes.sort_values(["symbol", "entry_time_ms"])
    records: list[dict[str, object]] = []
    for index, outcome in enumerate(selected.itertuples(index=False), start=1):
        frame, _quality = _load_symbol(cache_dir / f"{outcome.symbol}.parquet")
        group = "perehai" if bool(outcome.new_high) else "no_perehai"
        stamp = pd.Timestamp(int(outcome.entry_time_ms), unit="ms", tz="UTC").strftime("%Y%m%d_%H%M")
        path = output_dir / group / f"{stamp}_{outcome.symbol}.png"
        spec = build_chart_spec(frame, pd.Series(outcome._asdict()))
        render_chart(spec, path)
        records.append(
            {
                "symbol": outcome.symbol,
                "entry_time_ms": int(outcome.entry_time_ms),
                "perehai": bool(outcome.new_high),
                "net_return": float(outcome.net_wick_perehai),
                "chart_path": str(path),
            }
        )
        if index % 10 == 0 or index == len(selected):
            print(f"rendered {index}/{len(selected)} charts", flush=True)
    manifest = pd.DataFrame(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest.to_parquet(output_dir / "manifest.parquet", index=False)
    return manifest


def main() -> None:
    manifest = render_profile_charts()
    print(f"saved {len(manifest)} charts -> {DEFAULT_CHART_DIR}", flush=True)


if __name__ == "__main__":
    main()
