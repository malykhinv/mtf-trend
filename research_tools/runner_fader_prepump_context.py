"""DANGER offline runner/fader pre-pump context study.

This script studies whether traded pump events that later run can be separated
from faders using only HTF context that was available before the pump start.
It is deliberately offline research and must not be wired into live execution
until a walk-forward test proves stable separation.
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

import numpy as np
import pandas as pd

from research_tools.anomaly_continuation_lab import (
    DERIVATIVES_CONTEXT_SPECS,
    OI_TIMEFRAME,
    _emit_progress,
)

_MINUTE_MS = 60_000
_HOUR_MS = 3_600_000
DEFAULT_PREPUMP_CONTEXT_WINDOWS = "30m,1h,2h,6h"
DEFAULT_PREPUMP_CONTEXT_TIMEFRAME = "5m"
_DEFAULT_WINDOWS = DEFAULT_PREPUMP_CONTEXT_WINDOWS
_DEFAULT_CONTEXT_TIMEFRAME = DEFAULT_PREPUMP_CONTEXT_TIMEFRAME
_REQUIRED_TRADE_COLUMNS = (
    "symbol",
    "status",
    "anomaly_timestamp_ms",
    "decision_timestamp_ms",
    "entry_timestamp_ms",
    "exit_timestamp_ms",
    "exit_reason",
    "net_return",
    "mfe_pct",
    "mae_pct",
)
_OHLCV_COLUMNS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "number_of_trades",
)
_OPTIONAL_SPOT_COLUMNS = ("taker_buy_volume", "taker_buy_quote_volume")


@dataclass(frozen=True, slots=True)
class PrepumpContextConfig:
    cache_dir: Path
    output_dir: Path
    artifact_dirs: tuple[Path, ...]
    context_timeframe: str = _DEFAULT_CONTEXT_TIMEFRAME
    windows: tuple[tuple[str, int], ...] = ()
    min_coverage_ratio: float = 0.80
    runner_min_net_return: float = 0.01
    runner_min_mfe_pct: float = 0.03
    fader_max_net_return: float = -0.005
    fader_max_mfe_pct: float = 0.01
    event_dedup_window_ms: int = 10 * _MINUTE_MS


def _cache_symbol_dir_name(symbol: str) -> str:
    return quote(symbol, safe="")


def _symbol_from_cache_dir_name(value: str) -> str:
    return unquote(value)


def _timestamp_to_utc(timestamp_ms: int | float | None) -> str:
    if timestamp_ms is None or not np.isfinite(float(timestamp_ms)):
        return ""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _timeframe_to_ms(timeframe: str) -> int:
    value = timeframe.strip().lower()
    if not value:
        raise ValueError("empty timeframe")
    unit = value[-1]
    amount = int(value[:-1])
    if amount <= 0:
        raise ValueError(f"invalid timeframe: {timeframe}")
    if unit == "s":
        return amount * 1_000
    if unit == "m":
        return amount * _MINUTE_MS
    if unit == "h":
        return amount * _HOUR_MS
    if unit == "d":
        return amount * 24 * _HOUR_MS
    raise ValueError(f"unsupported timeframe: {timeframe}")


def parse_prepump_windows(value: str) -> tuple[tuple[str, int], ...]:
    windows: list[tuple[str, int]] = []
    for raw in str(value).split(","):
        label = raw.strip().lower()
        if not label:
            continue
        windows.append((label, _timeframe_to_ms(label)))
    if not windows:
        raise ValueError("at least one pre-pump window is required")
    return tuple(windows)


def _parse_windows(value: str) -> tuple[tuple[str, int], ...]:
    return parse_prepump_windows(value)


def _safe_float(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _safe_divide(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _pct_change(last: float, first: float) -> float:
    return _safe_divide(last - first, first)


def _slope_per_hour(values: pd.Series, timestamps: pd.Series) -> float:
    if len(values) < 3:
        return float("nan")
    y = pd.to_numeric(values, errors="coerce").astype(float).to_numpy()
    x = pd.to_numeric(timestamps, errors="coerce").astype(float).to_numpy() / _HOUR_MS
    mask = np.isfinite(y) & np.isfinite(x)
    if int(mask.sum()) < 3:
        return float("nan")
    y = y[mask]
    x = x[mask]
    if float(np.nanmax(x) - np.nanmin(x)) <= 1e-12:
        return float("nan")
    return float(np.polyfit(x - float(np.nanmin(x)), y, 1)[0])


def _log_slope_per_hour(values: pd.Series, timestamps: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    numeric = np.log1p(numeric.clip(lower=0.0))
    return _slope_per_hour(pd.Series(numeric, index=values.index), timestamps)


def _split_second_to_first_ratio(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").astype(float).dropna()
    if len(numeric) < 4:
        return float("nan")
    half = len(numeric) // 2
    first = float(numeric.iloc[:half].sum())
    second = float(numeric.iloc[half:].sum())
    return _safe_divide(second, first)


def _last_n_vs_median(values: pd.Series, *, n: int = 3) -> float:
    numeric = pd.to_numeric(values, errors="coerce").astype(float).dropna()
    if len(numeric) < n + 2:
        return float("nan")
    recent = float(numeric.iloc[-n:].mean())
    baseline = float(numeric.iloc[:-n].median())
    return _safe_divide(recent, baseline)


def _select_window(frame: pd.DataFrame, *, anchor_ms: int, window_ms: int) -> pd.DataFrame:
    # Strictly before anchor: these features must be available before pump start.
    start_ms = int(anchor_ms - window_ms)
    selected = frame.loc[(frame["timestamp"] >= start_ms) & (frame["timestamp"] < anchor_ms)].copy()
    selected.sort_values("timestamp", inplace=True)
    return selected


def _read_parquet_frame(path: Path, *, columns: Iterable[str] | None = None) -> tuple[pd.DataFrame | None, str]:
    if not path.exists():
        return None, "missing_frame"
    try:
        frame = pd.read_parquet(path, columns=list(columns) if columns is not None else None)
    except Exception as exc:
        return None, f"read_error:{type(exc).__name__}"
    if "timestamp" not in frame.columns:
        return None, "missing_timestamp"
    frame = frame.copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame.dropna(subset=["timestamp"], inplace=True)
    if frame.empty:
        return None, "empty_frame"
    frame["timestamp"] = frame["timestamp"].astype(np.int64)
    frame.sort_values("timestamp", inplace=True)
    frame.drop_duplicates("timestamp", keep="last", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame, "ok"


def _spot_frame_path(cache_dir: Path, symbol: str, timeframe: str) -> Path:
    return cache_dir / _cache_symbol_dir_name(symbol) / timeframe / "data.parquet"


def _read_spot_frame(cache_dir: Path, symbol: str, timeframe: str) -> tuple[pd.DataFrame | None, str]:
    path = _spot_frame_path(cache_dir, symbol, timeframe)
    columns = [*_OHLCV_COLUMNS, *_OPTIONAL_SPOT_COLUMNS]
    frame, status = _read_parquet_frame(path)
    if frame is None:
        return None, status
    missing = [column for column in _OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        return None, "missing_columns:" + ",".join(missing)
    selected_columns = [column for column in columns if column in frame.columns]
    selected = frame.loc[:, selected_columns].copy()
    for column in selected_columns:
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
    selected.dropna(subset=["timestamp", "open", "high", "low", "close"], inplace=True)
    if selected.empty:
        return None, "empty_ohlcv"
    selected.sort_values("timestamp", inplace=True)
    selected.reset_index(drop=True, inplace=True)
    return selected, "ok"


def _oi_frame_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / _cache_symbol_dir_name(symbol) / OI_TIMEFRAME / "data.parquet"


def _read_oi_frame(cache_dir: Path, symbol: str) -> tuple[pd.DataFrame | None, str]:
    frame, status = _read_parquet_frame(_oi_frame_path(cache_dir, symbol), columns=("timestamp", "open_interest"))
    if frame is None:
        return None, status
    if "open_interest" not in frame.columns:
        return None, "missing_column:open_interest"
    frame["open_interest"] = pd.to_numeric(frame["open_interest"], errors="coerce")
    frame.dropna(subset=["open_interest"], inplace=True)
    if frame.empty:
        return None, "empty_oi"
    return frame, "ok"


def _context_path(cache_dir: Path, symbol: str, spec: dict[str, object]) -> Path:
    path = cache_dir / _cache_symbol_dir_name(symbol)
    for part in spec["path_parts"]:
        path /= str(part)
    return path / "data.parquet"


def _read_context_frame(cache_dir: Path, symbol: str, spec: dict[str, object]) -> tuple[pd.DataFrame | None, str]:
    required = tuple(str(column) for column in spec["required_columns"])
    value_columns = tuple(str(column) for column in spec["value_columns"])
    frame, status = _read_parquet_frame(_context_path(cache_dir, symbol, spec))
    if frame is None:
        return None, status
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return None, "missing_columns:" + ",".join(missing)
    selected_columns = ["timestamp", *[column for column in value_columns if column in frame.columns]]
    selected = frame.loc[:, selected_columns].copy()
    for column in selected_columns:
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
    selected.dropna(subset=["timestamp"], inplace=True)
    if selected.empty:
        return None, "empty_context"
    selected.sort_values("timestamp", inplace=True)
    selected.drop_duplicates("timestamp", keep="last", inplace=True)
    selected.reset_index(drop=True, inplace=True)
    return selected, "ok"


def _spot_window_features(
    segment: pd.DataFrame,
    *,
    expected_candles: int,
    label: str,
) -> dict[str, object]:
    prefix = f"pre_{label}"
    values: dict[str, object] = {
        f"{prefix}_spot_candles": int(len(segment)),
        f"{prefix}_spot_expected_candles": int(expected_candles),
        f"{prefix}_spot_coverage_ratio": _safe_divide(float(len(segment)), float(expected_candles)),
    }
    if segment.empty:
        values[f"{prefix}_spot_status"] = "empty_window"
        return values
    opens = segment["open"].astype(float)
    highs = segment["high"].astype(float)
    lows = segment["low"].astype(float)
    closes = segment["close"].astype(float)
    first_open = float(opens.iloc[0])
    last_close = float(closes.iloc[-1])
    high_max = float(highs.max())
    low_min = float(lows.min())
    values.update(
        {
            f"{prefix}_spot_status": "ok",
            f"{prefix}_price_return_pct": _pct_change(last_close, first_open),
            f"{prefix}_price_range_pct": _safe_divide(high_max - low_min, first_open),
            f"{prefix}_price_close_position_in_range": _safe_divide(last_close - low_min, high_max - low_min),
            f"{prefix}_price_green_share": float((closes >= opens).mean()),
            f"{prefix}_price_slope_pct_per_hour": _safe_divide(
                _slope_per_hour(closes / first_open - 1.0, segment["timestamp"]),
                1.0,
            ),
            f"{prefix}_price_second_half_return_pct": _pct_change(float(closes.iloc[-1]), float(opens.iloc[len(segment) // 2])),
            f"{prefix}_quote_volume_sum": float(segment["quote_volume"].astype(float).sum()),
            f"{prefix}_quote_volume_second_to_first_ratio": _split_second_to_first_ratio(segment["quote_volume"]),
            f"{prefix}_quote_volume_last3_vs_prior_median": _last_n_vs_median(segment["quote_volume"]),
            f"{prefix}_quote_volume_log_slope_per_hour": _log_slope_per_hour(segment["quote_volume"], segment["timestamp"]),
            f"{prefix}_trade_count_sum": float(segment["number_of_trades"].astype(float).sum()),
            f"{prefix}_trade_count_second_to_first_ratio": _split_second_to_first_ratio(segment["number_of_trades"]),
            f"{prefix}_trade_count_last3_vs_prior_median": _last_n_vs_median(segment["number_of_trades"]),
            f"{prefix}_trade_count_log_slope_per_hour": _log_slope_per_hour(segment["number_of_trades"], segment["timestamp"]),
        }
    )
    avg_trade = [
        _safe_divide(float(q), float(n))
        for q, n in zip(segment["quote_volume"], segment["number_of_trades"], strict=True)
    ]
    avg_trade_series = pd.Series(avg_trade)
    values.update(
        {
            f"{prefix}_avg_trade_quote_size_last3_vs_prior_median": _last_n_vs_median(avg_trade_series),
            f"{prefix}_avg_trade_quote_size_log_slope_per_hour": _log_slope_per_hour(avg_trade_series, segment["timestamp"].reset_index(drop=True)),
        }
    )
    if "taker_buy_quote_volume" in segment.columns:
        quote = segment["quote_volume"].astype(float)
        taker = segment["taker_buy_quote_volume"].astype(float)
        share = pd.Series(
            [_safe_divide(float(tbq), float(qv)) for tbq, qv in zip(taker, quote, strict=True)],
            index=segment.index,
        )
        share = share.replace([np.inf, -np.inf], np.nan)
        values.update(
            {
                f"{prefix}_taker_buy_quote_share_mean": float(share.mean()) if share.notna().any() else float("nan"),
                f"{prefix}_taker_buy_quote_share_last3": float(share.dropna().iloc[-3:].mean()) if len(share.dropna()) else float("nan"),
                f"{prefix}_taker_buy_quote_share_last3_vs_prior_mean": _safe_divide(
                    float(share.dropna().iloc[-3:].mean()) if len(share.dropna()) >= 3 else float("nan"),
                    float(share.dropna().iloc[:-3].mean()) if len(share.dropna()) >= 6 else float("nan"),
                ),
            }
        )
    return values


def compute_spot_prepump_window_features(
    frame: pd.DataFrame,
    *,
    anchor_ms: int,
    timeframe_ms: int,
    windows: tuple[tuple[str, int], ...],
    min_coverage_ratio: float,
) -> dict[str, object]:
    """Return the P212 spot/flow pre-pump feature subset for a live/cache-only anchor.

    The anchor is exclusive. The helper intentionally computes only price/volume/trade/taker
    spot features from the provided frame; it does not fetch missing data and it does not
    infer OI or derivatives context. Callers must surface missing/insufficient coverage.
    """

    if timeframe_ms <= 0:
        raise ValueError(f"timeframe_ms must be positive, got {timeframe_ms!r}")
    if not windows:
        raise ValueError("at least one pre-pump window is required")
    if not math.isfinite(float(min_coverage_ratio)) or float(min_coverage_ratio) < 0.0:
        raise ValueError(f"min_coverage_ratio must be finite and >= 0, got {min_coverage_ratio!r}")
    required_columns = {"timestamp", "open", "high", "low", "close", "quote_volume", "number_of_trades"}
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise ValueError("missing spot prepump columns: " + ",".join(missing_columns))

    prepared = frame.copy()
    numeric_columns = set(required_columns)
    if "taker_buy_quote_volume" in prepared.columns:
        numeric_columns.add("taker_buy_quote_volume")
    for column in sorted(numeric_columns):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.loc[prepared["timestamp"].notna()].copy()
    if prepared.empty:
        raise ValueError("empty spot prepump frame")
    prepared["timestamp"] = prepared["timestamp"].astype(np.int64)
    prepared.sort_values("timestamp", inplace=True)
    prepared.drop_duplicates("timestamp", keep="last", inplace=True)

    output: dict[str, object] = {}
    for label, window_ms in windows:
        expected_candles = max(int(math.floor(int(window_ms) / int(timeframe_ms))), 1)
        segment = _select_window(prepared, anchor_ms=int(anchor_ms), window_ms=int(window_ms))
        features = _spot_window_features(segment, expected_candles=expected_candles, label=str(label))
        coverage_key = f"pre_{label}_spot_coverage_ratio"
        status_key = f"pre_{label}_spot_status"
        coverage = _safe_float(features.get(coverage_key))
        if np.isfinite(coverage) and coverage < float(min_coverage_ratio):
            features[status_key] = "insufficient_coverage"
        output.update(features)
    return output


def _single_value_window_features(
    frame: pd.DataFrame | None,
    *,
    status: str,
    anchor_ms: int,
    window_ms: int,
    expected_candles: int,
    label: str,
    prefix: str,
    value_columns: Iterable[str],
) -> dict[str, object]:
    output_prefix = f"pre_{label}_{prefix}"
    values: dict[str, object] = {
        f"{output_prefix}_status": status,
        f"{output_prefix}_rows": 0,
        f"{output_prefix}_expected_rows": int(expected_candles),
        f"{output_prefix}_coverage_ratio": float("nan"),
    }
    if frame is None or status != "ok":
        return values
    segment = _select_window(frame, anchor_ms=anchor_ms, window_ms=window_ms)
    values[f"{output_prefix}_rows"] = int(len(segment))
    values[f"{output_prefix}_coverage_ratio"] = _safe_divide(float(len(segment)), float(expected_candles))
    if segment.empty:
        values[f"{output_prefix}_status"] = "empty_window"
        return values
    values[f"{output_prefix}_status"] = "ok"
    for column in value_columns:
        if column not in segment.columns:
            values[f"{output_prefix}_{column}_status"] = "missing_column"
            continue
        series = pd.to_numeric(segment[column], errors="coerce").astype(float).dropna()
        if series.empty:
            values[f"{output_prefix}_{column}_status"] = "empty_values"
            continue
        timestamps = segment.loc[series.index, "timestamp"]
        first = float(series.iloc[0])
        last = float(series.iloc[-1])
        values.update(
            {
                f"{output_prefix}_{column}_status": "ok",
                f"{output_prefix}_{column}_first": first,
                f"{output_prefix}_{column}_last": last,
                f"{output_prefix}_{column}_change": last - first,
                f"{output_prefix}_{column}_change_pct": _pct_change(last, first),
                f"{output_prefix}_{column}_slope_per_hour": _slope_per_hour(series.reset_index(drop=True), timestamps.reset_index(drop=True)),
                f"{output_prefix}_{column}_last3_vs_prior_median": _last_n_vs_median(series.reset_index(drop=True)),
            }
        )
    return values


def _label_trade(row: pd.Series, config: PrepumpContextConfig) -> str:
    status = str(row.get("status", ""))
    if status != "closed":
        return "not_closed"
    net_return = _safe_float(row.get("net_return"))
    mfe_pct = _safe_float(row.get("mfe_pct"))
    exit_reason = str(row.get("exit_reason", ""))
    if np.isfinite(net_return) and net_return <= config.fader_max_net_return:
        return "fader"
    if exit_reason == "stop_loss" and (not np.isfinite(net_return) or net_return < 0.0):
        return "fader"
    if np.isfinite(mfe_pct) and mfe_pct <= config.fader_max_mfe_pct and np.isfinite(net_return) and net_return <= 0.0:
        return "fader"
    if np.isfinite(net_return) and np.isfinite(mfe_pct):
        if net_return >= config.runner_min_net_return and mfe_pct >= config.runner_min_mfe_pct:
            return "runner"
    return "mixed"


def _discover_trade_files(artifact_dirs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for artifact_dir in artifact_dirs:
        if artifact_dir.is_file() and artifact_dir.name == "anomaly_trades.csv":
            files.append(artifact_dir)
            continue
        direct = artifact_dir / "anomaly_trades.csv"
        if direct.exists():
            files.append(direct)
            continue
        files.extend(sorted(artifact_dir.glob("*/anomaly_trades.csv")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _load_trades(config: PrepumpContextConfig) -> pd.DataFrame:
    files = _discover_trade_files(config.artifact_dirs)
    if not files:
        raise ValueError("no anomaly_trades.csv files found")
    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path)
        missing = [column for column in _REQUIRED_TRADE_COLUMNS if column not in frame.columns]
        for column in missing:
            frame[column] = np.nan
        frame["source_trade_file"] = str(path)
        frame["source_run_id"] = path.parent.name
        frames.append(frame)
    trades = pd.concat(frames, ignore_index=True, sort=False)
    trades = trades.loc[trades["status"].astype(str).eq("closed")].copy()
    for column in (
        "anomaly_timestamp_ms",
        "decision_timestamp_ms",
        "entry_timestamp_ms",
        "exit_timestamp_ms",
        "net_return",
        "mfe_pct",
        "mae_pct",
    ):
        trades[column] = pd.to_numeric(trades[column], errors="coerce")
    trades.dropna(subset=["symbol", "anomaly_timestamp_ms"], inplace=True)
    trades["prepump_label"] = [str(_label_trade(row, config)) for _, row in trades.iterrows()]
    trades["prepump_anchor_timestamp_ms"] = trades["anomaly_timestamp_ms"].astype(np.int64)
    trades["prepump_anchor_timestamp_utc"] = [_timestamp_to_utc(value) for value in trades["prepump_anchor_timestamp_ms"]]
    trades.sort_values(["symbol", "prepump_anchor_timestamp_ms", "source_run_id"], inplace=True)
    trades.reset_index(drop=True, inplace=True)
    return trades


def _assign_event_ids(trades: pd.DataFrame, *, event_dedup_window_ms: int) -> pd.DataFrame:
    result = trades.copy()
    event_ids: list[str] = []
    for symbol, group in result.groupby("symbol", sort=False):
        current_event_index = -1
        current_event_start: int | None = None
        for row_index, row in group.sort_values("prepump_anchor_timestamp_ms").iterrows():
            anchor = int(row["prepump_anchor_timestamp_ms"])
            if current_event_start is None or anchor - current_event_start > event_dedup_window_ms:
                current_event_index += 1
                current_event_start = anchor
            event_ids.append((int(row_index), f"{symbol}|{current_event_start}|{current_event_index}"))
    event_id_by_index = {idx: event_id for idx, event_id in event_ids}
    result["prepump_event_id"] = [event_id_by_index[int(idx)] for idx in result.index]
    return result


def _build_context_rows(config: PrepumpContextConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = _assign_event_ids(_load_trades(config), event_dedup_window_ms=config.event_dedup_window_ms)
    spot_cache: dict[str, tuple[pd.DataFrame | None, str]] = {}
    oi_cache: dict[str, tuple[pd.DataFrame | None, str]] = {}
    derivative_cache: dict[tuple[str, str], tuple[pd.DataFrame | None, str, tuple[str, ...]]] = {}
    rows: list[dict[str, object]] = []
    status_rows: list[dict[str, object]] = []
    tf_ms = _timeframe_to_ms(config.context_timeframe)
    oi_tf_ms = _timeframe_to_ms(OI_TIMEFRAME)
    started_at = time.monotonic()
    next_progress_pct = 0
    for done, (_, trade) in enumerate(trades.iterrows(), start=1):
        symbol = str(trade["symbol"])
        anchor_ms = int(trade["prepump_anchor_timestamp_ms"])
        if symbol not in spot_cache:
            spot_cache[symbol] = _read_spot_frame(config.cache_dir, symbol, config.context_timeframe)
        if symbol not in oi_cache:
            oi_cache[symbol] = _read_oi_frame(config.cache_dir, symbol)
        spot_frame, spot_status = spot_cache[symbol]
        oi_frame, oi_status = oi_cache[symbol]
        base = {
            "symbol": symbol,
            "source_run_id": trade.get("source_run_id", ""),
            "source_trade_file": trade.get("source_trade_file", ""),
            "prepump_event_id": trade.get("prepump_event_id", ""),
            "prepump_label": trade.get("prepump_label", ""),
            "status": trade.get("status", ""),
            "exit_reason": trade.get("exit_reason", ""),
            "net_return": _safe_float(trade.get("net_return")),
            "mfe_pct": _safe_float(trade.get("mfe_pct")),
            "mae_pct": _safe_float(trade.get("mae_pct")),
            "pump_category_id": trade.get("pump_category_id", ""),
            "setup_timeframe": trade.get("setup_timeframe", ""),
            "entry_timeframe": trade.get("entry_timeframe", ""),
            "anomaly_timestamp_ms": int(trade.get("anomaly_timestamp_ms")),
            "anomaly_timestamp_utc": trade.get("anomaly_timestamp_utc", ""),
            "decision_timestamp_ms": _safe_float(trade.get("decision_timestamp_ms")),
            "decision_timestamp_utc": trade.get("decision_timestamp_utc", ""),
            "entry_timestamp_ms": _safe_float(trade.get("entry_timestamp_ms")),
            "entry_timestamp_utc": trade.get("entry_timestamp_utc", ""),
            "prepump_feature_anchor": "anomaly_timestamp_ms_exclusive",
            "prepump_context_timeframe": config.context_timeframe,
            "prepump_min_coverage_ratio": float(config.min_coverage_ratio),
            "prepump_status": "ok",
        }
        row = dict(base)
        for label, window_ms in config.windows:
            expected_spot = max(int(math.floor(window_ms / tf_ms)), 1)
            if spot_frame is None:
                row[f"pre_{label}_spot_status"] = spot_status
                row[f"pre_{label}_spot_candles"] = 0
                row[f"pre_{label}_spot_expected_candles"] = expected_spot
                row[f"pre_{label}_spot_coverage_ratio"] = float("nan")
            else:
                segment = _select_window(spot_frame, anchor_ms=anchor_ms, window_ms=window_ms)
                row.update(_spot_window_features(segment, expected_candles=expected_spot, label=label))
                coverage = _safe_float(row.get(f"pre_{label}_spot_coverage_ratio"))
                if np.isfinite(coverage) and coverage < config.min_coverage_ratio:
                    row[f"pre_{label}_spot_status"] = "insufficient_coverage"
            expected_oi = max(int(math.floor(window_ms / oi_tf_ms)), 1)
            row.update(
                _single_value_window_features(
                    oi_frame,
                    status=oi_status,
                    anchor_ms=anchor_ms,
                    window_ms=window_ms,
                    expected_candles=expected_oi,
                    label=label,
                    prefix="oi",
                    value_columns=("open_interest",),
                )
            )
            for spec in DERIVATIVES_CONTEXT_SPECS:
                prefix = str(spec["prefix"])
                cache_key = (symbol, prefix)
                if cache_key not in derivative_cache:
                    frame, status = _read_context_frame(config.cache_dir, symbol, spec)
                    derivative_cache[cache_key] = (frame, status, tuple(str(c) for c in spec["value_columns"]))
                context_frame, context_status, value_columns = derivative_cache[cache_key]
                expected_context = max(int(math.floor(window_ms / int(spec["expected_interval_ms"]))), 1)
                row.update(
                    _single_value_window_features(
                        context_frame,
                        status=context_status,
                        anchor_ms=anchor_ms,
                        window_ms=window_ms,
                        expected_candles=expected_context,
                        label=label,
                        prefix=prefix,
                        value_columns=value_columns,
                    )
                )
        rows.append(row)
        for label, _ in config.windows:
            for source in ("spot", "oi", *(str(spec["prefix"]) for spec in DERIVATIVES_CONTEXT_SPECS)):
                status_column = f"pre_{label}_{source}_status"
                coverage_column = f"pre_{label}_{source}_coverage_ratio"
                if source == "spot":
                    coverage_column = f"pre_{label}_spot_coverage_ratio"
                status_rows.append(
                    {
                        "symbol": symbol,
                        "source_run_id": trade.get("source_run_id", ""),
                        "prepump_event_id": trade.get("prepump_event_id", ""),
                        "prepump_label": trade.get("prepump_label", ""),
                        "window": label,
                        "source": source,
                        "status": row.get(status_column, "missing_status"),
                        "coverage_ratio": row.get(coverage_column, np.nan),
                    }
                )
        current_pct = int(100 * done / max(len(trades), 1))
        if current_pct >= next_progress_pct or done == len(trades):
            _emit_progress(label="runner/fader prepump context", done=done, total=len(trades), started_at=started_at)
            next_progress_pct = current_pct + 5
    return pd.DataFrame(rows), pd.DataFrame(status_rows)


def _numeric_columns(frame: pd.DataFrame) -> list[str]:
    blocked = {
        "anomaly_timestamp_ms",
        "decision_timestamp_ms",
        "entry_timestamp_ms",
        "exit_timestamp_ms",
        "net_return",
        "mfe_pct",
        "mae_pct",
    }
    columns: list[str] = []
    for column in frame.columns:
        if column in blocked:
            continue
        if column.startswith("pre_"):
            numeric = pd.to_numeric(frame[column], errors="coerce")
            if numeric.notna().any():
                columns.append(column)
    return columns


def _build_label_summary(context: pd.DataFrame) -> pd.DataFrame:
    if context.empty:
        return pd.DataFrame()
    return (
        context.groupby("prepump_label", dropna=False)
        .agg(
            rows=("symbol", "size"),
            events=("prepump_event_id", "nunique"),
            symbols=("symbol", "nunique"),
            mean_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            mean_mfe_pct=("mfe_pct", "mean"),
            mean_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
        .sort_values(["rows", "prepump_label"], ascending=[False, True])
    )


def _build_feature_separation(context: pd.DataFrame) -> pd.DataFrame:
    runner = context.loc[context["prepump_label"].eq("runner")]
    fader = context.loc[context["prepump_label"].eq("fader")]
    rows: list[dict[str, object]] = []
    if runner.empty or fader.empty:
        return pd.DataFrame(
            [
                {
                    "status": "insufficient_runner_or_fader_rows",
                    "runner_rows": int(len(runner)),
                    "fader_rows": int(len(fader)),
                }
            ]
        )
    for column in _numeric_columns(context):
        runner_values = pd.to_numeric(runner[column], errors="coerce").dropna()
        fader_values = pd.to_numeric(fader[column], errors="coerce").dropna()
        if len(runner_values) < 2 or len(fader_values) < 2:
            continue
        runner_mean = float(runner_values.mean())
        fader_mean = float(fader_values.mean())
        runner_std = float(runner_values.std(ddof=1))
        fader_std = float(fader_values.std(ddof=1))
        pooled = math.sqrt(max((runner_std**2 + fader_std**2) / 2.0, 0.0))
        rows.append(
            {
                "status": "ok",
                "feature": column,
                "runner_count": int(len(runner_values)),
                "fader_count": int(len(fader_values)),
                "runner_mean": runner_mean,
                "fader_mean": fader_mean,
                "mean_diff_runner_minus_fader": runner_mean - fader_mean,
                "mean_ratio_runner_to_fader": _safe_divide(runner_mean, fader_mean),
                "standardized_diff": _safe_divide(runner_mean - fader_mean, pooled),
                "runner_median": float(runner_values.median()),
                "fader_median": float(fader_values.median()),
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty and "standardized_diff" in result.columns:
        result["abs_standardized_diff"] = result["standardized_diff"].abs()
        result.sort_values("abs_standardized_diff", ascending=False, inplace=True)
        result.drop(columns=["abs_standardized_diff"], inplace=True)
    return result


def _build_status_summary(status: pd.DataFrame) -> pd.DataFrame:
    if status.empty:
        return pd.DataFrame()
    return (
        status.groupby(["window", "source", "status"], dropna=False)
        .agg(
            rows=("symbol", "size"),
            symbols=("symbol", "nunique"),
            mean_coverage_ratio=("coverage_ratio", "mean"),
            min_coverage_ratio=("coverage_ratio", "min"),
        )
        .reset_index()
        .sort_values(["window", "source", "rows"], ascending=[True, True, False])
    )


def write_runner_fader_prepump_context(config: PrepumpContextConfig) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    context, status = _build_context_rows(config)
    label_summary = _build_label_summary(context)
    feature_separation = _build_feature_separation(context)
    status_summary = _build_status_summary(status)
    context.to_csv(config.output_dir / "runner_fader_prepump_context.csv", index=False)
    label_summary.to_csv(config.output_dir / "runner_fader_prepump_label_summary.csv", index=False)
    feature_separation.to_csv(config.output_dir / "runner_fader_prepump_feature_separation.csv", index=False)
    status.to_csv(config.output_dir / "runner_fader_prepump_context_status.csv", index=False)
    status_summary.to_csv(config.output_dir / "runner_fader_prepump_context_status_summary.csv", index=False)
    run_config = pd.DataFrame(
        [
            {
                "experiment": "DANGER_runner_fader_prepump_context_v1",
                "cache_dir": str(config.cache_dir),
                "artifact_dirs": ",".join(str(path) for path in config.artifact_dirs),
                "context_timeframe": config.context_timeframe,
                "windows": ",".join(label for label, _ in config.windows),
                "feature_anchor": "anomaly_timestamp_ms_exclusive",
                "min_coverage_ratio": config.min_coverage_ratio,
                "runner_min_net_return": config.runner_min_net_return,
                "runner_min_mfe_pct": config.runner_min_mfe_pct,
                "fader_max_net_return": config.fader_max_net_return,
                "fader_max_mfe_pct": config.fader_max_mfe_pct,
                "event_dedup_window_ms": config.event_dedup_window_ms,
                "note": "Offline separability study only; do not wire into live without walk-forward validation.",
            }
        ]
    )
    run_config.to_csv(config.output_dir / "runner_fader_prepump_run_config.csv", index=False)
    return config.output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".output/cache"))
    parser.add_argument("--output-dir", type=Path, default=Path(".output/results/runner_fader_prepump_context"))
    parser.add_argument(
        "--artifact-dir",
        dest="artifact_dirs",
        type=Path,
        action="append",
        required=True,
        help="Backtest artifact directory, parent directory containing run subdirs, or anomaly_trades.csv path.",
    )
    parser.add_argument("--context-timeframe", default=_DEFAULT_CONTEXT_TIMEFRAME)
    parser.add_argument("--windows", default=_DEFAULT_WINDOWS)
    parser.add_argument("--min-coverage-ratio", type=float, default=0.80)
    parser.add_argument("--runner-min-net-return", type=float, default=0.01)
    parser.add_argument("--runner-min-mfe-pct", type=float, default=0.03)
    parser.add_argument("--fader-max-net-return", type=float, default=-0.005)
    parser.add_argument("--fader-max-mfe-pct", type=float, default=0.01)
    parser.add_argument("--event-dedup-window-minutes", type=float, default=10.0)
    return parser


def config_from_args(args: argparse.Namespace) -> PrepumpContextConfig:
    return PrepumpContextConfig(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        artifact_dirs=tuple(args.artifact_dirs),
        context_timeframe=str(args.context_timeframe),
        windows=_parse_windows(str(args.windows)),
        min_coverage_ratio=float(args.min_coverage_ratio),
        runner_min_net_return=float(args.runner_min_net_return),
        runner_min_mfe_pct=float(args.runner_min_mfe_pct),
        fader_max_net_return=float(args.fader_max_net_return),
        fader_max_mfe_pct=float(args.fader_max_mfe_pct),
        event_dedup_window_ms=int(float(args.event_dedup_window_minutes) * _MINUTE_MS),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = write_runner_fader_prepump_context(config_from_args(args))
    print(f"wrote runner/fader pre-pump context artifacts to {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
