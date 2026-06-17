from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from anomaly_science.contracts.market import SymbolDayUniverseRow
from anomaly_science.contracts.time import utc_ms_to_datetime


def universe_rows_to_artifact(rows: list[SymbolDayUniverseRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def build_symbol_universe_by_day(
    *,
    candles_1m: pd.DataFrame | None,
    candles_5m: pd.DataFrame | None,
    open_interest_5m: pd.DataFrame | None,
    liquidations: pd.DataFrame | None,
) -> list[SymbolDayUniverseRow]:
    """Build a conservative point-in-time universe from available dated datasets.

    The function uses only rows present for each trade date. It does not import a
    current/future universe and does not assume missing historical data means a
    symbol was untradable in the real world; missing fields are represented as
    explicit eligibility reasons.
    """
    has_1m = _symbol_dates(candles_1m, time_column="open_time_ms")
    has_5m = _symbol_dates(candles_5m, time_column="open_time_ms")
    has_oi = _symbol_dates(open_interest_5m, time_column="timestamp_ms")
    has_liq = _symbol_dates(liquidations, time_column="event_time_ms")

    symbols_dates = set(has_1m) | set(has_5m) | set(has_oi) | set(has_liq)
    rows: list[SymbolDayUniverseRow] = []
    for trade_date, symbol in sorted(symbols_dates):
        one = (trade_date, symbol) in has_1m
        five = (trade_date, symbol) in has_5m
        oi = (trade_date, symbol) in has_oi
        liq = (trade_date, symbol) in has_liq
        reasons = []
        if not one:
            reasons.append("missing_1m_data")
        if not five:
            reasons.append("missing_5m_data")
        tradable = one and five
        rows.append(
            SymbolDayUniverseRow(
                trade_date=trade_date,
                symbol=symbol,
                listed_asof_day=one or five,
                delisted_asof_day=False,
                tradable_on_day=tradable,
                has_1m_data=one,
                has_5m_data=five,
                has_oi_data=oi,
                has_liquidation_data=liq,
                liquidity_eligible_on_day=tradable,
                reason_if_excluded=";".join(reasons),
            )
        )
    return rows


def _symbol_dates(frame: pd.DataFrame | None, *, time_column: str) -> set[tuple[str, str]]:
    if frame is None or frame.empty or "symbol" not in frame.columns or time_column not in frame.columns:
        return set()
    result: set[tuple[str, str]] = set()
    for _, row in frame[["symbol", time_column]].dropna().iterrows():
        trade_date = utc_ms_to_datetime(int(row[time_column])).date().isoformat()
        result.add((trade_date, str(row["symbol"])))
    return result
