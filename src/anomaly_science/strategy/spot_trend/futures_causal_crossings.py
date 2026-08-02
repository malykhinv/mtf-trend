from __future__ import annotations

import numpy as np
import pandas as pd
import polars as pl

from .contracts import SpotTrendContractError
from .futures_breakout import (
    BreakoutFlowConfig,
    _anomaly_statistics,
    _exact_window,
    _window_totals,
)
from .futures_data import IS_START, OOS_START


def _calendar_close(group: pd.DataFrame) -> pd.Series:
    ordered = group.sort_values("date", kind="stable")
    calendar = pd.date_range(ordered["date"].min(), ordered["date"].max(), freq="D", tz="UTC")
    return ordered.set_index("date")["close"].reindex(calendar)


def _pre_crossing_flow_row(
    minute: pd.DataFrame,
    crossing_timestamp_ms: int,
    config: BreakoutFlowConfig,
) -> dict[str, object]:
    row: dict[str, object] = {}
    for window_minutes in config.windows_minutes:
        current_window = _exact_window(minute, crossing_timestamp_ms, window_minutes)
        row[f"pre_crossing_window_complete_{window_minutes}m"] = current_window is not None
        if current_window is None:
            continue
        current = _window_totals(current_window)
        baselines: dict[str, list[float]] = {name: [] for name in current}
        for days_back in range(1, config.baseline_lookback_days + 1):
            historical_window = _exact_window(
                minute,
                crossing_timestamp_ms - days_back * 86_400_000,
                window_minutes,
            )
            if historical_window is None:
                continue
            historical = _window_totals(historical_window)
            for name, value in historical.items():
                baselines[name].append(value)
        baseline_count = min(len(values) for values in baselines.values())
        row[f"pre_crossing_baseline_days_{window_minutes}m"] = baseline_count
        for name, value in current.items():
            row[f"pre_crossing_{name}_{window_minutes}m"] = value
        row[f"pre_crossing_mean_trade_notional_{window_minutes}m"] = (
            current["quote_volume"] / current["trade_count"] if current["trade_count"] > 0 else np.nan
        )
        row[f"pre_crossing_taker_buy_share_{window_minutes}m"] = (
            current["taker_buy_quote_volume"] / current["quote_volume"]
            if current["quote_volume"] > 0
            else np.nan
        )
        if baseline_count < config.minimum_baseline_days:
            continue
        for name in ("quote_volume", "trade_count"):
            median, log_ratio, robust_z, percentile = _anomaly_statistics(current[name], baselines[name])
            prefix = f"pre_crossing_{name}_{window_minutes}m"
            row[f"{prefix}_baseline_median"] = median
            row[f"{prefix}_log_ratio"] = log_ratio
            row[f"{prefix}_robust_z"] = robust_z
            row[f"{prefix}_percentile"] = percentile
    return row


