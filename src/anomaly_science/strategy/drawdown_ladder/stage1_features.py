"""Point-in-time Stage-1 features and recovery labels for ladder states."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from anomaly_science.market_context.sessions import MS_PER_MINUTE, UTC_SESSION_BY_SEQ
from anomaly_science.strategy.drawdown_ladder.data import read_is_symbol_minutes
from anomaly_science.strategy.drawdown_ladder.stage1_spec import (
    DrawdownLadderStage1Spec,
    build_stage1_feature_catalog,
)


class Stage1FeatureError(ValueError):
    """Raised when a Stage-1 row cannot preserve its point-in-time contract."""


@dataclass(frozen=True, slots=True)
class PreparedBTCFrame:
    """Read-only BTC arrays prepared once per Stage-1 worker."""

    timestamp: np.ndarray
    close: np.ndarray
    log_return: np.ndarray
    index_by_open_time: dict[int, int]
    oi_change: dict[int, np.ndarray]
    taker_imbalance: dict[int, np.ndarray]
    range_activity_60m: np.ndarray
    quote_activity_60m_vs_24h: np.ndarray


_RAW_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "trade_count",
    "taker_buy_quote_volume",
    "open_interest",
    "long_liquidations_vol",
    "short_liquidations_vol",
    "oi_available",
    "liquidation_available",
)


def _finite(value: object) -> float:
    numeric = float(value)
    return numeric if math.isfinite(numeric) else math.nan


def _safe_ratio(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0.0:
        return math.nan
    return numerator / denominator


def _contiguous_window(
    values: np.ndarray,
    timestamps: np.ndarray,
    *,
    end_index: int,
    length: int,
) -> np.ndarray | None:
    start = end_index - length + 1
    if start < 0:
        return None
    if int(timestamps[end_index] - timestamps[start]) != (length - 1) * MS_PER_MINUTE:
        return None
    window = values[start : end_index + 1]
    return window if bool(np.isfinite(window).all()) else None


def _return_at(
    close: np.ndarray,
    timestamps: np.ndarray,
    index: int,
    lag: int,
) -> float:
    previous = index - lag
    if previous < 0 or timestamps[index] - timestamps[previous] != lag * MS_PER_MINUTE:
        return math.nan
    if not close[previous] > 0.0 or not math.isfinite(close[previous]) or not math.isfinite(close[index]):
        return math.nan
    return float(close[index] / close[previous] - 1.0)


def _rolling_sum(
    values: np.ndarray,
    timestamps: np.ndarray,
    index: int,
    window: int,
) -> float:
    selected = _contiguous_window(values, timestamps, end_index=index, length=window)
    return math.nan if selected is None else float(np.sum(selected))


def _rolling_correlation(left: np.ndarray, right: np.ndarray) -> float:
    valid = np.isfinite(left) & np.isfinite(right)
    if int(valid.sum()) < max(10, int(len(left) * 0.8)):
        return math.nan
    x, y = left[valid], right[valid]
    if float(np.std(x)) <= 0.0 or float(np.std(y)) <= 0.0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def _ema_arrays(close: np.ndarray, periods: tuple[int, ...]) -> dict[int, np.ndarray]:
    series = pd.Series(close)
    return {
        period: series.ewm(span=period, adjust=False, min_periods=period).mean().to_numpy(float)
        for period in periods
    }


def _session_activity_ratio(
    values: np.ndarray,
    timestamps: np.ndarray,
    *,
    index: int,
    window: int,
) -> float:
    current = _rolling_sum(values, timestamps, index, window)
    if not math.isfinite(current):
        return math.nan
    prior: list[float] = []
    for day in range(1, 8):
        prior_index = index - day * 1440
        if prior_index < 0:
            continue
        if timestamps[index] - timestamps[prior_index] != day * 1440 * MS_PER_MINUTE:
            continue
        value = _rolling_sum(values, timestamps, prior_index, window)
        if math.isfinite(value) and value > 0.0:
            prior.append(value)
    if len(prior) < 3:
        return math.nan
    return current / float(np.mean(prior))


def _activity_features(
    *,
    row: dict[str, object],
    index: int,
    timestamps: np.ndarray,
    quote: np.ndarray,
    trade_count: np.ndarray,
    taker_quote: np.ndarray,
    windows: tuple[int, ...],
) -> None:
    for window in windows:
        quote_sum = _rolling_sum(quote, timestamps, index, window)
        trade_sum = _rolling_sum(trade_count, timestamps, index, window)
        taker_sum = _rolling_sum(taker_quote, timestamps, index, window)
        row[f"quote_volume_{window}m"] = quote_sum
        prior_stop = index - window + 1
        prior_start = prior_stop - 1440
        if prior_start >= 0 and timestamps[prior_stop] - timestamps[prior_start] == 1440 * MS_PER_MINUTE:
            prior = quote[prior_start:prior_stop]
            prior_mean = float(np.mean(prior)) if bool(np.isfinite(prior).all()) else math.nan
        else:
            prior_mean = math.nan
        row[f"quote_activity_{window}m_vs_24h"] = _safe_ratio(
            quote_sum,
            prior_mean * window,
        )
        row[f"trade_count_{window}m"] = trade_sum
        row[f"average_trade_notional_{window}m"] = _safe_ratio(quote_sum, trade_sum)
        row[f"taker_imbalance_{window}m"] = (
            2.0 * taker_sum / quote_sum - 1.0
            if math.isfinite(taker_sum) and math.isfinite(quote_sum) and quote_sum > 0.0
            else math.nan
        )


def _path_features(
    *,
    row: dict[str, object],
    index: int,
    timestamps: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    log_return: np.ndarray,
    spec: DrawdownLadderStage1Spec,
) -> None:
    for lag in spec.return_lags_minutes:
        value = _return_at(close, timestamps, index, lag)
        earlier = _return_at(close, timestamps, index - lag, lag) if index >= lag else math.nan
        row[f"return_{lag}m"] = value
        row[f"downside_return_{lag}m"] = min(value, 0.0) if math.isfinite(value) else math.nan
        row[f"return_acceleration_{lag}m"] = (
            value - earlier if math.isfinite(value) and math.isfinite(earlier) else math.nan
        )
    for window in spec.path_windows_minutes:
        returns = _contiguous_window(log_return, timestamps, end_index=index, length=window)
        highs = _contiguous_window(high, timestamps, end_index=index, length=window)
        lows = _contiguous_window(low, timestamps, end_index=index, length=window)
        closes = _contiguous_window(close, timestamps, end_index=index, length=window)
        if returns is None or highs is None or lows is None or closes is None:
            for name in (
                "realized_vol",
                "downside_vol",
                "range_activity",
                "close_to_high",
                "close_to_low",
                "efficiency_ratio",
            ):
                row[f"{name}_{window}m"] = math.nan
            continue
        row[f"realized_vol_{window}m"] = float(np.sqrt(np.sum(returns * returns)))
        downside = np.minimum(returns, 0.0)
        row[f"downside_vol_{window}m"] = float(np.sqrt(np.sum(downside * downside)))
        maximum = float(np.max(highs))
        minimum = float(np.min(lows))
        row[f"range_activity_{window}m"] = (maximum - minimum) / close[index]
        row[f"close_to_high_{window}m"] = close[index] / maximum - 1.0
        row[f"close_to_low_{window}m"] = close[index] / minimum - 1.0
        path_length = float(np.sum(np.abs(returns)))
        net = abs(float(np.log(closes[-1] / closes[0]))) if closes[0] > 0.0 else math.nan
        row[f"efficiency_ratio_{window}m"] = _safe_ratio(net, path_length)


def _trend_features(
    *,
    row: dict[str, object],
    index: int,
    timestamps: np.ndarray,
    close: np.ndarray,
    ema: dict[int, np.ndarray],
    spec: DrawdownLadderStage1Spec,
) -> None:
    available: list[tuple[int, float]] = []
    for period in spec.ema_periods_minutes:
        current = float(ema[period][index])
        row[f"close_to_ema_{period}m"] = (
            close[index] / current - 1.0 if math.isfinite(current) and current > 0.0 else math.nan
        )
        previous_index = index - period
        previous = float(ema[period][previous_index]) if previous_index >= 0 else math.nan
        row[f"ema_slope_{period}m"] = (
            current / previous - 1.0
            if previous_index >= 0
            and timestamps[index] - timestamps[previous_index] == period * MS_PER_MINUTE
            and math.isfinite(current)
            and math.isfinite(previous)
            and previous > 0.0
            else math.nan
        )
        if math.isfinite(current) and current > 0.0:
            available.append((period, current))
    if len(available) < 2:
        row["ema_fan_bullish_fraction"] = math.nan
        row["ema_fan_bearish_fraction"] = math.nan
        row["ema_fan_dispersion"] = math.nan
        return
    pairs = list(zip(available[:-1], available[1:], strict=True))
    row["ema_fan_bullish_fraction"] = float(
        np.mean([short_value > long_value for (_, short_value), (_, long_value) in pairs])
    )
    row["ema_fan_bearish_fraction"] = float(
        np.mean([short_value < long_value for (_, short_value), (_, long_value) in pairs])
    )
    values = np.asarray([value for _, value in available], dtype=float)
    row["ema_fan_dispersion"] = float(np.std(values) / close[index])


def _oi_liquidation_features(
    *,
    row: dict[str, object],
    index: int,
    timestamps: np.ndarray,
    close: np.ndarray,
    quote: np.ndarray,
    oi: np.ndarray,
    oi_available: np.ndarray,
    long_liq: np.ndarray,
    short_liq: np.ndarray,
    liquidation_available: np.ndarray,
    spec: DrawdownLadderStage1Spec,
) -> None:
    row["oi_available"] = bool(oi_available[index])
    row["liquidation_available"] = bool(liquidation_available[index])
    for lag in spec.oi_lags_minutes:
        previous = index - lag
        valid = (
            previous >= 0
            and timestamps[index] - timestamps[previous] == lag * MS_PER_MINUTE
            and bool(oi_available[index])
            and bool(oi_available[previous])
            and math.isfinite(oi[index])
            and math.isfinite(oi[previous])
            and oi[previous] > 0.0
        )
        change = float(oi[index] / oi[previous] - 1.0) if valid else math.nan
        price_return = _return_at(close, timestamps, index, lag)
        row[f"oi_change_{lag}m"] = change
        row[f"price_oi_interaction_{lag}m"] = (
            price_return * change
            if math.isfinite(price_return) and math.isfinite(change)
            else math.nan
        )
    for window in spec.liquidation_windows_minutes:
        start = index - window + 1
        valid = (
            start >= 0
            and timestamps[index] - timestamps[start] == (window - 1) * MS_PER_MINUTE
            and bool(liquidation_available[start : index + 1].all())
        )
        long_sum = _rolling_sum(long_liq, timestamps, index, window) if valid else math.nan
        short_sum = _rolling_sum(short_liq, timestamps, index, window) if valid else math.nan
        quote_sum = _rolling_sum(quote, timestamps, index, window) if valid else math.nan
        row[f"long_liquidation_intensity_{window}m"] = _safe_ratio(long_sum, quote_sum)
        row[f"short_liquidation_intensity_{window}m"] = _safe_ratio(short_sum, quote_sum)
        total = long_sum + short_sum
        row[f"liquidation_imbalance_{window}m"] = (
            (short_sum - long_sum) / total
            if math.isfinite(total) and total > 0.0
            else math.nan
        )


def _btc_features(
    *,
    row: dict[str, object],
    coin_index: int,
    coin_timestamps: np.ndarray,
    coin_close: np.ndarray,
    coin_log_return: np.ndarray,
    btc: PreparedBTCFrame,
    spec: DrawdownLadderStage1Spec,
) -> None:
    open_time = int(coin_timestamps[coin_index])
    btc_index = btc.index_by_open_time.get(open_time)
    if btc_index is None:
        for name in _btc_model_feature_names(spec):
            row[name] = math.nan
        return
    btc_timestamp = btc.timestamp
    btc_close = btc.close
    btc_log_return = btc.log_return
    for lag in spec.reference_return_lags_minutes:
        btc_return = _return_at(btc_close, btc_timestamp, btc_index, lag)
        coin_return = _return_at(coin_close, coin_timestamps, coin_index, lag)
        row[f"btc_return_{lag}m"] = btc_return
        row[f"coin_minus_btc_return_{lag}m"] = (
            coin_return - btc_return
            if math.isfinite(coin_return) and math.isfinite(btc_return)
            else math.nan
        )
    for window in spec.reference_correlation_windows_minutes:
        coin_start = coin_index - window + 1
        btc_start = btc_index - window + 1
        valid = (
            coin_start >= 0
            and btc_start >= 0
            and coin_timestamps[coin_index] - coin_timestamps[coin_start]
            == (window - 1) * MS_PER_MINUTE
            and btc_timestamp[btc_index] - btc_timestamp[btc_start]
            == (window - 1) * MS_PER_MINUTE
            and bool(
                np.array_equal(
                    coin_timestamps[coin_start : coin_index + 1],
                    btc_timestamp[btc_start : btc_index + 1],
                )
            )
        )
        if not valid:
            correlation = beta = residual = math.nan
        else:
            coin_values = coin_log_return[coin_start : coin_index + 1]
            btc_values = btc_log_return[btc_start : btc_index + 1]
            correlation = _rolling_correlation(coin_values, btc_values)
            finite = np.isfinite(coin_values) & np.isfinite(btc_values)
            variance = float(np.var(btc_values[finite])) if int(finite.sum()) >= 10 else 0.0
            beta = (
                float(np.cov(coin_values[finite], btc_values[finite], ddof=0)[0, 1] / variance)
                if variance > 0.0
                else math.nan
            )
            coin_return = _return_at(coin_close, coin_timestamps, coin_index, window)
            btc_return = _return_at(btc_close, btc_timestamp, btc_index, window)
            residual = (
                coin_return - beta * btc_return
                if math.isfinite(coin_return) and math.isfinite(btc_return) and math.isfinite(beta)
                else math.nan
            )
        row[f"btc_correlation_{window}m"] = correlation
        row[f"btc_beta_{window}m"] = beta
        row[f"btc_residual_return_{window}m"] = residual
    for lag in (15, 60, 240):
        row[f"btc_oi_change_{lag}m"] = float(btc.oi_change[lag][btc_index])
    for window in (15, 60):
        row[f"btc_taker_imbalance_{window}m"] = float(
            btc.taker_imbalance[window][btc_index]
        )
    row["btc_range_activity_60m"] = float(btc.range_activity_60m[btc_index])
    row["btc_quote_activity_60m_vs_24h"] = float(
        btc.quote_activity_60m_vs_24h[btc_index]
    )


def _btc_model_feature_names(spec: DrawdownLadderStage1Spec) -> tuple[str, ...]:
    names: list[str] = []
    for lag in spec.reference_return_lags_minutes:
        names.extend((f"btc_return_{lag}m", f"coin_minus_btc_return_{lag}m"))
    for window in spec.reference_correlation_windows_minutes:
        names.extend(
            (
                f"btc_correlation_{window}m",
                f"btc_beta_{window}m",
                f"btc_residual_return_{window}m",
            )
        )
    names.extend(
        (
            "btc_oi_change_15m",
            "btc_oi_change_60m",
            "btc_oi_change_240m",
            "btc_taker_imbalance_15m",
            "btc_taker_imbalance_60m",
            "btc_range_activity_60m",
            "btc_quote_activity_60m_vs_24h",
        )
    )
    return tuple(names)


def prepare_btc_frame(
    source_path: str | Path,
    *,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> PreparedBTCFrame:
    btc = read_is_symbol_minutes(source_path, columns=_RAW_COLUMNS)
    timestamp = btc["timestamp"].to_numpy(np.int64)
    close = btc["close"].to_numpy(float)
    high = btc["high"].to_numpy(float)
    low = btc["low"].to_numpy(float)
    quote = btc["quote_volume"].to_numpy(float)
    taker = btc["taker_buy_quote_volume"].to_numpy(float)
    oi = btc["open_interest"].to_numpy(float)
    oi_available = btc["oi_available"].astype(bool).to_numpy()
    log_return = np.empty(len(close), dtype=float)
    log_return[0] = math.nan
    log_return[1:] = np.diff(np.log(close))
    oi_change: dict[int, np.ndarray] = {}
    for lag in (15, 60, 240):
        change = np.full(len(btc), np.nan, dtype=float)
        if len(btc) > lag:
            valid = (
                (timestamp[lag:] - timestamp[:-lag] == lag * MS_PER_MINUTE)
                & oi_available[lag:]
                & oi_available[:-lag]
                & np.isfinite(oi[lag:])
                & np.isfinite(oi[:-lag])
                & (oi[:-lag] > 0.0)
            )
            target = change[lag:]
            target[valid] = oi[lag:][valid] / oi[:-lag][valid] - 1.0
        oi_change[lag] = change
    taker_imbalance: dict[int, np.ndarray] = {}
    for window in (15, 60):
        quote_sum = pd.Series(quote).rolling(window, min_periods=window).sum().to_numpy(float)
        taker_sum = pd.Series(taker).rolling(window, min_periods=window).sum().to_numpy(float)
        ratio = np.divide(
            2.0 * taker_sum,
            quote_sum,
            out=np.full(len(quote_sum), np.nan, dtype=float),
            where=quote_sum > 0.0,
        )
        taker_imbalance[window] = ratio - 1.0
    range_high = pd.Series(high).rolling(60, min_periods=60).max().to_numpy(float)
    range_low = pd.Series(low).rolling(60, min_periods=60).min().to_numpy(float)
    range_activity_60m = (range_high - range_low) / close
    activity = pd.Series(quote).rolling(60, min_periods=60).sum().to_numpy(float)
    prior = pd.Series(quote).shift(60).rolling(1440, min_periods=1440).mean().to_numpy(float)
    quote_activity_60m_vs_24h = np.where(
        prior > 0.0,
        activity / (prior * 60),
        np.nan,
    )
    return PreparedBTCFrame(
        timestamp=timestamp,
        close=close,
        log_return=log_return,
        index_by_open_time={int(value): index for index, value in enumerate(timestamp)},
        oi_change=oi_change,
        taker_imbalance=taker_imbalance,
        range_activity_60m=range_activity_60m,
        quote_activity_60m_vs_24h=quote_activity_60m_vs_24h,
    )


def build_symbol_stage1_features(
    *,
    source_path: str | Path,
    candidate_path: str | Path,
    outcome_path: str | Path,
    btc_frame: PreparedBTCFrame,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
    direction: Literal["long", "short"] = "long",
) -> pd.DataFrame:
    if direction not in {"long", "short"}:
        raise ValueError("Stage-1 direction must be long or short")
    source = Path(source_path)
    symbol = source.stem.upper()
    candidates = pd.read_parquet(candidate_path)
    outcomes = pd.read_parquet(outcome_path)
    joined = candidates.merge(
        outcomes[
            [
                "candidate_id",
                "future_start_time_ms",
                "available_future_minutes",
                "horizon_complete",
                f"break_even_{spec.recovery_cost_bps}bps_reached",
                f"time_to_break_even_{spec.recovery_cost_bps}bps_minutes",
            ]
        ],
        on="candidate_id",
        how="left",
        validate="one_to_one",
    )
    if len(joined) != len(candidates) or bool(joined["future_start_time_ms"].isna().any()):
        raise Stage1FeatureError("Stage-1 candidate/outcome join is incomplete")
    if joined.empty:
        return pd.DataFrame(
            columns=[
                row.name
                for row in build_stage1_feature_catalog(spec, direction=direction)
            ]
        )
    if set(joined["symbol"].astype(str).str.upper()) != {symbol}:
        raise Stage1FeatureError("Stage-1 shard symbol mismatch")
    minute = read_is_symbol_minutes(source, columns=_RAW_COLUMNS)
    timestamp = minute["timestamp"].to_numpy(np.int64)
    open_ = minute["open"].to_numpy(float)
    high = minute["high"].to_numpy(float)
    low = minute["low"].to_numpy(float)
    close = minute["close"].to_numpy(float)
    quote = minute["quote_volume"].to_numpy(float)
    trade_count = minute["trade_count"].to_numpy(float)
    taker_quote = minute["taker_buy_quote_volume"].to_numpy(float)
    oi = minute["open_interest"].to_numpy(float)
    long_liq = minute["long_liquidations_vol"].to_numpy(float)
    short_liq = minute["short_liquidations_vol"].to_numpy(float)
    oi_available = minute["oi_available"].astype(bool).to_numpy()
    liquidation_available = minute["liquidation_available"].astype(bool).to_numpy()
    if bool((~np.isfinite(np.column_stack((open_, high, low, close)))).any()):
        raise Stage1FeatureError(f"{symbol} has non-finite OHLC")
    log_return = np.empty(len(close), dtype=float)
    log_return[0] = math.nan
    log_return[1:] = np.diff(np.log(close))
    ema = _ema_arrays(close, spec.ema_periods_minutes)
    index_by_open_time = {int(value): index for index, value in enumerate(timestamp)}
    rows: list[dict[str, object]] = []
    for candidate in joined.sort_values(["snapshot_time_ms", "candidate_id"]).itertuples(index=False):
        index = index_by_open_time.get(int(candidate.fill_bar_open_time_ms))
        if index is None:
            raise Stage1FeatureError("candidate fill bar is absent from source")
        snapshot_time = int(candidate.snapshot_time_ms)
        if timestamp[index] + MS_PER_MINUTE != snapshot_time:
            raise Stage1FeatureError("candidate snapshot does not equal fill-bar availability")
        entry = float(candidate.equal_notional_average_entry_price)
        anchor = float(candidate.anchor_price)
        block = UTC_SESSION_BY_SEQ[int(candidate.session_seq)]
        duration = block.duration_minutes
        recovered = bool(getattr(candidate, f"break_even_{spec.recovery_cost_bps}bps_reached"))
        recovery_minutes_raw = getattr(
            candidate,
            f"time_to_break_even_{spec.recovery_cost_bps}bps_minutes",
        )
        recovery_minutes = (
            int(recovery_minutes_raw)
            if pd.notna(recovery_minutes_raw)
            else None
        )
        label_available = bool(candidate.horizon_complete)
        resolution_time = (
            snapshot_time + recovery_minutes * MS_PER_MINUTE
            if recovered and recovery_minutes is not None
            else snapshot_time
            + int(candidate.available_future_minutes) * MS_PER_MINUTE
        )
        fill_range = high[index] - low[index]
        extreme_column = (
            "same_bar_max_drawdown_pct"
            if direction == "long"
            else "same_bar_max_rally_pct"
        )
        extreme_value = float(getattr(candidate, extreme_column))
        row: dict[str, object] = {
            "feature_schema_version": spec.feature_schema_version,
            "label_schema_version": spec.label_schema_version,
            "is_nature_anchor": True,
            "candidate_id": str(candidate.candidate_id),
            "parent_event_id": str(candidate.parent_event_id),
            "symbol": symbol,
            "snapshot_time_ms": snapshot_time,
            "feature_cutoff_time_ms": snapshot_time,
            "future_start_time_ms": int(candidate.future_start_time_ms),
            "label_resolution_time_ms": resolution_time,
            "recovery_25bps_48h": recovered,
            "recovery_time_or_horizon_minutes": (
                recovery_minutes
                if recovered and recovery_minutes is not None
                else spec.recovery_horizon_minutes
            ),
            "label_available": label_available,
            "grid_step_pct": int(candidate.grid_step_pct),
            "filled_level_count": int(candidate.filled_level_count),
            "deepest_filled_level_pct": int(candidate.deepest_filled_level_pct),
            "average_entry_to_anchor": entry / anchor - 1.0,
            "snapshot_close_to_anchor": close[index] / anchor - 1.0,
            "snapshot_close_to_entry": close[index] / entry - 1.0,
            "fill_overshoot_pct": extreme_value
            - float(candidate.deepest_filled_level_pct),
            extreme_column: extreme_value,
            "fill_bar_range_pct": fill_range / close[index],
            "fill_bar_body_return": close[index] / open_[index] - 1.0,
            "fill_bar_close_location": (
                (close[index] - low[index]) / fill_range if fill_range > 0.0 else 0.5
            ),
            "fill_bar_rebound_from_low": close[index] / low[index] - 1.0,
            "minutes_from_order_activation": int(candidate.minutes_from_order_activation),
            "session_progress_fraction": min(
                1.0,
                int(candidate.minutes_from_order_activation) / duration,
            ),
            "session_seq": int(candidate.session_seq),
            "session_name": str(candidate.session_name),
            "utc_hour": int(pd.Timestamp(snapshot_time, unit="ms", tz="UTC").hour),
            "utc_weekday": int(pd.Timestamp(snapshot_time, unit="ms", tz="UTC").weekday()),
            "contract_age_days": (snapshot_time - int(timestamp[0])) / (24 * 60 * MS_PER_MINUTE),
            "history_gap_count_1440m": float(
                np.sum(np.diff(timestamp[max(0, index - 1440) : index + 1]) != MS_PER_MINUTE)
            ),
        }
        _path_features(
            row=row,
            index=index,
            timestamps=timestamp,
            high=high,
            low=low,
            close=close,
            log_return=log_return,
            spec=spec,
        )
        _trend_features(
            row=row,
            index=index,
            timestamps=timestamp,
            close=close,
            ema=ema,
            spec=spec,
        )
        _activity_features(
            row=row,
            index=index,
            timestamps=timestamp,
            quote=quote,
            trade_count=trade_count,
            taker_quote=taker_quote,
            windows=spec.activity_windows_minutes,
        )
        row["quote_activity_60m_vs_prior_sessions"] = _session_activity_ratio(
            quote, timestamp, index=index, window=60
        )
        row["trade_activity_60m_vs_prior_sessions"] = _session_activity_ratio(
            trade_count, timestamp, index=index, window=60
        )
        returns_60 = _contiguous_window(log_return, timestamp, end_index=index, length=60)
        quote_60 = _contiguous_window(quote, timestamp, end_index=index, length=60)
        taker_60 = _contiguous_window(taker_quote, timestamp, end_index=index, length=60)
        if returns_60 is None or quote_60 is None:
            row["price_volume_correlation_60m"] = math.nan
        else:
            row["price_volume_correlation_60m"] = _rolling_correlation(
                returns_60, np.log1p(quote_60)
            )
        if returns_60 is None or quote_60 is None or taker_60 is None:
            row["return_taker_correlation_60m"] = math.nan
        else:
            imbalance = np.divide(
                2.0 * taker_60,
                quote_60,
                out=np.full(len(quote_60), np.nan, dtype=float),
                where=quote_60 > 0.0,
            ) - 1.0
            row["return_taker_correlation_60m"] = _rolling_correlation(
                returns_60, imbalance
            )
        _oi_liquidation_features(
            row=row,
            index=index,
            timestamps=timestamp,
            close=close,
            quote=quote,
            oi=oi,
            oi_available=oi_available,
            long_liq=long_liq,
            short_liq=short_liq,
            liquidation_available=liquidation_available,
            spec=spec,
        )
        _btc_features(
            row=row,
            coin_index=index,
            coin_timestamps=timestamp,
            coin_close=close,
            coin_log_return=log_return,
            btc=btc_frame,
            spec=spec,
        )
        for lag in spec.breadth_return_lags_minutes:
            for suffix in (
                "mean_return",
                "return_dispersion",
                "share_negative",
                "share_down_1pct",
                "share_down_3pct",
                "share_up_1pct",
                "share_up_3pct",
            ):
                row[f"market_{suffix}_{lag}m"] = math.nan
        for name in (
            "market_symbol_count",
            "market_mean_quote_activity_60m",
            "market_share_quote_activity_gt_3x",
            "market_share_quote_activity_gt_10x",
            "prior_symbol_resolved_count",
            "prior_exact_state_resolved_count",
            "simultaneous_ladder_signal_count",
            "simultaneous_ladder_parent_count",
        ):
            row[name] = math.nan
        for window in spec.prior_reaction_windows:
            row[f"prior_symbol_recovery_rate_{window}"] = math.nan
            row[f"prior_exact_state_recovery_rate_{window}"] = math.nan
            row[f"prior_symbol_median_recovery_minutes_{window}"] = math.nan
        rows.append(row)
    frame = pd.DataFrame(rows)
    expected = [
        definition.name
        for definition in build_stage1_feature_catalog(spec, direction=direction)
    ]
    missing = sorted(set(expected) - set(frame.columns))
    extra = sorted(set(frame.columns) - set(expected))
    if missing or extra:
        raise Stage1FeatureError(f"Stage-1 feature schema mismatch; missing={missing}; extra={extra}")
    frame = frame.loc[:, expected]
    if not bool(frame["feature_cutoff_time_ms"].le(frame["snapshot_time_ms"]).all()):
        raise Stage1FeatureError("Stage-1 feature cutoff exceeds snapshot")
    if not bool(frame["future_start_time_ms"].gt(frame["snapshot_time_ms"]).all()):
        raise Stage1FeatureError("Stage-1 future does not start after snapshot")
    return frame


__all__ = [
    "PreparedBTCFrame",
    "Stage1FeatureError",
    "build_symbol_stage1_features",
    "prepare_btc_frame",
]
