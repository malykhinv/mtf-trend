from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .contracts import DAY_COUNT, DEFAULT_HORIZONS, FEATURE_SCHEMA_VERSION, SpotTrendContractError
from .trend import build_trend_state
from .universe import PointInTimeUniverse, validate_daily_bars


RETURN_WINDOWS: tuple[int, ...] = (1, 3, 5, 10, 20, 60, 120, 250)
RANGE_WINDOWS: tuple[int, ...] = (10, 20, 60, 120, 250)
VOLATILITY_WINDOWS: tuple[int, ...] = (10, 20, 60, 90)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.where(denominator.abs() > 0)
    return result.replace([np.inf, -np.inf], np.nan)


def _percentile_rank(series: pd.Series) -> pd.Series:
    present = series.notna().sum()
    if present == 0:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    if present == 1:
        result = pd.Series(np.nan, index=series.index, dtype="float64")
        result.loc[series.notna()] = 0.5
        return result
    return series.rank(method="average", pct=True)


def _rms_downside(values: np.ndarray) -> float:
    if not np.isfinite(values).all():
        return np.nan
    downside = np.minimum(values, 0.0)
    return float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(DAY_COUNT))


def _instrument_feature_history(group: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    ordered = group.sort_values("date", kind="stable").copy()
    symbol = str(ordered["symbol"].iloc[0])
    calendar = pd.date_range(ordered["date"].min(), ordered["date"].max(), freq="D", tz="UTC")
    frame = ordered.set_index("date").reindex(calendar).rename_axis("date").reset_index()
    frame["symbol"] = symbol
    close = frame["close"]
    log_close = np.log(close)
    log_return = log_close.diff()
    frame["daily_log_return"] = log_return
    for window in RETURN_WINDOWS:
        frame[f"return_{window}"] = log_close - log_close.shift(window)
    frame["_market_return_200"] = log_close - log_close.shift(200)
    for window in RANGE_WINDOWS:
        rolling_max = close.rolling(window, min_periods=window).max()
        rolling_min = close.rolling(window, min_periods=window).min()
        sma = close.rolling(window, min_periods=window).mean()
        frame[f"close_to_max_{window}"] = _safe_ratio(close, rolling_max) - 1.0
        frame[f"close_to_min_{window}"] = _safe_ratio(close, rolling_min) - 1.0
        frame[f"close_to_sma_{window}"] = _safe_ratio(close, sma) - 1.0
        frame[f"range_position_{window}"] = _safe_ratio(close - rolling_min, rolling_max - rolling_min)
    frame["_market_close_to_sma_50"] = _safe_ratio(close, close.rolling(50, min_periods=50).mean()) - 1.0
    frame["_market_close_to_sma_200"] = _safe_ratio(close, close.rolling(200, min_periods=200).mean()) - 1.0
    for window in VOLATILITY_WINDOWS:
        frame[f"vol_{window}"] = log_return.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(DAY_COUNT)
    frame["vol10_to_vol90"] = _safe_ratio(frame["vol_10"], frame["vol_90"])
    frame["vol20_to_vol90"] = _safe_ratio(frame["vol_20"], frame["vol_90"])
    frame["downside_vol_20"] = log_return.rolling(20, min_periods=20).apply(_rms_downside, raw=True)
    frame["downside_vol_90"] = log_return.rolling(90, min_periods=90).apply(_rms_downside, raw=True)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr14_to_close"] = _safe_ratio(true_range.rolling(14, min_periods=14).mean(), close)
    normalized_range = _safe_ratio(frame["high"] - frame["low"], close)
    frame["mean_range_10"] = normalized_range.rolling(10, min_periods=10).mean()
    frame["mean_range_30"] = normalized_range.rolling(30, min_periods=30).mean()
    frame["maximum_daily_loss_30"] = (-log_return).clip(lower=0).rolling(30, min_periods=30).max()
    frame["maximum_daily_loss_90"] = (-log_return).clip(lower=0).rolling(90, min_periods=90).max()

    for window in (7, 30, 90):
        median_volume = frame["quote_volume"].rolling(window, min_periods=window).median()
        frame[f"median_quote_volume_{window}"] = median_volume
        frame[f"log_median_quote_volume_{window}"] = np.log(median_volume.where(median_volume > 0))
    frame["quote_volume_7_to_30"] = _safe_ratio(
        frame["median_quote_volume_7"], frame["median_quote_volume_30"]
    )
    frame["quote_volume_30_to_90"] = _safe_ratio(
        frame["median_quote_volume_30"], frame["median_quote_volume_90"]
    )
    volume_mean = frame["quote_volume"].rolling(30, min_periods=30).mean()
    volume_std = frame["quote_volume"].rolling(30, min_periods=30).std(ddof=1)
    frame["quote_volume_zscore_30"] = _safe_ratio(frame["quote_volume"] - volume_mean, volume_std)
    frame["trade_count"] = frame["number_of_trades"]
    frame["taker_buy_quote_share"] = _safe_ratio(frame["taker_buy_quote_volume"], frame["quote_volume"])
    amihud = _safe_ratio(log_return.abs(), frame["quote_volume"])
    frame["amihud_20"] = amihud.rolling(20, min_periods=20).mean()
    return frame.loc[frame["close"].notna()].copy()


def _market_features(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, cross_section in frame.groupby("date", sort=True):
        row: dict[str, object] = {"date": day}
        for asset in ("BTC", "ETH"):
            selected = cross_section.loc[cross_section["base_asset"].eq(asset)]
            prefix = asset.lower()
            for window in ((5, 20, 60, 200) if asset == "BTC" else (20, 60)):
                source = "_market_return_200" if window == 200 else f"return_{window}"
                row[f"{prefix}_return_{window}"] = (
                    float(selected.iloc[0][source]) if len(selected) else np.nan
                )
            if asset == "BTC":
                row["btc_close_to_sma_200"] = (
                    float(selected.iloc[0]["_market_close_to_sma_200"]) if len(selected) else np.nan
                )
                row["btc_vol_20"] = float(selected.iloc[0]["vol_20"]) if len(selected) else np.nan
                row["btc_vol_90"] = float(selected.iloc[0]["vol_90"]) if len(selected) else np.nan
        for window in (1, 5, 20):
            row[f"universe_median_return_{window}"] = float(cross_section[f"return_{window}"].median())
        row["universe_share_above_sma_50"] = float((cross_section["_market_close_to_sma_50"] > 0).mean())
        row["universe_share_above_sma_200"] = float((cross_section["_market_close_to_sma_200"] > 0).mean())
        row["universe_share_positive_return_20"] = float((cross_section["return_20"] > 0).mean())
        row["universe_median_vol_20"] = float(cross_section["vol_20"].median())
        rows.append(row)
    return pd.DataFrame(rows)


def build_feature_matrix(
    daily_bars: pd.DataFrame,
    universe: PointInTimeUniverse,
    trend_state: pd.DataFrame | None = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Build point-in-time instrument and market features for active members."""

    bars = validate_daily_bars(daily_bars)
    if universe.daily_membership.empty:
        return pd.DataFrame()
    trend = build_trend_state(bars, horizons) if trend_state is None else trend_state.copy()
    history = pd.concat(
        [_instrument_feature_history(group, horizons) for _, group in bars.groupby("symbol", sort=False)],
        ignore_index=True,
    )
    frame = history.merge(trend, on=["date", "symbol"], how="inner", validate="one_to_one")
    membership = universe.daily_membership.rename(columns={"liquidity_rank": "universe_liquidity_rank"})
    frame = frame.merge(membership, on=["date", "symbol"], how="inner", validate="many_to_one")
    if frame.empty:
        return frame
    frame["age_days"] = (
        (frame["date"] - frame["trading_start_date"]).dt.days.clip(lower=0, upper=2_000).astype(float)
    )
    frame["liquidity_rank"] = frame.groupby("date")["median_quote_volume_30"].rank(
        method="first", ascending=False
    )
    for window in (5, 20, 60, 120):
        frame[f"return_{window}_percentile"] = frame.groupby("date", group_keys=False)[f"return_{window}"].apply(
            _percentile_rank
        )
    frame["volatility_percentile"] = frame.groupby("date", group_keys=False)["vol_20"].apply(_percentile_rank)
    frame["liquidity_percentile"] = frame.groupby("date", group_keys=False)["median_quote_volume_30"].apply(
        _percentile_rank
    )
    market = _market_features(frame)
    frame = frame.merge(market, on="date", how="left", validate="many_to_one")
    frame["snapshot_date"] = frame["date"]
    frame["feature_cutoff_date"] = frame["date"]
    frame["feature_schema_version"] = FEATURE_SCHEMA_VERSION
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def model_feature_columns(frame: pd.DataFrame) -> tuple[str, ...]:
    columns: list[str] = []
    columns.extend(f"return_{window}" for window in RETURN_WINDOWS)
    for window in RANGE_WINDOWS:
        columns.extend(
            (
                f"close_to_max_{window}",
                f"close_to_min_{window}",
                f"close_to_sma_{window}",
                f"range_position_{window}",
            )
        )
    columns.extend(
        (
            "trend_signal",
            "active_count",
            "minimum_active_horizon",
            "maximum_active_horizon",
            "days_since_new_breakout",
        )
    )
    columns.extend(f"stop_distance_{horizon}" for horizon in DEFAULT_HORIZONS)
    columns.extend(("minimum_stop_distance", "mean_stop_distance"))
    columns.extend(f"vol_{window}" for window in VOLATILITY_WINDOWS)
    columns.extend(
        (
            "vol10_to_vol90",
            "vol20_to_vol90",
            "downside_vol_20",
            "downside_vol_90",
            "atr14_to_close",
            "mean_range_10",
            "mean_range_30",
            "maximum_daily_loss_30",
            "maximum_daily_loss_90",
            "log_median_quote_volume_7",
            "log_median_quote_volume_30",
            "log_median_quote_volume_90",
            "quote_volume_7_to_30",
            "quote_volume_30_to_90",
            "quote_volume_zscore_30",
            "trade_count",
            "taker_buy_quote_share",
            "amihud_20",
            "age_days",
            "liquidity_rank",
            "return_5_percentile",
            "return_20_percentile",
            "return_60_percentile",
            "return_120_percentile",
            "volatility_percentile",
            "liquidity_percentile",
            "btc_return_5",
            "btc_return_20",
            "btc_return_60",
            "btc_return_200",
            "btc_close_to_sma_200",
            "btc_vol_20",
            "btc_vol_90",
            "eth_return_20",
            "eth_return_60",
            "universe_median_return_1",
            "universe_median_return_5",
            "universe_median_return_20",
            "universe_share_above_sma_50",
            "universe_share_above_sma_200",
            "universe_share_positive_return_20",
            "universe_median_vol_20",
        )
    )
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise SpotTrendContractError(f"feature matrix is missing frozen model features: {missing}")
    return tuple(columns)
