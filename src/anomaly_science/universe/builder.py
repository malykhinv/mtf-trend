from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

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

    The builder never imports a current/future symbol list. It derives every
    symbol lifecycle from data observed inside the historical snapshot, fills
    missing days between first and last observation as explicit non-tradable
    rows, and keeps listing/delisting confidence as data-source metadata rather
    than pretending to know exchange status that was not supplied.
    """
    has_1m = _symbol_dates(candles_1m, time_column="open_time_ms")
    has_5m = _symbol_dates(candles_5m, time_column="open_time_ms")
    has_oi = _symbol_dates(open_interest_5m, time_column="timestamp_ms")
    has_liq = _symbol_dates(liquidations, time_column="event_time_ms")
    observations = _symbol_time_bounds(
        candles_1m=(candles_1m, "open_time_ms"),
        candles_5m=(candles_5m, "open_time_ms"),
        open_interest_5m=(open_interest_5m, "timestamp_ms"),
        liquidations=(liquidations, "event_time_ms"),
    )

    rows: list[SymbolDayUniverseRow] = []
    for symbol, bounds in sorted(observations.items()):
        first_seen_ms, last_seen_ms = bounds
        first_date = utc_ms_to_datetime(first_seen_ms).date()
        last_date = utc_ms_to_datetime(last_seen_ms).date()
        for trade_day in _inclusive_date_range(first_date, last_date):
            trade_date = trade_day.isoformat()
            key = (trade_date, symbol)
            one = key in has_1m
            five = key in has_5m
            oi = key in has_oi
            liq = key in has_liq
            observed_on_day = one or five or oi or liq
            reasons = []
            if not one:
                reasons.append("missing_1m_data")
            if not five:
                reasons.append("missing_5m_data")
            tradable = one and five
            eligible_for_cross_section = tradable
            rows.append(
                SymbolDayUniverseRow(
                    trade_date=trade_date,
                    symbol=symbol,
                    listed_asof_day=True,
                    delisted_asof_day=False,
                    tradable_on_day=tradable,
                    has_1m_data=one,
                    has_5m_data=five,
                    has_oi_data=oi,
                    has_liquidation_data=liq,
                    liquidity_eligible_on_day=tradable,
                    eligible_for_cross_section=eligible_for_cross_section,
                    first_seen_data_time_ms=first_seen_ms,
                    last_seen_data_time_ms=last_seen_ms,
                    data_source_symbol_status="observed_on_day" if observed_on_day else "inferred_missing_day_between_observations",
                    listing_confidence="data_observed" if observed_on_day else "data_inferred_between_observations",
                    delisting_confidence="unknown_without_external_metadata",
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


def _symbol_time_bounds(**datasets: tuple[pd.DataFrame | None, str]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for frame, time_column in datasets.values():
        if frame is None or frame.empty or "symbol" not in frame.columns or time_column not in frame.columns:
            continue
        for _, row in frame[["symbol", time_column]].dropna().iterrows():
            symbol = str(row["symbol"])
            timestamp_ms = int(row[time_column])
            if symbol not in result:
                result[symbol] = (timestamp_ms, timestamp_ms)
                continue
            first_ms, last_ms = result[symbol]
            result[symbol] = (min(first_ms, timestamp_ms), max(last_ms, timestamp_ms))
    return result


def _inclusive_date_range(start: date, end: date) -> list[date]:
    if start > end:
        return []
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]
