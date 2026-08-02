from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from .contracts import SpotTrendContractError
from .futures_data import IS_END, IS_START


@dataclass(frozen=True, slots=True)
class BreakoutFlowConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    windows_minutes: tuple[int, ...] = (5, 15, 30, 60)
    baseline_lookback_days: int = 30
    minimum_baseline_days: int = 10

    def __post_init__(self) -> None:
        if self.windows_minutes != (5, 15, 30, 60):
            raise SpotTrendContractError("breakout flow windows are frozen at 5/15/30/60 minutes")
        if self.baseline_lookback_days != 30 or self.minimum_baseline_days != 10:
            raise SpotTrendContractError("breakout anomaly baseline is frozen at 30 days with 10 required")


def _breakout_candidates(
    daily_bars: pd.DataFrame,
    trend_state: pd.DataFrame,
    horizons: tuple[int, ...],
    eligible_symbols: set[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    entered_columns = {horizon: f"entered_{horizon}" for horizon in horizons}
    missing = [name for name in entered_columns.values() if name not in trend_state]
    if missing:
        raise SpotTrendContractError(f"trend state missing breakout flags: {missing}")
    indexed_trend = trend_state.set_index(["date", "symbol"])
    for symbol, group in daily_bars.loc[daily_bars["symbol"].isin(eligible_symbols)].groupby(
        "symbol", sort=True
    ):
        ordered = group.sort_values("date", kind="stable").copy()
        thresholds = {
            horizon: ordered["close"].shift(1).rolling(horizon, min_periods=horizon).max()
            for horizon in horizons
        }
        for position, market_row in ordered.reset_index(drop=True).iterrows():
            key = (market_row["date"], symbol)
            if key not in indexed_trend.index:
                continue
            state = indexed_trend.loc[key]
            entered = [horizon for horizon, name in entered_columns.items() if bool(state[name])]
            if not entered:
                continue
            levels = [(horizon, float(thresholds[horizon].iloc[position])) for horizon in entered]
            if not all(np.isfinite(level) for _, level in levels):
                raise SpotTrendContractError(f"non-finite Donchian threshold for {symbol} {market_row['date']}")
            trigger_horizon, trigger_level = min(levels, key=lambda item: (item[1], item[0]))
            rows.append(
                {
                    "date": market_row["date"],
                    "symbol": symbol,
                    "breakout_threshold": trigger_level,
                    "breakout_trigger_horizon": trigger_horizon,
                    "breakout_entered_horizon_count": len(entered),
                }
            )
    return pd.DataFrame(rows)


def _exact_window(frame: pd.DataFrame, end_timestamp_ms: int, window: int) -> pd.DataFrame | None:
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    end_position = int(np.searchsorted(timestamps, end_timestamp_ms, side="left"))
    if end_position < window or end_position >= len(timestamps) or timestamps[end_position] != end_timestamp_ms:
        return None
    selected = frame.iloc[end_position - window : end_position]
    expected = np.arange(end_timestamp_ms - window * 60_000, end_timestamp_ms, 60_000, dtype=np.int64)
    if not np.array_equal(selected["timestamp"].to_numpy(dtype=np.int64), expected):
        return None
    return selected


def _window_totals(window: pd.DataFrame) -> dict[str, float]:
    return {
        "quote_volume": float(window["quote_volume"].sum()),
        "base_volume": float(window["volume"].sum()),
        "trade_count": float(window["trade_count"].sum()),
        "taker_buy_quote_volume": float(window["taker_buy_quote_volume"].sum()),
    }


def _anomaly_statistics(current: float, history: list[float]) -> tuple[float, float, float, float]:
    historical = np.asarray(history, dtype=float)
    median_raw = float(np.median(historical))
    log_history = np.log1p(historical)
    log_current = float(np.log1p(current))
    median_log = float(np.median(log_history))
    mad = float(np.median(np.abs(log_history - median_log)))
    log_ratio = log_current - median_log
    robust_z = (log_current - median_log) / (1.4826 * mad) if mad > 0 else np.nan
    percentile = float((1 + np.count_nonzero(historical <= current)) / (len(historical) + 1))
    return median_raw, log_ratio, robust_z, percentile


def build_breakout_flow_features(
    daily_bars: pd.DataFrame,
    trend_state: pd.DataFrame,
    horizons: tuple[int, ...],
    eligible_symbols: set[str],
    *,
    config: BreakoutFlowConfig = BreakoutFlowConfig(),
) -> pd.DataFrame:
    """Measure strictly pre-crossing 1m flow against prior same-clock days."""

    candidates = _breakout_candidates(daily_bars, trend_state, horizons, eligible_symbols)
    if candidates.empty:
        return candidates
    start_ms = int(pd.Timestamp(IS_START, tz="UTC").timestamp() * 1_000)
    end_ms = int((pd.Timestamp(IS_END, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1_000)
    outputs: list[dict[str, object]] = []
    for symbol, symbol_events in candidates.groupby("symbol", sort=True):
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
        if minute.empty:
            raise SpotTrendContractError(f"missing calendar-2025 minute rows for breakout symbol {symbol}")
        timestamp_values = minute["timestamp"].to_numpy(dtype=np.int64)
        for event in symbol_events.itertuples(index=False):
            day_start_ms = int(pd.Timestamp(event.date).timestamp() * 1_000)
            day_end_ms = day_start_ms + 86_400_000
            left = int(np.searchsorted(timestamp_values, day_start_ms, side="left"))
            right = int(np.searchsorted(timestamp_values, day_end_ms, side="left"))
            day = minute.iloc[left:right]
            crossings = day.loc[day["close"].gt(float(event.breakout_threshold))]
            if crossings.empty:
                raise SpotTrendContractError(
                    f"daily-confirmed breakout has no minute crossing: {symbol} {event.date}"
                )
            crossing_timestamp = int(crossings.iloc[0]["timestamp"])
            row: dict[str, object] = {
                "date": event.date,
                "symbol": symbol,
                "breakout_timestamp": pd.to_datetime(crossing_timestamp, unit="ms", utc=True),
                "breakout_threshold": float(event.breakout_threshold),
                "breakout_minute_of_day": (crossing_timestamp % 86_400_000) // 60_000,
                "breakout_trigger_horizon": int(event.breakout_trigger_horizon),
                "breakout_entered_horizon_count": int(event.breakout_entered_horizon_count),
            }
            for window_minutes in config.windows_minutes:
                current_window = _exact_window(minute, crossing_timestamp, window_minutes)
                row[f"pre_breakout_window_complete_{window_minutes}m"] = current_window is not None
                if current_window is None:
                    continue
                current = _window_totals(current_window)
                baselines: dict[str, list[float]] = {name: [] for name in current}
                for days_back in range(1, config.baseline_lookback_days + 1):
                    historical_window = _exact_window(
                        minute,
                        crossing_timestamp - days_back * 86_400_000,
                        window_minutes,
                    )
                    if historical_window is None:
                        continue
                    historical = _window_totals(historical_window)
                    for name, value in historical.items():
                        baselines[name].append(value)
                baseline_count = min(len(values) for values in baselines.values())
                row[f"pre_breakout_baseline_days_{window_minutes}m"] = baseline_count
                for name, value in current.items():
                    row[f"pre_breakout_{name}_{window_minutes}m"] = value
                row[f"pre_breakout_mean_trade_notional_{window_minutes}m"] = (
                    current["quote_volume"] / current["trade_count"] if current["trade_count"] > 0 else np.nan
                )
                row[f"pre_breakout_taker_buy_share_{window_minutes}m"] = (
                    current["taker_buy_quote_volume"] / current["quote_volume"]
                    if current["quote_volume"] > 0
                    else np.nan
                )
                if baseline_count < config.minimum_baseline_days:
                    continue
                for name in ("quote_volume", "trade_count"):
                    median, log_ratio, robust_z, percentile = _anomaly_statistics(current[name], baselines[name])
                    prefix = f"pre_breakout_{name}_{window_minutes}m"
                    row[f"{prefix}_baseline_median"] = median
                    row[f"{prefix}_log_ratio"] = log_ratio
                    row[f"{prefix}_robust_z"] = robust_z
                    row[f"{prefix}_percentile"] = percentile
            outputs.append(row)
    result = pd.DataFrame(outputs)
    return result.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def attach_breakout_flow_features(base_features: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return base_features.copy()
    direct = base_features.merge(events, on=["date", "symbol"], how="left", validate="one_to_one")
    carry_columns = [
        column
        for column in events.columns
        if column.startswith("pre_breakout_") and not column.startswith("pre_breakout_window_complete_")
    ]
    carried_parts: list[pd.DataFrame] = []
    event_subset = events[["date", "symbol", *carry_columns]].rename(columns={"date": "last_breakout_date"})
    for symbol, feature_group in direct.groupby("symbol", sort=False):
        current = feature_group.sort_values("date", kind="stable").copy()
        history = event_subset.loc[event_subset["symbol"].eq(symbol)].sort_values("last_breakout_date")
        if history.empty:
            missing_columns = pd.DataFrame(
                {f"last_{column}": np.nan for column in carry_columns}, index=current.index
            )
            missing_columns["days_since_last_breakout_flow_event"] = np.nan
            current = pd.concat([current, missing_columns], axis=1)
        else:
            current = pd.merge_asof(
                current.sort_values("date"),
                history.drop(columns="symbol"),
                left_on="date",
                right_on="last_breakout_date",
                direction="backward",
                allow_exact_matches=True,
                suffixes=("", "_last"),
            )
            current = current.rename(
                columns={f"{column}_last": f"last_{column}" for column in carry_columns}
            )
            current["days_since_last_breakout_flow_event"] = (
                current["date"] - current["last_breakout_date"]
            ).dt.days.astype(float)
            current = current.drop(columns="last_breakout_date")
        carried_parts.append(current)
    return pd.concat(carried_parts, ignore_index=True).sort_values(["date", "symbol"], kind="stable")
