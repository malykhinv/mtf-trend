"""Registered pump-long hypotheses, each expressed as a universe config.

Run with ``python -m anomaly_science.strategy.pump_long.research.hypotheses``.
Every study here reuses the one universe builder (:mod:`.context`), the one
Core simulator, and the one metrics module (:mod:`.metrics`) - so a new
hypothesis is a filter + a couple of knobs, not a new script. Findings are
written up in ``docs/strategies/pump_long.md``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_long.research.backtest import run_backtest
from anomaly_science.strategy.pump_long.research.context import (
    build_event_table,
    build_symbol_cache,
    prior_fade_context,
)

RECENT_MIN = 360  # "recent" prior fade = last one < 6h ago


def _distributed_vol(events: pd.DataFrame, ref: pd.DataFrame) -> pd.Series:
    """Distributed-volume entry filter: top-candle turnover share <= median."""

    return events["tcs"] <= ref["tcs"].median()


def window_sweep(ev: pd.DataFrame, cache_factory) -> None:
    """Lookback-window comparison 24-144h (near overhead target)."""

    print("\n=== lookback window sweep (near target, distrib-vol, recent<6h) ===", flush=True)
    tcs_med = ev["tcs"].median()
    for window_h in (24, 36, 48, 64, 96, 144):
        d = prior_fade_context(ev, window_h=window_h)
        uni = d.loc[(d["last_prior_min"] < RECENT_MIN) & (d["tcs"] <= tcs_med) & d["near"].notna()]
        if len(uni) < 15:
            print(f"  {window_h:>3}h: too few ({len(uni)})", flush=True)
            continue
        cache = cache_factory(uni["symbol"].unique())
        res = run_backtest(uni, cache, target="near")
        print(res.summary(risk=0.03).line(f"{window_h:>3}h near"), flush=True)


def target_ab(ev: pd.DataFrame, cache_factory) -> None:
    """NEAR vs FAR overhead target on the same broad 48h universe."""

    print("\n=== target A/B (same 48h universe) ===", flush=True)
    tcs_med = ev["tcs"].median()
    d = prior_fade_context(ev, window_h=48)
    uni = d.loc[(d["last_prior_min"] < RECENT_MIN) & (d["tcs"] <= tcs_med) & d["near"].notna()]
    cache = cache_factory(uni["symbol"].unique())
    for tgt in ("near", "far"):
        res = run_backtest(uni, cache, target=tgt)
        print(res.summary().line(f"{tgt.upper()} target"), flush=True)


def level_cluster_by_tests(ev: pd.DataFrame, cache_factory) -> None:
    """Tradeable EV by number of prior tests of the resistance level."""

    print("\n=== level cluster, tradeable EV by n_tests (target below level) ===", flush=True)
    tcs_med = ev["tcs"].median()
    d = prior_fade_context(ev, window_h=48)
    d = d.loc[(d["tcs"] <= tcs_med) & (d["close0"] < d["level"])]
    d["tb"] = np.where(d["n_tests"] >= 3, "3+", d["n_tests"].astype(str))
    cache = cache_factory(d["symbol"].unique())
    for bucket in ("1", "2", "3+", "ALL"):
        sub = d if bucket == "ALL" else d.loc[d["tb"] == bucket]
        if len(sub) < 12:
            print(f"  n_tests={bucket}: too few ({len(sub)})", flush=True)
            continue
        res = run_backtest(sub, cache, target="level")
        print(res.summary().line(f"n_tests={bucket}"), flush=True)


def main() -> None:
    print("building event table (development)...", flush=True)
    ev = build_event_table()
    print(f"  events: {len(ev)}", flush=True)

    _cache: dict = {}

    def cache_factory(symbols) -> dict:
        missing = [s for s in symbols if s not in _cache]
        if missing:
            _cache.update(build_symbol_cache(missing))
        return _cache

    window_sweep(ev, cache_factory)
    target_ab(ev, cache_factory)
    level_cluster_by_tests(ev, cache_factory)


if __name__ == "__main__":
    main()
