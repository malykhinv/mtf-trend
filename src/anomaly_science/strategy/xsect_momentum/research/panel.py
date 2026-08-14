"""Daily panel loader + point-in-time universe (§2.2).

Source: the prebuilt daily panel `daily_local_is_v1` (all symbols stacked, one
row per symbol-day, already carrying lower-timeframe intraday features). We only
add causal universe construction on top of it — no forward information.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# multi-regime klines panel (built from klines_1d by build_daily_panel.py)
KLINES_DAILY_PANEL = ".output/market/binance_vision/um_futures/daily_klines_v1/panel.parquet"

# Default source + IS/OOS cutoff. Set env XSM_PANEL_GLOB / XSM_IS_END to flip every
# runner onto the multi-regime panel without editing each script. Frozen OOS begins
# at IS_END and is never read until the logic is frozen (§0.2).
DAILY_PANEL_GLOB = os.environ.get(
    "XSM_PANEL_GLOB", ".output/market/binance_vision/um_futures/daily_local_is_v1/*.parquet")
IS_END = pd.Timestamp(os.environ.get("XSM_IS_END", "2026-01-01"), tz="UTC")

# Stable / wrapped exclusions (§2.2). Matched against the base part of the
# `<BASE>USDT`/`<BASE>USDC` symbol.
_STABLE_BASES = {
    "USDC", "USDT", "TUSD", "FDUSD", "USDP", "DAI", "BUSD", "USDD", "GUSD",
    "USTC", "USDE", "SUSD", "FRAX", "LUSD", "PYUSD", "EURT", "EURI", "AEUR",
}
_WRAPPED_BASES = {"WBTC", "WETH", "WBETH", "WEETH", "STETH", "CBETH", "BETH"}


def _base_of(symbol: str) -> str:
    for quote in ("USDT", "USDC", "USD"):
        if symbol.endswith(quote):
            base = symbol[: -len(quote)]
            # strip a leading 1000.. denomination so 1000BONK -> BONK
            base = re.sub(r"^1000+", "", base)
            return base
    return symbol


def is_excluded_symbol(symbol: str) -> bool:
    base = _base_of(symbol)
    return base in _STABLE_BASES or base in _WRAPPED_BASES


def load_panel(is_only: bool = True, source_glob: str = DAILY_PANEL_GLOB,
               is_end: pd.Timestamp = IS_END) -> pd.DataFrame:
    """Load the stacked daily panel, complete bars only, sorted, IS-filtered.

    source_glob lets a caller point at the multi-regime klines panel instead of
    the original 7-month `daily_local_is_v1`. is_end is the frozen-OOS boundary.
    """
    files = sorted(glob.glob(source_glob))
    if not files:
        raise FileNotFoundError(f"no daily panel parquet at {source_glob}")
    frames = [pd.read_parquet(f) for f in files]
    panel = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    panel["date"] = pd.to_datetime(panel["date"], utc=True)
    if "complete_daily_bar" in panel.columns:
        panel = panel[panel["complete_daily_bar"].astype(bool)]
    panel = panel[~panel["symbol"].map(is_excluded_symbol)]
    if is_only:
        panel = panel[panel["date"] < is_end]
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    return panel


def pivot(panel: pd.DataFrame, column: str) -> pd.DataFrame:
    """date (index) x symbol (columns) matrix for one column."""
    mat = panel.pivot(index="date", columns="symbol", values=column)
    return mat.sort_index()


def build_universe_mask(
    panel: pd.DataFrame,
    quote_volume: pd.DataFrame,
    universe_n: int,
    liquidity_lb: int,
    min_age_days: int,
) -> pd.DataFrame:
    """Boolean date x symbol mask of the point-in-time liquid universe.

    On each date a symbol is admitted iff, using only trailing data:
      * it has >= min_age_days of prior observed bars,
      * its trailing median dollar volume ranks in the top `universe_n`.
    """
    close = pivot(panel, "close")
    # age = count of non-NaN closes strictly before the current row
    observed = close.notna()
    age = observed.cumsum().shift(1).fillna(0)

    med_dollar = quote_volume.rolling(liquidity_lb, min_periods=max(5, liquidity_lb // 2)).median()
    # only consider symbols old enough and currently trading
    eligible = observed & (age >= min_age_days) & med_dollar.notna()

    ranked = med_dollar.where(eligible)
    # rank within each date, largest volume = rank 1
    order = ranked.rank(axis=1, ascending=False, method="first")
    mask = order.le(universe_n) & eligible
    return mask.fillna(False)
