from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import SpotTrendContractError
from .futures_breakout import attach_breakout_flow_features


FUTURES_FEATURE_SCHEMA_VERSION = "usdm_trend_features_v4_breakout_flow_is_2025"


@dataclass(frozen=True, slots=True)
class FuturesFeatureSpec:
    name: str
    family: str
    rationale: str


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return (numerator / denominator.where(denominator.abs() > 0)).replace([np.inf, -np.inf], np.nan)


def _rolling_slope_and_r2(values: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    time_index = pd.Series(np.arange(len(values), dtype=float), index=values.index)
    sum_y = values.rolling(window, min_periods=window).sum()
    sum_x = time_index.rolling(window, min_periods=window).sum()
    sum_xy = (values * time_index).rolling(window, min_periods=window).sum()
    sum_x2 = (time_index * time_index).rolling(window, min_periods=window).sum()
    sum_y2 = (values * values).rolling(window, min_periods=window).sum()
    covariance = sum_xy - sum_x * sum_y / window
    variance_x = sum_x2 - (sum_x * sum_x) / window
    variance_y = sum_y2 - (sum_y * sum_y) / window
    slope = _safe_divide(covariance, variance_x)
    r2 = _safe_divide(covariance * covariance, variance_x * variance_y).clip(0.0, 1.0)
    return slope, r2


def _streak(values: pd.Series) -> pd.Series:
    signs = np.sign(values.fillna(0.0).to_numpy(dtype=float))
    result = np.zeros(len(signs), dtype=float)
    for index, sign in enumerate(signs):
        if sign == 0:
            result[index] = 0.0
        elif index > 0 and np.sign(result[index - 1]) == sign:
            result[index] = result[index - 1] + sign
        else:
            result[index] = sign
    return pd.Series(result, index=values.index)


def _instrument_futures_features(group: pd.DataFrame, carry: pd.DataFrame) -> pd.DataFrame:
    ordered = group.sort_values("date", kind="stable").copy()
    symbol = str(ordered["symbol"].iloc[0])
    calendar = pd.date_range(ordered["date"].min(), ordered["date"].max(), freq="D", tz="UTC")
    frame = ordered.set_index("date").reindex(calendar).rename_axis("date").reset_index()
    frame["symbol"] = symbol
    if not carry.empty:
        symbol_carry = carry.loc[carry["symbol"].eq(symbol)].drop(columns="symbol")
        frame = frame.merge(symbol_carry, on="date", how="left", validate="one_to_one")
    close = frame["close"]
    open_price = frame["open"]
    high = frame["high"]
    low = frame["low"]
    log_close = np.log(close)
    log_return = log_close.diff()
    simple_return = close.pct_change(fill_method=None)
    frame["signed_return_streak"] = _streak(log_return)

    return_by_window: dict[int, pd.Series] = {}
    for window in (5, 10, 20, 60, 120, 250):
        return_by_window[window] = log_close - log_close.shift(window)
    frame["momentum_acceleration_5_20"] = return_by_window[5] - return_by_window[20] * 0.25
    frame["momentum_acceleration_20_60"] = return_by_window[20] - return_by_window[60] / 3.0
    frame["momentum_acceleration_60_120"] = return_by_window[60] - return_by_window[120] * 0.5
    for window in (10, 20, 60):
        path_length = log_return.abs().rolling(window, min_periods=window).sum()
        frame[f"efficiency_ratio_{window}"] = _safe_divide((log_close - log_close.shift(window)).abs(), path_length)
        frame[f"up_day_share_{window}"] = (log_return > 0).astype(float).rolling(window, min_periods=window).mean()
    for window in (10, 20, 60, 120):
        slope, r2 = _rolling_slope_and_r2(log_close, window)
        frame[f"log_price_slope_{window}"] = slope
        frame[f"log_price_trend_r2_{window}"] = r2
    for window in (20, 60, 120, 250):
        rolling_high = close.rolling(window, min_periods=window).max()
        frame[f"drawdown_from_high_{window}"] = _safe_divide(close, rolling_high) - 1.0
    drawdown_250 = _safe_divide(close, close.rolling(250, min_periods=1).max()) - 1.0
    for window in (20, 60):
        frame[f"ulcer_index_{window}"] = np.sqrt(
            (drawdown_250 * drawdown_250).rolling(window, min_periods=window).mean()
        )
        frame[f"return_autocorrelation_1_{window}"] = log_return.rolling(window, min_periods=window).corr(
            log_return.shift(1)
        )
    for window in (20, 60, 120):
        frame[f"return_skew_{window}"] = log_return.rolling(window, min_periods=window).skew()
        frame[f"return_kurtosis_{window}"] = log_return.rolling(window, min_periods=window).kurt()

    log_hl = np.log(_safe_divide(high, low))
    log_co = np.log(_safe_divide(close, open_price))
    previous_close = close.shift(1)
    log_oc = np.log(_safe_divide(open_price, previous_close))
    parkinson_daily = (log_hl * log_hl) / (4.0 * np.log(2.0))
    garman_klass_daily = 0.5 * (log_hl * log_hl) - (2.0 * np.log(2.0) - 1.0) * (log_co * log_co)
    rogers_satchell_daily = np.log(_safe_divide(high, close)) * np.log(_safe_divide(high, open_price)) + np.log(
        _safe_divide(low, close)
    ) * np.log(_safe_divide(low, open_price))
    squared_return = log_return * log_return
    bipower_daily = (np.pi / 2.0) * log_return.abs() * log_return.shift(1).abs()
    normalized_range = _safe_divide(high - low, close)
    close_location = _safe_divide(close - low, high - low)
    for window in (10, 20, 60):
        frame[f"parkinson_volatility_{window}"] = np.sqrt(
            parkinson_daily.rolling(window, min_periods=window).mean().clip(lower=0.0) * 365.0
        )
        frame[f"garman_klass_volatility_{window}"] = np.sqrt(
            garman_klass_daily.rolling(window, min_periods=window).mean().clip(lower=0.0) * 365.0
        )
        frame[f"rogers_satchell_volatility_{window}"] = np.sqrt(
            rogers_satchell_daily.rolling(window, min_periods=window).mean().clip(lower=0.0) * 365.0
        )
    for window in (20, 60):
        realized_variance = squared_return.rolling(window, min_periods=window).sum()
        bipower_variation = bipower_daily.rolling(window, min_periods=window).sum()
        frame[f"jump_variation_share_{window}"] = _safe_divide(
            (realized_variance - bipower_variation).clip(lower=0.0), realized_variance
        )
        frame[f"volatility_of_volatility_{window}"] = log_return.abs().rolling(
            window, min_periods=window
        ).std(ddof=1)
        frame[f"gap_volatility_{window}"] = log_oc.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(365.0)
        frame[f"close_location_mean_{window}"] = close_location.rolling(window, min_periods=window).mean()
    range_5 = normalized_range.rolling(5, min_periods=5).mean()
    range_20 = normalized_range.rolling(20, min_periods=20).mean()
    range_60 = normalized_range.rolling(60, min_periods=60).mean()
    frame["range_compression_5_20"] = _safe_divide(range_5, range_20)
    frame["range_compression_20_60"] = _safe_divide(range_20, range_60)
    frame["overnight_gap"] = log_oc
    frame["close_location"] = close_location

    quote_volume = frame["quote_volume"]
    trade_count = frame["number_of_trades"]
    taker_share = _safe_divide(frame["taker_buy_quote_volume"], quote_volume)
    taker_imbalance = 2.0 * taker_share - 1.0
    frame["log_quote_volume_current"] = np.log(quote_volume.where(quote_volume > 0))
    for window in (1, 5, 20):
        frame[f"quote_volume_change_{window}"] = np.log(quote_volume.where(quote_volume > 0)) - np.log(
            quote_volume.shift(window).where(quote_volume.shift(window) > 0)
        )
    for window in (20, 60):
        volume_mean = quote_volume.rolling(window, min_periods=window).mean()
        volume_std = quote_volume.rolling(window, min_periods=window).std(ddof=1)
        frame[f"quote_volume_zscore_{window}"] = _safe_divide(quote_volume - volume_mean, volume_std)
        trade_mean = trade_count.rolling(window, min_periods=window).mean()
        trade_std = trade_count.rolling(window, min_periods=window).std(ddof=1)
        frame[f"trade_count_zscore_{window}"] = _safe_divide(trade_count - trade_mean, trade_std)
    mean_trade_notional = _safe_divide(quote_volume, trade_count)
    frame["mean_trade_notional"] = mean_trade_notional
    frame["mean_trade_notional_zscore_20"] = _safe_divide(
        mean_trade_notional - mean_trade_notional.rolling(20, min_periods=20).mean(),
        mean_trade_notional.rolling(20, min_periods=20).std(ddof=1),
    )
    frame["taker_imbalance"] = taker_imbalance
    for window in (5, 20):
        frame[f"taker_imbalance_mean_{window}"] = taker_imbalance.rolling(window, min_periods=window).mean()
    frame["taker_imbalance_std_20"] = taker_imbalance.rolling(20, min_periods=20).std(ddof=1)
    signed_flow = taker_imbalance * quote_volume
    frame["signed_taker_flow_to_volume_20"] = _safe_divide(
        signed_flow, quote_volume.rolling(20, min_periods=20).median()
    )
    frame["signed_taker_flow_zscore_20"] = _safe_divide(
        signed_flow - signed_flow.rolling(20, min_periods=20).mean(),
        signed_flow.rolling(20, min_periods=20).std(ddof=1),
    )
    frame["flow_impact_proxy"] = _safe_divide(log_return.abs(), signed_flow.abs())
    frame["volume_volatility_interaction_20"] = frame["quote_volume_zscore_20"] * log_return.abs()

    if "open_interest_close" in frame:
        oi_close = frame["open_interest_close"].where(frame["open_interest_close"] > 0)
        log_oi = np.log(oi_close)
        frame["oi_log_level"] = log_oi
        for window in (1, 3, 5, 10, 20):
            frame[f"oi_log_change_{window}"] = log_oi - log_oi.shift(window)
        frame["oi_intraday_log_change"] = np.log(
            _safe_divide(frame["open_interest_close"], frame["open_interest_open"])
        )
        frame["oi_intraday_range"] = _safe_divide(
            frame["open_interest_high"] - frame["open_interest_low"],
            frame["open_interest_mean"],
        )
        for window in (20, 60):
            oi_mean = log_oi.rolling(window, min_periods=window).mean()
            oi_std = log_oi.rolling(window, min_periods=window).std(ddof=1)
            frame[f"oi_log_level_zscore_{window}"] = _safe_divide(log_oi - oi_mean, oi_std)
        oi_notional = oi_close * close
        frame["oi_log_notional"] = np.log(oi_notional.where(oi_notional > 0))
        frame["oi_notional_to_quote_volume"] = _safe_divide(oi_notional, quote_volume)
        frame["oi_change_5_minus_price_return_5"] = frame["oi_log_change_5"] - return_by_window[5]
        frame["price_oi_interaction_1"] = log_return * frame["oi_log_change_1"]
        frame["price_up_oi_up"] = ((log_return > 0) & (frame["oi_log_change_1"] > 0)).astype(float)
        frame["price_up_oi_down"] = ((log_return > 0) & (frame["oi_log_change_1"] < 0)).astype(float)
        frame["price_down_oi_up"] = ((log_return < 0) & (frame["oi_log_change_1"] > 0)).astype(float)
        frame["price_down_oi_down"] = ((log_return < 0) & (frame["oi_log_change_1"] < 0)).astype(float)
        realized_volatility_20 = log_return.rolling(20, min_periods=20).std(ddof=1)
        frame["oi_change_5_to_volatility_20"] = _safe_divide(
            frame["oi_log_change_5"], realized_volatility_20
        )
        if "oi_available_minutes" in frame and "minute_count" in frame:
            frame["oi_observation_coverage"] = _safe_divide(
                frame["oi_available_minutes"], frame["minute_count"]
            ).clip(0.0, 1.0)
            frame["oi_missing_share"] = _safe_divide(
                frame["missing_oi_minutes"], frame["minute_count"]
            ).clip(0.0, 1.0)

    if "funding_rate_sum" in frame:
        funding = frame["funding_rate_sum"]
        frame["funding_rate_sum_current"] = funding
        for window in (7, 30):
            frame[f"funding_rate_sum_{window}"] = funding.rolling(window, min_periods=window).sum()
            frame[f"funding_rate_mean_{window}"] = funding.rolling(window, min_periods=window).mean()
        frame["funding_rate_zscore_30"] = _safe_divide(
            funding - funding.rolling(30, min_periods=30).mean(),
            funding.rolling(30, min_periods=30).std(ddof=1),
        )
        frame["funding_rate_change_1"] = funding - funding.shift(1)
    if "premium_close" in frame:
        premium = frame["premium_close"]
        frame["premium_index_close"] = premium
        frame["premium_index_change_1"] = premium - premium.shift(1)
        frame["premium_index_change_5"] = premium - premium.shift(5)
        frame["premium_index_mean_7"] = premium.rolling(7, min_periods=7).mean()
        frame["premium_index_mean_30"] = premium.rolling(30, min_periods=30).mean()
        frame["premium_index_zscore_30"] = _safe_divide(
            premium - premium.rolling(30, min_periods=30).mean(),
            premium.rolling(30, min_periods=30).std(ddof=1),
        )
        frame["premium_intraday_range"] = frame["premium_high"] - frame["premium_low"]
        if "funding_rate_sum" in frame:
            frame["premium_minus_funding"] = premium - frame["funding_rate_sum"]
    return frame.loc[frame["close"].notna()].copy()


def _percentile_rank(series: pd.Series) -> pd.Series:
    count = series.notna().sum()
    if count == 0:
        return pd.Series(np.nan, index=series.index)
    if count == 1:
        output = pd.Series(np.nan, index=series.index)
        output.loc[series.notna()] = 0.5
        return output
    return series.rank(method="average", pct=True)


def build_futures_feature_matrix(
    base_features: pd.DataFrame,
    daily_bars: pd.DataFrame,
    *,
    carry: pd.DataFrame | None = None,
    breakout_events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if base_features.empty:
        return base_features.copy()
    symbols = set(base_features["symbol"])
    bars = daily_bars.loc[daily_bars["symbol"].isin(symbols)].copy()
    carry_frame = pd.DataFrame() if carry is None else carry.loc[carry["symbol"].isin(symbols)].copy()
    frames = [
        _instrument_futures_features(group, carry_frame)
        for _, group in bars.groupby("symbol", sort=False)
    ]
    extra = pd.concat(frames, ignore_index=True)
    identity = {"date", "symbol"}
    raw = {
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
        "available_time_ms",
        "premium_open",
        "premium_high",
        "premium_low",
        "premium_close",
        "funding_rate_sum",
        "funding_rate_mean",
        "funding_rate_max_abs",
        "funding_observation_count",
        "open_interest_open",
        "open_interest_close",
        "open_interest_low",
        "open_interest_high",
        "open_interest_mean",
        "open_interest_observations",
        "oi_available_minutes",
        "missing_oi_minutes",
        "minute_count",
        "complete_daily_bar",
    }
    extra_columns = [column for column in extra.columns if column in identity or column not in raw]
    frame = base_features.merge(extra[extra_columns], on=["date", "symbol"], how="left", validate="one_to_one")
    if breakout_events is not None:
        frame = attach_breakout_flow_features(frame, breakout_events)
    rank_sources = (
        "momentum_acceleration_5_20",
        "efficiency_ratio_20",
        "log_price_slope_20",
        "log_price_trend_r2_20",
        "drawdown_from_high_60",
        "parkinson_volatility_20",
        "jump_variation_share_20",
        "quote_volume_zscore_20",
        "mean_trade_notional",
        "taker_imbalance_mean_20",
        "signed_taker_flow_zscore_20",
        "funding_rate_sum_30",
        "premium_index_zscore_30",
        "oi_log_change_5",
        "oi_log_level_zscore_20",
        "oi_notional_to_quote_volume",
        "price_oi_interaction_1",
    )
    rank_features = {
        f"{source}_cs_rank": frame.groupby("date", group_keys=False)[source].apply(_percentile_rank)
        for source in rank_sources
        if source in frame
    }
    if rank_features:
        frame = pd.concat([frame, pd.DataFrame(rank_features, index=frame.index)], axis=1)
    daily_market: list[dict[str, object]] = []
    for day, cross_section in frame.groupby("date", sort=True):
        row: dict[str, object] = {"date": day}
        row["market_return_dispersion_1"] = float(cross_section["return_1"].std(ddof=1))
        row["market_return_dispersion_20"] = float(cross_section["return_20"].std(ddof=1))
        volume = cross_section["quote_volume"].clip(lower=0.0)
        volume_share = volume / volume.sum() if volume.sum() > 0 else volume * np.nan
        row["market_liquidity_hhi"] = float((volume_share * volume_share).sum())
        if "funding_rate_sum_current" in cross_section:
            row["market_median_funding"] = float(cross_section["funding_rate_sum_current"].median())
            row["market_positive_funding_share"] = float((cross_section["funding_rate_sum_current"] > 0).mean())
            row["market_funding_dispersion"] = float(cross_section["funding_rate_sum_current"].std(ddof=1))
        if "premium_index_close" in cross_section:
            row["market_median_premium"] = float(cross_section["premium_index_close"].median())
            row["market_premium_dispersion"] = float(cross_section["premium_index_close"].std(ddof=1))
        if "oi_log_change_1" in cross_section:
            row["market_median_oi_change_1"] = float(cross_section["oi_log_change_1"].median())
            row["market_oi_increase_share"] = float((cross_section["oi_log_change_1"] > 0).mean())
            row["market_oi_change_dispersion_1"] = float(cross_section["oi_log_change_1"].std(ddof=1))
        daily_market.append(row)
    frame = frame.merge(pd.DataFrame(daily_market), on="date", how="left", validate="many_to_one")
    frame["futures_feature_schema_version"] = FUTURES_FEATURE_SCHEMA_VERSION
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def futures_feature_catalog(frame: pd.DataFrame) -> tuple[FuturesFeatureSpec, ...]:
    base_exclusions = {
        "date",
        "symbol",
        "base_asset",
        "canonical_asset_id",
        "snapshot_date",
        "feature_cutoff_date",
        "feature_schema_version",
        "futures_feature_schema_version",
        "effective_date",
        "trading_start_date",
        "daily_log_return",
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
        "available_time_ms",
        "open_interest_open",
        "open_interest_close",
        "open_interest_low",
        "open_interest_high",
        "open_interest_mean",
        "open_interest_observations",
        "oi_available_minutes",
        "missing_oi_minutes",
        "minute_count",
        "complete_daily_bar",
        "universe_liquidity_rank",
        "liquidity_30_at_snapshot",
        "trend_state_changed",
        "new_breakout",
        "breakout_threshold",
        "future_start_date",
        "label_end_date",
        "future_open",
        "future_close",
        "forward_return",
        "forward_risk",
        "target",
        "target_horizon_days",
    }
    specs: list[FuturesFeatureSpec] = []
    for name in frame.columns:
        if name in base_exclusions or not pd.api.types.is_numeric_dtype(frame[name]):
            continue
        if "pre_breakout_baseline_days_" in name or "pre_breakout_window_complete_" in name:
            continue
        if name.startswith(("active_", "entered_", "exited_", "_market_")):
            continue
        if name.startswith("stop_") and not name.startswith("stop_distance_"):
            continue
        if any(token in name for token in ("future_", "target", "label_")):
            raise SpotTrendContractError(f"future/label field attempted to enter futures feature catalog: {name}")
        if name.startswith("oi_") or "_oi_" in name or name.startswith("price_oi_"):
            family = "open_interest_positioning"
            rationale = "Position build-up, crowding, and price/open-interest state known by the daily cutoff."
        elif any(
            token in name
            for token in (
                "funding",
                "premium",
            )
        ):
            family = "carry_basis"
            rationale = "Perpetual carry and basis crowding known by the daily cutoff."
        elif any(token in name for token in ("volume", "trade", "taker", "flow", "amihud", "liquidity")):
            family = "liquidity_flow"
            rationale = "Trading activity, aggressor balance, and causal price-impact proxies."
        elif any(
            token in name
            for token in (
                "vol_",
                "volatility",
                "variance",
                "range",
                "atr",
                "loss",
                "jump",
                "gap",
                "close_location",
                "intraday_return",
                "intraday_high",
                "intraday_low",
            )
        ):
            family = "volatility_range"
            rationale = "Realized risk, range estimators, jumps, and compression/expansion."
        elif name.startswith("market_") or name.startswith("btc_") or name.startswith("eth_") or name.endswith("_cs_rank"):
            family = "cross_section_market"
            rationale = "Point-in-time relative strength, breadth, concentration, and market regime."
        else:
            family = "trend_path"
            rationale = "Directional path geometry, persistence, breakout state, and momentum quality."
        specs.append(FuturesFeatureSpec(name=name, family=family, rationale=rationale))
    return tuple(specs)
