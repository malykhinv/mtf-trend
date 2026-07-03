"""Universe construction for pump-long research.

Turns the registered runner state-lattice into a per-event table and attaches
the 48h prior-pump-fade context (recurrence, overhead-resistance targets, and
multi-tested level clusters) that every hypothesis conditions on. This is the
logic that used to be copy-pasted into each ``tmp`` script; now it lives once.

Leak safety: ``faded`` is the resolved nature of a PRIOR event; a prior only
enters the context of event ``i`` when its ignition precedes ``i``'s ignition,
matching the "resolved before t0" rule enforced upstream in the lattice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Shared, schema-validated 1m loader for the enriched market cache. It is data
# infrastructure both strategies depend on; imported here once rather than
# re-implemented per hypothesis.
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.simulation.numpy_path import causal_atr

DEFAULT_CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_LATTICE = Path(".output/results/pump_long_v1/state_lattice_runner.parquet")
DEV_END_MS = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
LEVEL_BAND = (0.85, 1.05)  # cluster prior peaks near the reference resistance


def build_event_table(
    lattice_path: Path = DEFAULT_LATTICE,
    *,
    end_ms: int | None = DEV_END_MS,
    start_ms: int | None = None,
) -> pd.DataFrame:
    """Collapse the per-decision runner lattice into one row per event.

    ``end_ms`` / ``start_ms`` bound the development (or OOS) window by snapshot
    time. Pass ``end_ms=None`` for no upper bound.
    """

    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    if start_ms is not None:
        mask &= lat["snapshot_time_ms"] >= start_ms
    lat = lat.loc[mask].copy()
    lat["ign"] = lat["group"].str.rsplit(":", n=1).str[-1].astype("int64")
    agg = {
        "symbol": ("symbol", "first"),
        "ign": ("ign", "first"),
        "base": ("base_level", "first"),
        "close0": ("current_close", "first"),
        "tcs": ("turnover_top_candle_share", "first"),
        "peak": ("anchor_high", "max"),
        "faded": ("nature_y", "first"),
        "snap0": ("snapshot_time_ms", "first"),
    }
    if "forward_mfe" in lat.columns:
        agg["fmfe"] = ("forward_mfe", "first")
    ev = lat.sort_values("snapshot_time_ms").groupby("group").agg(**agg).reset_index()
    ev["faded"] = ev["faded"].fillna(0).astype(int)
    stamp = pd.to_datetime(ev["snap0"], unit="ms", utc=True)
    ev["mo"] = stamp.dt.strftime("%Y-%m")
    ev["week"] = stamp.dt.strftime("%G-W%V")
    return ev


def prior_fade_context(
    events: pd.DataFrame,
    *,
    window_h: int = 48,
    band: tuple[float, float] = LEVEL_BAND,
) -> pd.DataFrame:
    """Attach 48h prior-fade recurrence + resistance context per event.

    Adds, for each event that has >=1 FADED prior pump in the window:
      - ``n_prior_fades``      count of faded priors in the window
      - ``last_prior_min``     minutes since the most recent prior ignition
      - ``near`` / ``far``     nearest / furthest prior peak still above price
      - ``n_tests`` / ``level``  multi-tested resistance cluster (median peak)
    Events with no qualifying prior fade are dropped (not part of the universe).
    """

    window_ms = window_h * 3600 * 1000
    out: list[dict] = []
    for symbol, g in events.groupby("symbol"):
        g = g.sort_values("ign").reset_index(drop=True)
        ign = g["ign"].to_numpy()
        peak = g["peak"].to_numpy()
        faded = g["faded"].to_numpy()
        close0 = g["close0"].to_numpy()
        for i in range(len(g)):
            prior = (ign < ign[i]) & (ign >= ign[i] - window_ms) & (faded == 1)
            if not prior.any():
                continue
            prior_peaks = peak[prior]
            overhead = prior_peaks[prior_peaks > close0[i]]
            ref = prior_peaks.max()
            cluster = prior_peaks[(prior_peaks >= band[0] * ref) & (prior_peaks <= band[1] * ref)]
            row = g.iloc[i].to_dict()
            row.update(
                symbol=symbol,
                n_prior_fades=int(prior.sum()),
                last_prior_min=float((ign[i] - ign[prior].max()) / 60000.0),
                near=float(overhead.min()) if len(overhead) else np.nan,
                far=float(overhead.max()) if len(overhead) else np.nan,
                n_tests=int(len(cluster)),
                level=float(np.median(cluster)),
            )
            out.append(row)
    return pd.DataFrame(out)


def load_symbol_arrays(
    symbol: str,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    atr_window: int = 30,
) -> tuple[np.ndarray, ...]:
    """Load one symbol's closed 1m bars as arrays plus a causal ATR.

    Returns ``(timestamp, open, high, low, close, atr)``.
    """

    frame = _load_symbol(cache_dir / f"{symbol}.parquet")[0]
    ts = frame["timestamp"].to_numpy(np.int64)
    open_ = frame["open"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    close = frame["close"].to_numpy(float)
    atr = causal_atr(high=high, low=low, close=close, window=atr_window)
    return ts, open_, high, low, close, atr


def build_symbol_cache(
    symbols: list[str], *, cache_dir: Path = DEFAULT_CACHE
) -> dict[str, tuple[np.ndarray, ...]]:
    """Preload arrays for a set of symbols (one pass, with progress logs)."""

    cache: dict[str, tuple[np.ndarray, ...]] = {}
    total = len(symbols)
    for k, sym in enumerate(sorted(symbols), 1):
        cache[sym] = load_symbol_arrays(sym, cache_dir=cache_dir)
        if k % 40 == 0 or k == total:
            print(f"  loaded {k}/{total} symbols", flush=True)
    return cache


__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_LATTICE",
    "DEV_END_MS",
    "build_event_table",
    "build_symbol_cache",
    "load_symbol_arrays",
    "prior_fade_context",
]