def build_all_causal_crossings(
    daily_bars: pd.DataFrame,
    trend_state: pd.DataFrame,
    membership_rows: pd.DataFrame,
    horizons: tuple[int, ...],
    *,
    config: BreakoutFlowConfig = BreakoutFlowConfig(),
) -> pd.DataFrame:
    """Build every first intraday crossing knowable online, including later failures."""

    members = membership_rows[["date", "symbol"]].drop_duplicates().copy()
    members["date"] = pd.to_datetime(members["date"], utc=True).dt.normalize()
    symbols = sorted(set(members["symbol"]))
    trend = trend_state.copy()
    trend["date"] = pd.to_datetime(trend["date"], utc=True).dt.normalize()
    trend_index = trend.set_index(["date", "symbol"])
    bar_groups = {
        symbol: group.sort_values("date", kind="stable").copy()
        for symbol, group in daily_bars.loc[daily_bars["symbol"].isin(symbols)].groupby("symbol", sort=True)
    }
    start_ms = int(pd.Timestamp(IS_START, tz="UTC").timestamp() * 1_000)
    end_ms = int(pd.Timestamp(OOS_START, tz="UTC").timestamp() * 1_000)
    outputs: list[dict[str, object]] = []
    for symbol in symbols:
        if symbol not in bar_groups:
            raise SpotTrendContractError(f"universe member {symbol} has no daily bars")
        path = config.source_dir / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        minute = (
            pl.scan_parquet(path)
            .filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
            .select(
                "timestamp",
                "close",
                "volume",
                "quote_volume",
                "trade_count",
                "taker_buy_quote_volume",
            )
            .sort("timestamp")
            .collect()
            .to_pandas()
        )
        timestamps = minute["timestamp"].to_numpy(dtype=np.int64)
        closes = _calendar_close(bar_groups[symbol])
        symbol_dates = members.loc[members["symbol"].eq(symbol), "date"].sort_values()
        for day in symbol_dates:
            prior_day = day - pd.Timedelta(days=1)
            prior_key = (prior_day, symbol)
            if prior_key not in trend_index.index or day not in closes.index:
                continue
            prior_state = trend_index.loc[prior_key]
            eligible: list[tuple[int, float]] = []
            for horizon in horizons:
                if bool(prior_state[f"active_{horizon}"]):
                    continue
                history = closes.loc[day - pd.Timedelta(days=horizon) : prior_day]
                if len(history) != horizon or history.isna().any():
                    continue
                eligible.append((horizon, float(history.max())))
            if not eligible:
                continue
            day_start_ms = int(day.timestamp() * 1_000)
            day_end_ms = day_start_ms + 86_400_000
            left = int(np.searchsorted(timestamps, day_start_ms, side="left"))
            right = int(np.searchsorted(timestamps, day_end_ms, side="left"))
            day_minutes = minute.iloc[left:right]
            if day_minutes.empty:
                continue
            crossing_candidates: list[tuple[int, int, float]] = []
            for horizon, threshold in eligible:
                crossed = day_minutes.loc[day_minutes["close"].gt(threshold)]
                if not crossed.empty:
                    crossing_candidates.append((int(crossed.iloc[0]["timestamp"]), horizon, threshold))
            if not crossing_candidates:
                continue
            crossing_timestamp, trigger_horizon, threshold = min(
                crossing_candidates, key=lambda item: (item[0], item[2], item[1])
            )
            crossing_record = day_minutes.loc[day_minutes["timestamp"].eq(crossing_timestamp)].iloc[0]
            crossing_close = float(crossing_record["close"])
            crossed_at_snapshot = [
                horizon for horizon, level in eligible if crossing_close > level
            ]
            snapshot_time = pd.to_datetime(crossing_timestamp + 60_000, unit="ms", utc=True)
            current_key = (day, symbol)
            current_state = trend_index.loc[current_key] if current_key in trend_index.index else None
            daily_confirmed = bool(
                current_state is not None
                and any(bool(current_state[f"entered_{horizon}"]) for horizon in crossed_at_snapshot)
            )
            row: dict[str, object] = {
                "event_id": f"{symbol}:{day.date()}:{crossing_timestamp}",
                "date": day,
                "symbol": symbol,
                "crossing_timestamp": pd.to_datetime(crossing_timestamp, unit="ms", utc=True),
                "snapshot_time": snapshot_time,
                "feature_cutoff_time": snapshot_time,
                "snapshot_close": crossing_close,
                "breakout_threshold": threshold,
                "trigger_horizon": trigger_horizon,
                "eligible_horizon_count": len(eligible),
                "crossed_horizon_count_at_snapshot": len(crossed_at_snapshot),
                "crossing_minute_of_day": (crossing_timestamp % 86_400_000) // 60_000,
                "daily_close_confirmed": daily_confirmed,
            }
            row.update(_pre_crossing_flow_row(minute, crossing_timestamp, config))
            outputs.append(row)
    result = pd.DataFrame(outputs)
    if result.empty:
        raise SpotTrendContractError("all-crossings builder produced no causal IS events")
    if result["feature_cutoff_time"].ne(result["snapshot_time"]).any():
        raise SpotTrendContractError("causal crossing features extend beyond their snapshot")
    return result.sort_values(["snapshot_time", "symbol"], kind="stable").reset_index(drop=True)
