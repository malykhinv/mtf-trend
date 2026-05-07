"""Build early anomaly-continuation research artifacts from cached OHLCV data.

This module is deliberately separate from the PNO trading engine. It studies the
early "wake-up after sleep" hypothesis without changing executable PNO logic.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

import numpy as np
import pandas as pd


DEFAULT_CACHE_DIR = Path(".output/cache")
DEFAULT_OUTPUT_DIR = Path(".output/manual_checks/anomaly_continuation_lab")
DEFAULT_TIMEFRAME = "1m"
DEFAULT_DAYS = 31
DEFAULT_BASELINE_CANDLES = 60
DEFAULT_CONFIRMATION_CANDLES = 4
DEFAULT_FORWARD_HIGH_CANDLES = 60
DEFAULT_FORWARD_LOW_CANDLES = 30
DEFAULT_MIN_QUOTE_RATIO_START = 10.0
DEFAULT_MIN_TRADE_RATIO_START = 10.0
DEFAULT_BIG_MOVE_THRESHOLD = 0.25
OI_TIMEFRAME = "5m"
OI_LOOKBACK_BARS = (1, 3, 6)
OI_EXPECTED_INTERVAL_MS = 5 * 60 * 1000

OHLCV_COLUMNS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "number_of_trades",
)
OPTIONAL_FLOW_COLUMNS = (
    "taker_buy_volume",
    "taker_buy_quote_volume",
)


@dataclass(frozen=True, slots=True)
class AnomalyLabConfig:
    cache_dir: Path = DEFAULT_CACHE_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    timeframe: str = DEFAULT_TIMEFRAME
    days: int = DEFAULT_DAYS
    end_timestamp_ms: int | None = None
    baseline_candles: int = DEFAULT_BASELINE_CANDLES
    confirmation_candles: int = DEFAULT_CONFIRMATION_CANDLES
    forward_high_candles: int = DEFAULT_FORWARD_HIGH_CANDLES
    forward_low_candles: int = DEFAULT_FORWARD_LOW_CANDLES
    min_quote_ratio_start: float = DEFAULT_MIN_QUOTE_RATIO_START
    min_trade_ratio_start: float = DEFAULT_MIN_TRADE_RATIO_START
    hold_min_start_fraction: float = 0.35
    hold_min_baseline_ratio: float = 3.0
    cooldown_candles: int = 60
    big_move_threshold: float = DEFAULT_BIG_MOVE_THRESHOLD
    fade_max_upside: float = 0.10
    fade_drawdown_threshold: float = -0.03


def _safe_divide(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _symbol_from_cache_dir(symbol_dir: Path) -> str:
    return unquote(symbol_dir.name)


def _cache_symbol_dir_name(symbol: str) -> str:
    return quote(symbol, safe="")


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _format_eta(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _emit_progress(*, label: str, done: int, total: int, started_at: float) -> None:
    if total <= 0:
        return
    pct = 100.0 * done / total
    elapsed = max(time.monotonic() - started_at, 1e-9)
    eta = elapsed * (total - done) / max(done, 1)
    print(f"{label}: {pct:5.1f}% eta {_format_eta(eta)}", flush=True)


def _read_symbol_frame(path: Path, *, start_ms: int, end_ms: int) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    missing_columns = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"{path} missing required columns: {missing_columns}")
    selected_columns = list(OHLCV_COLUMNS) + [column for column in OPTIONAL_FLOW_COLUMNS if column in frame.columns]
    frame = frame.loc[
        (frame["timestamp"] >= start_ms) & (frame["timestamp"] <= end_ms),
        selected_columns,
    ].copy()
    frame.sort_values("timestamp", inplace=True)
    frame.drop_duplicates("timestamp", keep="last", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def _resolve_end_timestamp_ms(cache_dir: Path, timeframe: str) -> int:
    max_timestamp: int | None = None
    for path in cache_dir.glob(f"*/{timeframe}/data.parquet"):
        try:
            timestamps = pd.read_parquet(path, columns=["timestamp"])
        except Exception:
            continue
        if timestamps.empty:
            continue
        current = int(timestamps["timestamp"].max())
        max_timestamp = current if max_timestamp is None else max(max_timestamp, current)
    if max_timestamp is None:
        raise ValueError(f"No cached {timeframe} data found in {cache_dir}")
    return max_timestamp


def compute_start_verticality_metrics(segment: pd.DataFrame) -> dict[str, float]:
    """Return bounded and raw metrics for how vertical the initial pump segment is.

    The segment must contain only candles available by the decision point:
    anomaly start plus the fixed confirmation window.
    """
    if segment.empty:
        return {
            "start_verticality_score": float("nan"),
            "start_verticality_path_efficiency": float("nan"),
            "start_verticality_range_efficiency": float("nan"),
            "start_verticality_slope_pct_per_candle": float("nan"),
            "start_verticality_max_retrace_fraction": float("nan"),
            "start_verticality_green_share": float("nan"),
        }

    opens = segment["open"].astype(float).to_numpy()
    highs = segment["high"].astype(float).to_numpy()
    lows = segment["low"].astype(float).to_numpy()
    closes = segment["close"].astype(float).to_numpy()
    start_price = float(opens[0])
    end_price = float(closes[-1])
    net_move = end_price - start_price
    positive_net_move = max(net_move, 0.0)
    close_path = abs(float(closes[0]) - start_price)
    if len(closes) > 1:
        close_path += float(np.sum(np.abs(np.diff(closes))))
    path_efficiency = _safe_divide(positive_net_move, close_path)
    segment_range = float(np.nanmax(highs) - np.nanmin(lows))
    range_efficiency = _safe_divide(positive_net_move, segment_range)
    candle_count = max(int(len(segment)), 1)
    slope_pct_per_candle = _safe_divide(net_move, start_price) / candle_count

    running_high = np.maximum.accumulate(highs)
    drawdowns = running_high - lows
    max_retrace = float(np.nanmax(drawdowns)) if drawdowns.size else 0.0
    max_retrace_fraction = _safe_divide(max_retrace, positive_net_move) if positive_net_move > 0.0 else 1.0
    retrace_component = 1.0 - min(max(max_retrace_fraction, 0.0), 1.0)
    green_share = float(np.mean(closes >= opens))
    path_component = min(max(path_efficiency, 0.0), 1.0)
    range_component = min(max(range_efficiency, 0.0), 1.0)
    verticality_score = (
        0.45 * path_component
        + 0.25 * range_component
        + 0.20 * retrace_component
        + 0.10 * green_share
    )
    if net_move <= 0.0:
        verticality_score = 0.0

    return {
        "start_verticality_score": float(verticality_score),
        "start_verticality_path_efficiency": float(path_efficiency),
        "start_verticality_range_efficiency": float(range_efficiency),
        "start_verticality_slope_pct_per_candle": float(slope_pct_per_candle),
        "start_verticality_max_retrace_fraction": float(max_retrace_fraction),
        "start_verticality_green_share": float(green_share),
    }


def _flow_metrics(
    *,
    frame: pd.DataFrame,
    idx: int,
    confirmation_slice: slice,
    baseline_slice: slice,
    quote_volume: pd.Series,
    trade_count: pd.Series,
) -> dict[str, object]:
    metrics: dict[str, object] = {
        "flow_taker_buy_status": "missing_columns",
        "start_taker_buy_quote_share": float("nan"),
        "baseline_taker_buy_quote_share_median": float("nan"),
        "start_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_mean": float("nan"),
        "next_n_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_decay": float("nan"),
        "start_avg_trade_quote_size": float("nan"),
        "baseline_avg_trade_quote_size_median": float("nan"),
        "start_avg_trade_quote_size_ratio": float("nan"),
        "next_n_avg_trade_quote_size_mean": float("nan"),
        "next_n_avg_trade_quote_size_decay": float("nan"),
    }
    avg_trade_quote_size = quote_volume / trade_count.replace(0.0, np.nan)
    start_avg_trade = float(avg_trade_quote_size.iloc[idx])
    baseline_avg_trade = float(avg_trade_quote_size.iloc[baseline_slice].median())
    next_avg_trade = float(avg_trade_quote_size.iloc[confirmation_slice].mean())
    metrics.update(
        {
            "start_avg_trade_quote_size": start_avg_trade,
            "baseline_avg_trade_quote_size_median": baseline_avg_trade,
            "start_avg_trade_quote_size_ratio": _safe_divide(start_avg_trade, baseline_avg_trade),
            "next_n_avg_trade_quote_size_mean": next_avg_trade,
            "next_n_avg_trade_quote_size_decay": _safe_divide(next_avg_trade, start_avg_trade),
        }
    )

    if "taker_buy_quote_volume" not in frame.columns:
        return metrics
    taker_buy_quote = frame["taker_buy_quote_volume"].astype(float)
    taker_share = taker_buy_quote / quote_volume.replace(0.0, np.nan)
    start_share = float(taker_share.iloc[idx])
    baseline_share = float(taker_share.iloc[baseline_slice].median())
    next_share = float(taker_share.iloc[confirmation_slice].mean())
    metrics.update(
        {
            "flow_taker_buy_status": "ok" if np.isfinite(start_share) else "missing_values",
            "start_taker_buy_quote_share": start_share,
            "baseline_taker_buy_quote_share_median": baseline_share,
            "start_taker_buy_quote_share_delta": start_share - baseline_share,
            "next_n_taker_buy_quote_share_mean": next_share,
            "next_n_taker_buy_quote_share_delta": next_share - baseline_share,
            "next_n_taker_buy_quote_share_decay": _safe_divide(next_share, start_share),
        }
    )
    return metrics


def _effort_and_sleep_metrics(
    *,
    idx: int,
    decision_idx: int,
    baseline_slice: slice,
    open_price: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    quote_volume: pd.Series,
    trade_count: pd.Series,
    baseline_quote_value: float,
    baseline_trade_value: float,
    impulse_low: float,
    impulse_high: float,
) -> dict[str, object]:
    start_open = float(open_price.iloc[idx])
    start_close = float(close.iloc[idx])
    start_high = float(high.iloc[idx])
    start_low = float(low.iloc[idx])
    start_range = start_high - start_low
    start_ret = _safe_divide(start_close - start_open, start_open)
    abs_start_ret = abs(start_ret) if np.isfinite(start_ret) else float("nan")
    range_series = (high - low).astype(float)
    baseline_range = float(range_series.iloc[baseline_slice].median())
    baseline_range_pct = float((range_series / close.replace(0.0, np.nan)).iloc[baseline_slice].median())
    start_range_pct = _safe_divide(start_range, start_open)
    decision_ret = _safe_divide(float(close.iloc[decision_idx]) - start_open, start_open)
    impulse_range = impulse_high - impulse_low
    post_start_low = float(low.iloc[idx + 1 : decision_idx + 1].min()) if decision_idx > idx else float("nan")
    post_start_pullback_fraction = _safe_divide(impulse_high - post_start_low, impulse_range)
    return {
        "decision_return_from_start_open": decision_ret,
        "start_close_position_in_range": _safe_divide(start_close - start_low, start_range),
        "start_body_to_range": _safe_divide(abs(start_close - start_open), start_range),
        "start_upper_wick_to_range": _safe_divide(start_high - max(start_open, start_close), start_range),
        "start_lower_wick_to_range": _safe_divide(min(start_open, start_close) - start_low, start_range),
        "baseline_range_median": baseline_range,
        "baseline_range_pct_median": baseline_range_pct,
        "start_range_ratio_to_baseline": _safe_divide(start_range, baseline_range),
        "start_range_pct": start_range_pct,
        "start_range_pct_ratio_to_baseline": _safe_divide(start_range_pct, baseline_range_pct),
        "start_quote_per_abs_return": _safe_divide(float(quote_volume.iloc[idx]), abs_start_ret),
        "start_trades_per_abs_return": _safe_divide(float(trade_count.iloc[idx]), abs_start_ret),
        "start_quote_ratio_per_abs_return": _safe_divide(
            _safe_divide(float(quote_volume.iloc[idx]), baseline_quote_value),
            abs_start_ret,
        ),
        "start_trade_ratio_per_abs_return": _safe_divide(
            _safe_divide(float(trade_count.iloc[idx]), baseline_trade_value),
            abs_start_ret,
        ),
        "post_start_pullback_fraction_of_box": post_start_pullback_fraction,
    }


def _classify_outcome(
    *,
    future_ret_high: float,
    future_dd_low: float,
    config: AnomalyLabConfig,
) -> str:
    if np.isfinite(future_ret_high) and future_ret_high >= config.big_move_threshold:
        return "big_25p"
    if (
        np.isfinite(future_ret_high)
        and np.isfinite(future_dd_low)
        and future_ret_high < config.fade_max_upside
        and future_dd_low <= config.fade_drawdown_threshold
    ):
        return "fast_fade"
    return "other"


def collect_symbol_anomaly_rows(
    *,
    symbol: str,
    frame: pd.DataFrame,
    config: AnomalyLabConfig,
) -> list[dict[str, object]]:
    if len(frame) < (
        config.baseline_candles
        + config.confirmation_candles
        + max(config.forward_high_candles, config.forward_low_candles)
        + 1
    ):
        return []

    quote_volume = frame["quote_volume"].astype(float)
    trade_count = frame["number_of_trades"].astype(float)
    open_price = frame["open"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    baseline_quote = quote_volume.shift(1).rolling(config.baseline_candles, min_periods=config.baseline_candles // 2).median()
    baseline_trades = trade_count.shift(1).rolling(config.baseline_candles, min_periods=config.baseline_candles // 2).median()
    quote_ratio = quote_volume / baseline_quote.replace(0.0, np.nan)
    trade_ratio = trade_count / baseline_trades.replace(0.0, np.nan)
    anomaly_mask = (
        quote_ratio.ge(config.min_quote_ratio_start)
        & trade_ratio.ge(config.min_trade_ratio_start)
        & quote_ratio.notna()
        & trade_ratio.notna()
    )

    rows: list[dict[str, object]] = []
    last_selected_idx = -10**9
    max_forward = max(config.forward_high_candles, config.forward_low_candles)
    for idx in np.flatnonzero(anomaly_mask.to_numpy()):
        if idx < config.baseline_candles or idx + config.confirmation_candles + max_forward >= len(frame):
            continue
        if idx - last_selected_idx < config.cooldown_candles:
            continue
        if idx >= 3 and bool((anomaly_mask.iloc[idx - 3 : idx]).any()):
            continue
        last_selected_idx = int(idx)

        decision_idx = idx + config.confirmation_candles
        confirmation_slice = slice(idx + 1, decision_idx + 1)
        baseline_slice = slice(max(0, idx - config.baseline_candles), idx)
        start_quote = float(quote_volume.iloc[idx])
        start_trades = float(trade_count.iloc[idx])
        baseline_quote_value = float(baseline_quote.iloc[idx])
        baseline_trade_value = float(baseline_trades.iloc[idx])
        quote_hold_threshold = max(
            config.hold_min_start_fraction * start_quote,
            config.hold_min_baseline_ratio * baseline_quote_value,
        )
        trade_hold_threshold = max(
            config.hold_min_start_fraction * start_trades,
            config.hold_min_baseline_ratio * baseline_trade_value,
        )
        hold_mask = (
            quote_volume.iloc[confirmation_slice].ge(quote_hold_threshold)
            & trade_count.iloc[confirmation_slice].ge(trade_hold_threshold)
        )
        hold_count = int(hold_mask.sum())
        next_quote_mean = float(quote_volume.iloc[confirmation_slice].mean())
        next_trade_mean = float(trade_count.iloc[confirmation_slice].mean())
        segment = frame.iloc[idx : decision_idx + 1]
        verticality = compute_start_verticality_metrics(segment)
        impulse_low = float(low.iloc[idx : decision_idx + 1].min())
        impulse_high = float(high.iloc[idx : decision_idx + 1].max())
        start_open = float(open_price.iloc[idx])
        decision_close = float(close.iloc[decision_idx])
        impulse_range = impulse_high - impulse_low
        price_retention = _safe_divide(decision_close - impulse_low, impulse_range)
        midpoint_lost = bool(decision_close < (impulse_low + 0.5 * impulse_range))
        new_high_count = int(high.iloc[idx + 1 : decision_idx + 1].gt(float(high.iloc[idx])).sum())
        entry_price = decision_close
        future_high = float(high.iloc[decision_idx + 1 : decision_idx + 1 + config.forward_high_candles].max())
        future_low = float(low.iloc[decision_idx + 1 : decision_idx + 1 + config.forward_low_candles].min())
        future_ret_high = _safe_divide(future_high - entry_price, entry_price)
        future_dd_low = _safe_divide(future_low - entry_price, entry_price)
        flow = _flow_metrics(
            frame=frame,
            idx=idx,
            confirmation_slice=confirmation_slice,
            baseline_slice=baseline_slice,
            quote_volume=quote_volume,
            trade_count=trade_count,
        )
        effort_sleep = _effort_and_sleep_metrics(
            idx=idx,
            decision_idx=decision_idx,
            baseline_slice=baseline_slice,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
            quote_volume=quote_volume,
            trade_count=trade_count,
            baseline_quote_value=baseline_quote_value,
            baseline_trade_value=baseline_trade_value,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
        )

        rows.append(
            {
                "symbol": symbol,
                "timeframe": config.timeframe,
                "timestamp_ms": int(frame["timestamp"].iloc[idx]),
                "timestamp_utc": _timestamp_to_utc(int(frame["timestamp"].iloc[idx])),
                "decision_timestamp_ms": int(frame["timestamp"].iloc[decision_idx]),
                "decision_timestamp_utc": _timestamp_to_utc(int(frame["timestamp"].iloc[decision_idx])),
                "confirmation_candles": int(config.confirmation_candles),
                "baseline_candles": int(config.baseline_candles),
                "start_open": start_open,
                "start_high": float(high.iloc[idx]),
                "start_low": float(low.iloc[idx]),
                "start_close": float(close.iloc[idx]),
                "decision_close": decision_close,
                "start_quote_volume": start_quote,
                "start_trade_count": start_trades,
                "baseline_quote_volume_median": baseline_quote_value,
                "baseline_trade_count_median": baseline_trade_value,
                "start_quote_ratio": float(quote_ratio.iloc[idx]),
                "start_trade_ratio": float(trade_ratio.iloc[idx]),
                "next_n_quote_volume_mean": next_quote_mean,
                "next_n_trade_count_mean": next_trade_mean,
                "next_n_quote_decay": _safe_divide(next_quote_mean, start_quote),
                "next_n_trade_decay": _safe_divide(next_trade_mean, start_trades),
                "hold_count_next_n_candles": hold_count,
                "hold_ratio_next_n_candles": _safe_divide(hold_count, config.confirmation_candles),
                "price_retention_next_n": price_retention,
                "midpoint_lost_next_n": midpoint_lost,
                "new_high_count_next_n": new_high_count,
                "decision_box_low": impulse_low,
                "decision_box_high": impulse_high,
                "decision_box_range": impulse_range,
                "future_high": future_high,
                "future_low": future_low,
                "future_ret_high_after_decision": future_ret_high,
                "future_dd_low_after_decision": future_dd_low,
                "outcome_label": _classify_outcome(
                    future_ret_high=future_ret_high,
                    future_dd_low=future_dd_low,
                    config=config,
                ),
                **verticality,
                **flow,
                **effort_sleep,
            }
        )
    return rows


def collect_anomaly_lab_rows(
    config: AnomalyLabConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
) -> pd.DataFrame:
    end_ms = config.end_timestamp_ms
    if end_ms is None:
        end_ms = _resolve_end_timestamp_ms(config.cache_dir, config.timeframe)
    start_ms = int((datetime.fromtimestamp(end_ms / 1000, UTC) - timedelta(days=config.days)).timestamp() * 1000)
    wanted_symbols = set(symbols) if symbols is not None else None
    rows: list[dict[str, object]] = []
    paths = sorted(config.cache_dir.glob(f"*%2FUSDT%3AUSDT/{config.timeframe}/data.parquet"))
    if wanted_symbols is not None:
        paths = [path for path in paths if _symbol_from_cache_dir(path.parent.parent) in wanted_symbols]
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, path in enumerate(paths, start=1):
        symbol = _symbol_from_cache_dir(path.parent.parent)
        try:
            frame = _read_symbol_frame(path, start_ms=start_ms, end_ms=end_ms)
            rows.extend(collect_symbol_anomaly_rows(symbol=symbol, frame=frame, config=config))
        except Exception as exc:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": config.timeframe,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if progress_label is not None and paths:
            current_pct = int(100 * processed_count / len(paths))
            if current_pct >= next_progress_pct or processed_count == len(paths):
                _emit_progress(
                    label=progress_label,
                    done=processed_count,
                    total=len(paths),
                    started_at=progress_started_at,
                )
                next_progress_pct = current_pct + 5
    result = pd.DataFrame(rows)
    if not result.empty and "timestamp_ms" in result.columns:
        result.sort_values(["timestamp_ms", "symbol"], inplace=True)
        result.reset_index(drop=True, inplace=True)
    return enrich_candidates_with_open_interest(result, cache_dir=config.cache_dir)


def _empty_oi_columns() -> dict[str, object]:
    values: dict[str, object] = {
        "oi_timeframe": OI_TIMEFRAME,
        "oi_status": "not_checked",
        "oi_timestamp_ms": np.nan,
        "oi_timestamp_utc": "",
        "oi_age_ms": np.nan,
        "oi_open_interest": np.nan,
    }
    for bars in OI_LOOKBACK_BARS:
        values[f"oi_change_{bars}x5m"] = np.nan
        values[f"oi_change_pct_{bars}x5m"] = np.nan
    return values


def _read_oi_frame(cache_dir: Path, symbol: str) -> tuple[pd.DataFrame | None, str]:
    path = cache_dir / _cache_symbol_dir_name(symbol) / OI_TIMEFRAME / "data.parquet"
    if not path.exists():
        return None, "missing_frame"
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return None, f"read_error:{type(exc).__name__}"
    if "open_interest" not in frame.columns:
        return None, "missing_column"
    if "timestamp" not in frame.columns:
        return None, "missing_timestamp"
    oi_frame = frame.loc[:, ["timestamp", "open_interest"]].copy()
    oi_frame.dropna(subset=["timestamp", "open_interest"], inplace=True)
    if oi_frame.empty:
        return None, "empty_oi"
    oi_frame.sort_values("timestamp", inplace=True)
    oi_frame.drop_duplicates("timestamp", keep="last", inplace=True)
    oi_frame.reset_index(drop=True, inplace=True)
    return oi_frame, "ok"


def _enrich_symbol_oi(candidates: pd.DataFrame, oi_frame: pd.DataFrame | None, status: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if oi_frame is None:
        for _ in range(len(candidates)):
            values = _empty_oi_columns()
            values["oi_status"] = status
            rows.append(values)
        return rows

    oi_ts = oi_frame["timestamp"].astype(np.int64).to_numpy()
    oi_values = oi_frame["open_interest"].astype(float).to_numpy()
    for _, candidate in candidates.iterrows():
        values = _empty_oi_columns()
        decision_ts = candidate.get("decision_timestamp_ms")
        if pd.isna(decision_ts):
            values["oi_status"] = "missing_decision_timestamp"
            rows.append(values)
            continue
        decision_ts_int = int(decision_ts)
        oi_idx = int(np.searchsorted(oi_ts, decision_ts_int, side="right") - 1)
        if oi_idx < 0:
            values["oi_status"] = "no_oi_before_decision"
            rows.append(values)
            continue

        current_ts = int(oi_ts[oi_idx])
        age_ms = decision_ts_int - current_ts
        values["oi_status"] = "stale_asof" if age_ms > OI_EXPECTED_INTERVAL_MS else "ok"
        values["oi_timestamp_ms"] = current_ts
        values["oi_timestamp_utc"] = _timestamp_to_utc(current_ts)
        values["oi_age_ms"] = int(age_ms)
        values["oi_open_interest"] = float(oi_values[oi_idx])
        for bars in OI_LOOKBACK_BARS:
            prev_idx = oi_idx - bars
            if prev_idx < 0:
                continue
            previous = float(oi_values[prev_idx])
            current = float(oi_values[oi_idx])
            values[f"oi_change_{bars}x5m"] = current - previous
            values[f"oi_change_pct_{bars}x5m"] = _safe_divide(current - previous, previous)
        rows.append(values)
    return rows


def enrich_candidates_with_open_interest(candidates: pd.DataFrame, *, cache_dir: Path) -> pd.DataFrame:
    """Attach 5m open-interest context available at each decision timestamp.

    Open interest is not available on 1m in this project. For any lab timeframe,
    the feature is the last cached 5m OI row with timestamp <= decision time.
    Missing OI stays missing and is reported through oi_status.
    """
    if candidates.empty or "symbol" not in candidates.columns:
        return candidates

    result = candidates.copy()
    oi_rows_by_index: dict[int, dict[str, object]] = {}
    for symbol, group in result.groupby("symbol", sort=False):
        oi_frame, status = _read_oi_frame(cache_dir, str(symbol))
        enriched_rows = _enrich_symbol_oi(group, oi_frame, status)
        for row_index, oi_values in zip(group.index, enriched_rows, strict=True):
            oi_rows_by_index[int(row_index)] = oi_values

    oi_frame = pd.DataFrame.from_dict(oi_rows_by_index, orient="index").sort_index()
    for column in oi_frame.columns:
        result[column] = oi_frame[column]
    if "decision_return_from_start_open" in result.columns:
        decision_return = result["decision_return_from_start_open"].astype(float)
        price_direction = np.select(
            [decision_return.gt(0.001), decision_return.lt(-0.001)],
            ["price_up", "price_down"],
            default="price_flat",
        )
        oi_change = result["oi_change_pct_3x5m"].astype(float)
        oi_direction = np.select(
            [oi_change.gt(0.0), oi_change.lt(0.0)],
            ["oi_up", "oi_down"],
            default="oi_flat",
        )
        result["oi_price_interaction_3x5m"] = [
            f"{oi}_{price}" if status == "ok" else f"oi_{status}"
            for oi, price, status in zip(oi_direction, price_direction, result["oi_status"].astype(str), strict=True)
        ]
        result["oi_change_pct_3x5m_per_decision_return"] = [
            _safe_divide(float(oi), abs(float(ret)))
            for oi, ret in zip(oi_change, decision_return, strict=True)
        ]
    return result


def build_oi_context_status(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or "oi_status" not in candidates.columns:
        return pd.DataFrame(
            [
                {
                    "oi_timeframe": OI_TIMEFRAME,
                    "symbols": 0,
                    "rows": 0,
                    "oi_status": "no_candidates",
                }
            ]
        )
    status = (
        candidates.groupby(["oi_timeframe", "oi_status"], dropna=False)
        .agg(
            rows=("symbol", "size"),
            symbols=("symbol", "nunique"),
            min_oi_age_ms=("oi_age_ms", "min"),
            max_oi_age_ms=("oi_age_ms", "max"),
        )
        .reset_index()
    )
    return status


def write_anomaly_lab_artifacts(
    config: AnomalyLabConfig,
    *,
    symbols: Iterable[str] | None = None,
) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_anomaly_lab_rows(config, symbols=symbols)
    rows.to_csv(config.output_dir / "anomaly_continuation_lab.csv", index=False)
    build_oi_context_status(rows).to_csv(config.output_dir / "oi_context_status.csv", index=False)
    if not rows.empty and "outcome_label" in rows.columns:
        summary = (
            rows.groupby("outcome_label", dropna=False)
            .agg(
                events=("symbol", "size"),
                symbols=("symbol", "nunique"),
                median_start_trade_count=("start_trade_count", "median"),
                median_start_trade_ratio=("start_trade_ratio", "median"),
                median_start_quote_ratio=("start_quote_ratio", "median"),
                median_hold_next_n_candles=("hold_count_next_n_candles", "median"),
                median_verticality_score=("start_verticality_score", "median"),
                median_future_ret_high=("future_ret_high_after_decision", "median"),
                median_future_dd_low=("future_dd_low_after_decision", "median"),
            )
            .reset_index()
        )
    else:
        summary = pd.DataFrame()
    summary.to_csv(config.output_dir / "anomaly_continuation_summary.csv", index=False)
    return config.output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--baseline-candles", type=int, default=DEFAULT_BASELINE_CANDLES)
    parser.add_argument("--confirmation-candles", type=int, default=DEFAULT_CONFIRMATION_CANDLES)
    parser.add_argument("--forward-high-candles", type=int, default=DEFAULT_FORWARD_HIGH_CANDLES)
    parser.add_argument("--forward-low-candles", type=int, default=DEFAULT_FORWARD_LOW_CANDLES)
    parser.add_argument("--min-quote-ratio-start", type=float, default=DEFAULT_MIN_QUOTE_RATIO_START)
    parser.add_argument("--min-trade-ratio-start", type=float, default=DEFAULT_MIN_TRADE_RATIO_START)
    parser.add_argument("--big-move-threshold", type=float, default=DEFAULT_BIG_MOVE_THRESHOLD)
    parser.add_argument("--symbols", nargs="*", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = AnomalyLabConfig(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        timeframe=args.timeframe,
        days=args.days,
        end_timestamp_ms=args.end_timestamp_ms,
        baseline_candles=args.baseline_candles,
        confirmation_candles=args.confirmation_candles,
        forward_high_candles=args.forward_high_candles,
        forward_low_candles=args.forward_low_candles,
        min_quote_ratio_start=args.min_quote_ratio_start,
        min_trade_ratio_start=args.min_trade_ratio_start,
        big_move_threshold=args.big_move_threshold,
    )
    output_dir = write_anomaly_lab_artifacts(config, symbols=args.symbols)
    print(f"wrote anomaly continuation lab artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
