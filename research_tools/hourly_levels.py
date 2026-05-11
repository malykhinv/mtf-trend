"""Hourly overhead level diagnostics for pump-continuation review."""

from __future__ import annotations

import csv
import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote

import numpy as np
import pandas as pd

from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe
from research_tools.charting import (
    CHART_AXIS_FACE,
    CHART_DOWN,
    CHART_ENTRY,
    CHART_EXIT,
    CHART_FIGURE_FACE,
    CHART_GRID,
    CHART_LEVEL,
    CHART_MUTED,
    CHART_SAVEFIG_KWARGS,
    CHART_TEXT,
    CHART_UP,
    annotate_axis_price_tag,
    build_tick_labels_from_timestamps,
    build_tick_positions_from_timestamps,
    build_tick_timestamps,
    configure_plot_axes,
    draw_candles,
    format_chart_symbol,
    format_price_label,
    resolve_axis_tag_positions,
    resolve_timestamp_plot_idx,
)


@dataclass(frozen=True, slots=True)
class HourlyLevelScanConfig:
    cache_dir: Path
    output_dir: Path
    source_timeframe: Timeframe = Timeframe.M5
    symbols: tuple[str, ...] = ()
    days: int = 45
    lookback_bars: int = 720
    chart_bars: int = 240
    min_touches: int = 3
    touch_tolerance_pct: float = 0.006
    min_bounce_pct: float = 0.05
    bounce_lookahead_bars: int = 12
    min_target_room_pct: float = 0.05
    max_overhead_distance_pct: float = 0.60
    min_recent_move_pct: float = 0.05
    recent_move_lookback_bars: int = 24
    reaction_to_move_threshold: float = 0.80
    pivot_side_bars: int = 3
    break_close_tolerance_pct: float = 0.004
    break_hold_bars: int = 2
    reject_downtrend_symbols: bool = True
    reject_downtrend_levels: bool = True
    max_levels_per_symbol: int = 4
    reject_pierced_levels: bool = True
    max_level_pierce_pct: float = 0.015
    save_empty_charts: bool = False
    progress_every_symbols: int = 5
    progress_min_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class TouchReaction:
    timestamp_ms: int
    timestamp_utc: str
    touch_high: float
    close: float
    reaction_pct: float
    valid: bool


@dataclass(frozen=True, slots=True)
class HourlyLevelMetric:
    symbol: str
    level_price: float
    latest_close: float
    latest_timestamp_ms: int
    latest_timestamp_utc: str
    distance_pct: float
    raw_touch_count: int
    valid_touch_count: int
    first_touch_timestamp_ms: int
    first_touch_timestamp_utc: str
    last_touch_timestamp_ms: int
    last_touch_timestamp_utc: str
    max_reaction_pct: float
    median_valid_reaction_pct: float
    recent_move_pct: float
    reaction_to_recent_move_ratio: float
    pierce_count: int
    max_pierce_pct: float
    strength_score: float
    context: str
    trend_state: str
    chart_path: str


def _ascii_safe_chart_text(value: object) -> str:
    """Return text that matplotlib default fonts can render without missing-glyph warnings."""
    text = str(value)
    if text.isascii():
        return text
    return text.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def _safe_chart_file_stem(value: object) -> str:
    text = _ascii_safe_chart_text(value).replace("/", "_").replace(":", "_")
    text = re.sub(r"[^A-Za-z0-9_.\-]+", "_", text)
    return text.strip("._") or "symbol"


def _format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(float(seconds)) or seconds < 0.0:
        return "unknown"
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _progress_line(
    *,
    done: int,
    total: int,
    started_at: float,
    levels_found: int,
    symbols_with_levels: int,
    charts_saved: int,
    last_symbol: str,
    last_status: str,
    last_reason: str,
) -> str:
    elapsed = max(time.monotonic() - started_at, 0.0)
    rate = done / elapsed if elapsed > 0.0 else 0.0
    eta = ((total - done) / rate) if rate > 0.0 and total >= done else None
    pct = (100.0 * done / total) if total > 0 else 100.0
    symbol_text = _ascii_safe_chart_text(last_symbol)
    return (
        "1h level scan progress: "
        f"{done}/{total} ({pct:.1f}%), "
        f"elapsed={_format_duration(elapsed)}, eta={_format_duration(eta)}, "
        f"levels={levels_found}, symbols_with_levels={symbols_with_levels}, charts={charts_saved}, "
        f"last={symbol_text}, status={last_status}, reason={last_reason}"
    )


def _should_emit_progress(
    *,
    done: int,
    total: int,
    last_emit_at: float,
    config: HourlyLevelScanConfig,
) -> bool:
    if done <= 0:
        return False
    if done == total:
        return True
    if config.progress_every_symbols > 0 and done % config.progress_every_symbols == 0:
        return True
    return (time.monotonic() - last_emit_at) >= max(config.progress_min_seconds, 0.0)


@dataclass(frozen=True, slots=True)
class SymbolScanStatus:
    symbol: str
    status: str
    reason: str
    rows_source: int = 0
    rows_1h: int = 0
    levels_found: int = 0
    trend_state: str = "unknown"
    source_path: str = ""


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    if not math.isfinite(float(timestamp_ms)):
        return ""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _safe_float(value: object) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(resolved):
        return None
    return resolved


def _resolve_timeframe(value: str) -> Timeframe:
    normalized = value.strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe
    supported = ", ".join(tf.value for tf in Timeframe)
    raise ValueError(f"unsupported timeframe {value!r}; supported: {supported}")


def _discover_cached_symbols(cache_dir: Path, timeframe: Timeframe) -> list[str]:
    if not cache_dir.exists():
        return []
    symbols: list[str] = []
    for symbol_dir in sorted(path for path in cache_dir.iterdir() if path.is_dir()):
        data_path = symbol_dir / timeframe.value / "data.parquet"
        if data_path.exists():
            symbols.append(unquote(symbol_dir.name))
    return symbols


def _read_cached_frame(cache_dir: Path, symbol: str, timeframe: Timeframe) -> tuple[pd.DataFrame, Path]:
    storage = ParquetStorage(base_dir=cache_dir)
    result = storage.load_result(symbol, timeframe)
    if not result.ok:
        return pd.DataFrame(), result.path
    frame = result.frame.copy()
    frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return frame, result.path


def _prepare_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    prepared = frame.copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades"):
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
    prepared = prepared.loc[(prepared["open"] > 0.0) & (prepared["high"] > 0.0) & (prepared["low"] > 0.0) & (prepared["close"] > 0.0)].copy()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    prepared.sort_values("timestamp", inplace=True)
    prepared.drop_duplicates("timestamp", keep="last", inplace=True)
    prepared.reset_index(drop=True, inplace=True)
    return prepared


def _aggregate_to_1h(frame: pd.DataFrame, source_timeframe: Timeframe) -> pd.DataFrame:
    prepared = _prepare_ohlcv(frame)
    if source_timeframe == Timeframe.H1:
        return prepared
    ts = pd.to_datetime(prepared["timestamp"], unit="ms", utc=True)
    resample_input = prepared.copy()
    resample_input.index = ts
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in resample_input.columns:
            aggregation[column] = "sum"
    hourly = resample_input.resample("1h", label="left", closed="left").agg(aggregation)
    hourly = hourly.dropna(subset=["open", "high", "low", "close"]).copy()
    hourly["timestamp"] = (hourly.index.view("int64") // 1_000_000).astype("int64")
    hourly.reset_index(drop=True, inplace=True)
    return hourly


def _trim_to_recent_window(frame_1h: pd.DataFrame, days: int, lookback_bars: int) -> pd.DataFrame:
    if frame_1h.empty:
        return frame_1h
    latest_ts = int(frame_1h["timestamp"].max())
    min_ts = latest_ts - int(days) * 86_400_000
    trimmed = frame_1h.loc[frame_1h["timestamp"].ge(min_ts)].copy()
    if lookback_bars > 0 and len(trimmed) > lookback_bars:
        trimmed = trimmed.tail(lookback_bars).copy()
    trimmed.reset_index(drop=True, inplace=True)
    return trimmed


def _trend_state(frame: pd.DataFrame) -> str:
    if len(frame) < 72:
        return "unknown"
    recent = frame.tail(72).copy()
    closes = recent["close"].astype(float).to_numpy()
    highs = recent["high"].astype(float).to_numpy()
    if len(closes) < 2 or closes[0] <= 0.0:
        return "unknown"
    x = np.arange(len(closes), dtype=float)
    slope = float(np.polyfit(x, closes, 1)[0])
    slope_pct = slope * len(closes) / closes[0]
    ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().iloc[-1]
    first_high = float(np.nanmax(highs[:24]))
    last_high = float(np.nanmax(highs[-24:]))
    lower_highs = first_high > 0.0 and last_high < first_high * 0.97
    if slope_pct < -0.08 and closes[-1] < float(ema50) and lower_highs:
        return "downtrend"
    if slope_pct > 0.04 or closes[-1] >= float(ema50):
        return "sideways_or_up"
    return "mixed"


def _pivot_high_prices(frame: pd.DataFrame, side_bars: int) -> list[tuple[int, float]]:
    highs = frame["high"].astype(float).reset_index(drop=True)
    timestamps = frame["timestamp"].astype("int64").reset_index(drop=True)
    if len(frame) < side_bars * 2 + 1:
        return []
    pivots: list[tuple[int, float]] = []
    for idx in range(side_bars, len(frame) - side_bars):
        window = highs.iloc[idx - side_bars: idx + side_bars + 1]
        value = float(highs.iloc[idx])
        if value >= float(window.max()) and value > 0.0:
            pivots.append((int(timestamps.iloc[idx]), value))
    return pivots


@dataclass(frozen=True, slots=True)
class LevelCluster:
    level_price: float
    pivot_count: int
    first_pivot_timestamp_ms: int
    last_pivot_timestamp_ms: int
    min_price: float
    max_price: float


def _cluster_prices(pivots: list[tuple[int, float]], tolerance_pct: float) -> list[LevelCluster]:
    clusters: list[list[tuple[int, float]]] = []
    for pivot in sorted(pivots, key=lambda item: item[1]):
        _, price = pivot
        attached = False
        for cluster in clusters:
            center = float(np.median([item[1] for item in cluster]))
            if center > 0.0 and abs(price - center) / center <= tolerance_pct:
                cluster.append(pivot)
                attached = True
                break
        if not attached:
            clusters.append([pivot])

    resolved: list[LevelCluster] = []
    for cluster in clusters:
        timestamps = [item[0] for item in cluster]
        prices = [item[1] for item in cluster]
        resolved.append(
            LevelCluster(
                level_price=float(max(prices)),
                pivot_count=len(cluster),
                first_pivot_timestamp_ms=min(timestamps),
                last_pivot_timestamp_ms=max(timestamps),
                min_price=float(min(prices)),
                max_price=float(max(prices)),
            )
        )
    return resolved


def _touch_reactions(
    frame: pd.DataFrame,
    *,
    level_price: float,
    tolerance_pct: float,
    min_bounce_pct: float,
    bounce_lookahead_bars: int,
    min_retouch_distance_pct: float,
) -> list[TouchReaction]:
    touches: list[TouchReaction] = []
    band_low = level_price * (1.0 - tolerance_pct)
    band_high = level_price * (1.0 + tolerance_pct)
    rearm_price = level_price * (1.0 - max(min_retouch_distance_pct, tolerance_pct))
    armed = True

    for idx, row in frame.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if not armed:
            if low <= rearm_price or close <= rearm_price:
                armed = True
            else:
                continue

        if high < band_low or low > band_high:
            continue

        future = frame.iloc[idx + 1: idx + 1 + bounce_lookahead_bars]
        if future.empty:
            continue
        forward_low = float(future["low"].min())
        touch_high = max(high, level_price)
        reaction_pct = max((touch_high - forward_low) / touch_high, 0.0) if touch_high > 0.0 else 0.0
        touches.append(
            TouchReaction(
                timestamp_ms=int(row["timestamp"]),
                timestamp_utc=_timestamp_to_utc(int(row["timestamp"])),
                touch_high=touch_high,
                close=close,
                reaction_pct=reaction_pct,
                valid=reaction_pct >= min_bounce_pct,
            )
        )
        armed = False
    return touches


def _level_pierce_stats(
    frame: pd.DataFrame,
    *,
    level_price: float,
    after_timestamp_ms: int,
    accepted_close_tolerance_pct: float,
    min_pierce_pct: float,
) -> tuple[int, float]:
    after = frame.loc[frame["timestamp"].ge(after_timestamp_ms)].copy()
    if after.empty or level_price <= 0.0:
        return 0, 0.0

    highs = after["high"].astype(float)
    closes = after["close"].astype(float)
    pierce_pct = (highs / level_price) - 1.0
    rejected_pierce = (
        pierce_pct.gt(max(min_pierce_pct, 0.0))
        & closes.le(level_price * (1.0 + accepted_close_tolerance_pct))
    )
    if not bool(rejected_pierce.any()):
        return 0, 0.0
    return int(rejected_pierce.sum()), float(pierce_pct.loc[rejected_pierce].max())


def _select_major_levels(
    metrics: list[HourlyLevelMetric],
    *,
    max_levels: int,
    min_level_separation_pct: float,
) -> list[HourlyLevelMetric]:
    selected: list[HourlyLevelMetric] = []
    for metric in metrics:
        if all(abs(metric.level_price - existing.level_price) / existing.level_price >= min_level_separation_pct for existing in selected):
            selected.append(metric)
        if len(selected) >= max_levels:
            break
    return selected


def _has_held_break_above(
    frame: pd.DataFrame,
    *,
    level_price: float,
    after_timestamp_ms: int,
    close_tolerance_pct: float,
    hold_bars: int,
) -> bool:
    if hold_bars <= 0:
        return False
    after = frame.loc[frame["timestamp"].gt(after_timestamp_ms)].copy()
    if after.empty:
        return False
    closes_above = after["close"].astype(float).gt(level_price * (1.0 + close_tolerance_pct))
    run = 0
    for is_above in closes_above.tolist():
        run = run + 1 if bool(is_above) else 0
        if run >= hold_bars:
            return True
    return False


def _recent_move_pct(frame: pd.DataFrame, lookback_bars: int) -> float:
    recent = frame.tail(max(2, lookback_bars))
    latest_close = float(recent["close"].iloc[-1])
    recent_low = float(recent["low"].min())
    if recent_low <= 0.0:
        return 0.0
    return max((latest_close - recent_low) / recent_low, 0.0)


def find_hourly_overhead_levels(
    frame_1h: pd.DataFrame,
    *,
    symbol: str,
    config: HourlyLevelScanConfig,
    chart_path: str = "",
) -> tuple[list[HourlyLevelMetric], str, str]:
    if frame_1h.empty:
        return [], "empty", "empty_1h_frame"
    frame = _prepare_ohlcv(frame_1h)
    if len(frame) < max(72, config.min_touches * 10):
        return [], "too_short", "not_enough_1h_bars"

    trend = _trend_state(frame)
    if config.reject_downtrend_symbols and trend == "downtrend":
        return [], trend, "symbol_downtrend_rejected"

    latest_close = float(frame["close"].iloc[-1])
    latest_ts = int(frame["timestamp"].iloc[-1])
    recent_move = _recent_move_pct(frame, config.recent_move_lookback_bars)
    pivots = _pivot_high_prices(frame, config.pivot_side_bars)
    level_clusters = _cluster_prices(pivots, config.touch_tolerance_pct)
    metrics: list[HourlyLevelMetric] = []

    for cluster in level_clusters:
        level_price = cluster.level_price
        distance_pct = (level_price - latest_close) / latest_close if latest_close > 0.0 else float("nan")
        if not math.isfinite(distance_pct):
            continue
        if distance_pct <= 0.0 or distance_pct > config.max_overhead_distance_pct:
            continue

        touches = _touch_reactions(
            frame,
            level_price=level_price,
            tolerance_pct=config.touch_tolerance_pct,
            min_bounce_pct=config.min_bounce_pct,
            bounce_lookahead_bars=config.bounce_lookahead_bars,
            min_retouch_distance_pct=max(config.min_bounce_pct, config.touch_tolerance_pct * 3.0),
        )
        valid_touches = [touch for touch in touches if touch.valid]
        if len(valid_touches) < config.min_touches:
            continue
        last_valid_touch_ts = max(touch.timestamp_ms for touch in valid_touches)
        if _has_held_break_above(
            frame,
            level_price=level_price,
            after_timestamp_ms=last_valid_touch_ts,
            close_tolerance_pct=config.break_close_tolerance_pct,
            hold_bars=config.break_hold_bars,
        ):
            continue

        first_valid_touch_ts = min(touch.timestamp_ms for touch in valid_touches)
        if config.reject_downtrend_levels:
            segment = frame.loc[(frame["timestamp"] >= first_valid_touch_ts) & (frame["timestamp"] <= last_valid_touch_ts)]
            if len(segment) >= 72 and _trend_state(segment) == "downtrend":
                continue

        pierce_count, max_pierce_pct = _level_pierce_stats(
            frame,
            level_price=level_price,
            after_timestamp_ms=first_valid_touch_ts,
            accepted_close_tolerance_pct=config.break_close_tolerance_pct,
            min_pierce_pct=config.max_level_pierce_pct,
        )
        if config.reject_pierced_levels and pierce_count > 0:
            continue

        reactions = [touch.reaction_pct for touch in valid_touches]
        max_reaction = max(reactions)
        median_reaction = float(np.median(reactions))
        ratio = max_reaction / recent_move if recent_move > 0.0 else float("inf")
        if distance_pct < config.min_target_room_pct:
            context = "danger_ceiling"
        elif recent_move >= config.min_recent_move_pct and ratio >= config.reaction_to_move_threshold:
            context = "bullish_target"
        else:
            context = "overhead_level"
        strength = (
            len(valid_touches) * 1.0
            + min(max_reaction / max(config.min_bounce_pct, 1e-9), 4.0)
            + min(distance_pct / max(config.min_target_room_pct, 1e-9), 3.0)
        )
        first_touch = min(valid_touches, key=lambda touch: touch.timestamp_ms)
        last_touch = max(valid_touches, key=lambda touch: touch.timestamp_ms)
        metrics.append(
            HourlyLevelMetric(
                symbol=symbol,
                level_price=float(level_price),
                latest_close=latest_close,
                latest_timestamp_ms=latest_ts,
                latest_timestamp_utc=_timestamp_to_utc(latest_ts),
                distance_pct=distance_pct,
                raw_touch_count=len(touches),
                valid_touch_count=len(valid_touches),
                first_touch_timestamp_ms=first_touch.timestamp_ms,
                first_touch_timestamp_utc=first_touch.timestamp_utc,
                last_touch_timestamp_ms=last_touch.timestamp_ms,
                last_touch_timestamp_utc=last_touch.timestamp_utc,
                max_reaction_pct=max_reaction,
                median_valid_reaction_pct=median_reaction,
                recent_move_pct=recent_move,
                reaction_to_recent_move_ratio=ratio,
                pierce_count=pierce_count,
                max_pierce_pct=max_pierce_pct,
                strength_score=float(strength),
                context=context,
                trend_state=trend,
                chart_path=chart_path,
            )
        )
    metrics.sort(key=lambda item: (item.distance_pct, -item.strength_score))
    metrics = _select_major_levels(
        metrics,
        max_levels=config.max_levels_per_symbol,
        min_level_separation_pct=max(config.touch_tolerance_pct * 2.5, 0.012),
    )
    return metrics, trend, "ok" if metrics else "no_valid_overhead_levels"


def _level_color(context: str) -> str:
    if context == "bullish_target":
        return CHART_EXIT
    if context == "danger_ceiling":
        return CHART_LEVEL
    return CHART_ENTRY


def _level_label(context: str) -> str:
    if context == "bullish_target":
        return "target"
    if context == "danger_ceiling":
        return "ceiling"
    return "level"


def _draw_volume_panel(volume_ax: object, chart_frame: pd.DataFrame, x_values: np.ndarray) -> None:
    volume_column = "quote_volume" if "quote_volume" in chart_frame.columns else "volume"
    if volume_column not in chart_frame.columns:
        volume_ax.set_visible(False)
        return
    volumes = pd.to_numeric(chart_frame[volume_column], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    colors = np.where(
        chart_frame["close"].astype(float).to_numpy() >= chart_frame["open"].astype(float).to_numpy(),
        CHART_UP,
        CHART_DOWN,
    )
    volume_ax.bar(x_values, volumes, width=0.72, color=colors.tolist(), alpha=0.42)
    volume_ax.set_ylabel("Vol", fontsize=8, color=CHART_MUTED)
    if volumes.size and float(np.nanmax(volumes)) > 0.0:
        volume_ax.set_ylim(0.0, float(np.nanmax(volumes)) * 1.25)


def save_hourly_level_chart(symbol: str, frame_1h: pd.DataFrame, levels: list[HourlyLevelMetric], output_path: Path, chart_bars: int) -> None:
    import matplotlib.pyplot as plt

    chart_frame = frame_1h.tail(chart_bars).copy().reset_index(drop=True)
    if chart_frame.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)

    x_values = np.arange(len(chart_frame), dtype=float)
    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(15, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [4.2, 1.0], "hspace": 0.03},
        facecolor=CHART_FIGURE_FACE,
    )
    configure_plot_axes(price_ax=price_ax, volume_ax=volume_ax)
    draw_candles(price_ax, chart_frame, x_values, alpha=0.98)
    _draw_volume_panel(volume_ax, chart_frame, x_values)

    latest_close = float(chart_frame["close"].iloc[-1])
    y_values = [latest_close, *chart_frame["low"].astype(float).tolist(), *chart_frame["high"].astype(float).tolist()]
    y_values.extend(level.level_price for level in levels)
    finite_y = [float(value) for value in y_values if math.isfinite(float(value))]
    if finite_y:
        y_min = min(finite_y)
        y_max = max(finite_y)
        padding = max((y_max - y_min) * 0.08, max(abs(latest_close), 1e-12) * 0.002)
        price_ax.set_ylim(y_min - padding, y_max + padding)
    price_ax.axhline(latest_close, linestyle="--", linewidth=0.8, alpha=0.45, color=CHART_MUTED, zorder=2.7)

    timestamps = chart_frame["timestamp"].astype("int64").to_numpy()
    tag_items: list[tuple[str, float | None]] = [("last", latest_close)]
    for level_index, level in enumerate(levels, start=1):
        tag_items.append((f"{_level_label(level.context)}{level_index}", level.level_price))
    tag_positions = resolve_axis_tag_positions(tag_items)

    for level_index, level in enumerate(levels, start=1):
        level_color = _level_color(level.context)
        start_idx = resolve_timestamp_plot_idx(timestamps, level.first_touch_timestamp_ms)
        end_idx = max(len(chart_frame) - 1, start_idx)
        price_ax.hlines(
            level.level_price,
            start_idx,
            end_idx,
            linewidth=1.15,
            alpha=0.86,
            color=level_color,
            zorder=3.2,
        )
        price_ax.scatter(
            [start_idx],
            [level.level_price],
            s=14,
            color=level_color,
            zorder=4.5,
            alpha=0.95,
        )
        tag_key = f"{_level_label(level.context)}{level_index}"
        annotate_axis_price_tag(
            price_ax,
            y=level.level_price,
            label=_level_label(level.context),
            color=level_color,
            leader_start_x=end_idx,
            text_y=tag_positions.get(tag_key),
        )
        label = (
            f"{_level_label(level.context)} {format_price_label(level.level_price)}  "
            f"t={level.valid_touch_count}  dist={level.distance_pct:.1%}  "
            f"bounce={level.max_reaction_pct:.1%}"
        )
        if level.pierce_count > 0:
            label += f"  pierce={level.max_pierce_pct:.1%}"
        price_ax.text(
            end_idx,
            level.level_price,
            label,
            va="bottom",
            ha="right",
            fontsize=7.4,
            color=level_color,
            alpha=0.92,
            zorder=5.0,
        )

    annotate_axis_price_tag(
        price_ax,
        y=latest_close,
        label="last",
        color=CHART_MUTED,
        text_y=tag_positions.get("last"),
    )

    tick_timestamps = build_tick_timestamps(chart_frame)
    tick_positions = build_tick_positions_from_timestamps(tick_timestamps, len(chart_frame))
    tick_labels = build_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    volume_ax.set_xticks(tick_positions.tolist())
    volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=CHART_MUTED)

    chart_symbol = format_chart_symbol(symbol)
    price_ax.set_title(f"{chart_symbol} 1h overhead levels", fontsize=15, color=CHART_TEXT, pad=12, weight="bold")
    price_ax.set_ylabel("Price", fontsize=8, color=CHART_MUTED)
    price_ax.set_xlim(-1, len(chart_frame) + 2)
    fig.tight_layout()
    fig.savefig(output_path, **CHART_SAVEFIG_KWARGS)
    plt.close(fig)


def _write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_hourly_level_scan(
    config: HourlyLevelScanConfig,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, object]:
    output_dir = config.output_dir
    charts_dir = output_dir / "charts"
    symbols = list(config.symbols) if config.symbols else _discover_cached_symbols(config.cache_dir, config.source_timeframe)
    metrics_rows: list[dict[str, object]] = []
    status_rows: list[dict[str, object]] = []
    symbols_with_levels: set[str] = set()
    charts_saved = 0
    started_at = time.monotonic()
    last_emit_at = started_at

    def _emit(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    _emit(
        "1h level scan started: "
        f"symbols={len(symbols)}, source_timeframe={config.source_timeframe.value}, "
        f"days={config.days}, min_touches={config.min_touches}, "
        f"min_bounce_pct={config.min_bounce_pct:.2%}, output_dir={output_dir}"
    )

    for processed_count, symbol in enumerate(symbols, start=1):
        last_status = "unknown"
        last_reason = "unknown"
        try:
            source_frame, source_path = _read_cached_frame(config.cache_dir, symbol, config.source_timeframe)
            if source_frame.empty:
                last_status = "skipped"
                last_reason = "source_cache_missing_or_empty"
                status_rows.append(asdict(SymbolScanStatus(symbol, last_status, last_reason, source_path=str(source_path))))
            else:
                frame_1h = _aggregate_to_1h(source_frame, config.source_timeframe)
                frame_1h = _trim_to_recent_window(frame_1h, config.days, config.lookback_bars)
                if frame_1h.empty:
                    last_status = "skipped"
                    last_reason = "empty_after_1h_aggregation"
                    status_rows.append(
                        asdict(
                            SymbolScanStatus(
                                symbol=symbol,
                                status=last_status,
                                reason=last_reason,
                                rows_source=len(source_frame),
                                rows_1h=0,
                                source_path=str(source_path),
                            )
                        )
                    )
                else:
                    safe_name = _safe_chart_file_stem(symbol)
                    chart_path = charts_dir / f"{safe_name}_1h_levels.png"
                    levels, trend, reason = find_hourly_overhead_levels(
                        frame_1h,
                        symbol=symbol,
                        config=config,
                        chart_path=str(chart_path),
                    )
                    if levels or config.save_empty_charts:
                        save_hourly_level_chart(symbol, frame_1h, levels, chart_path, config.chart_bars)
                        charts_saved += 1
                    if levels:
                        symbols_with_levels.add(symbol)
                    metrics_rows.extend(asdict(level) for level in levels)
                    last_status = "ok" if levels else "skipped"
                    last_reason = reason
                    status_rows.append(
                        asdict(
                            SymbolScanStatus(
                                symbol=symbol,
                                status=last_status,
                                reason=last_reason,
                                rows_source=len(source_frame),
                                rows_1h=len(frame_1h),
                                levels_found=len(levels),
                                trend_state=trend,
                                source_path=str(source_path),
                            )
                        )
                    )
        except Exception as exc:
            last_status = "failed"
            last_reason = f"scan_failed:{type(exc).__name__}"
            status_rows.append(
                asdict(
                    SymbolScanStatus(
                        symbol=symbol,
                        status=last_status,
                        reason=last_reason,
                    )
                )
            )

        if _should_emit_progress(
            done=processed_count,
            total=len(symbols),
            last_emit_at=last_emit_at,
            config=config,
        ):
            _emit(
                _progress_line(
                    done=processed_count,
                    total=len(symbols),
                    started_at=started_at,
                    levels_found=len(metrics_rows),
                    symbols_with_levels=len(symbols_with_levels),
                    charts_saved=charts_saved,
                    last_symbol=symbol,
                    last_status=last_status,
                    last_reason=last_reason,
                )
            )
            last_emit_at = time.monotonic()

    summary_path = output_dir / "hourly_levels_summary.csv"
    status_path = output_dir / "hourly_levels_status.csv"
    metrics_fieldnames = list(HourlyLevelMetric.__dataclass_fields__.keys())
    status_fieldnames = list(SymbolScanStatus.__dataclass_fields__.keys())
    _write_csv(summary_path, metrics_rows, metrics_fieldnames)
    _write_csv(status_path, status_rows, status_fieldnames)
    return {
        "symbols_scanned": len(symbols),
        "levels_found": len(metrics_rows),
        "symbols_with_levels": len(symbols_with_levels),
        "charts_saved": charts_saved,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "output_dir": str(output_dir),
        "summary_path": str(summary_path),
        "status_path": str(status_path),
        "charts_dir": str(charts_dir),
    }


def build_config_from_namespace(args: object, *, cache_dir: Path, results_dir: Path) -> HourlyLevelScanConfig:
    output_dir_raw = getattr(args, "output_dir", None)
    if output_dir_raw:
        output_dir = Path(str(output_dir_raw))
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = results_dir / "hourly_levels" / stamp
    return HourlyLevelScanConfig(
        cache_dir=cache_dir,
        output_dir=output_dir,
        source_timeframe=_resolve_timeframe(str(getattr(args, "source_timeframe", "5m"))),
        symbols=tuple(str(symbol) for symbol in (getattr(args, "symbols", None) or ())),
        days=int(getattr(args, "days", 45)),
        lookback_bars=int(getattr(args, "lookback_bars", 720)),
        chart_bars=int(getattr(args, "chart_bars", 240)),
        min_touches=int(getattr(args, "min_touches", 3)),
        touch_tolerance_pct=float(getattr(args, "touch_tolerance_pct", 0.006)),
        min_bounce_pct=float(getattr(args, "min_bounce_pct", 0.05)),
        bounce_lookahead_bars=int(getattr(args, "bounce_lookahead_bars", 12)),
        min_target_room_pct=float(getattr(args, "min_target_room_pct", 0.05)),
        max_overhead_distance_pct=float(getattr(args, "max_overhead_distance_pct", 0.60)),
        min_recent_move_pct=float(getattr(args, "min_recent_move_pct", 0.05)),
        recent_move_lookback_bars=int(getattr(args, "recent_move_lookback_bars", 24)),
        reaction_to_move_threshold=float(getattr(args, "reaction_to_move_threshold", 0.80)),
        pivot_side_bars=int(getattr(args, "pivot_side_bars", 3)),
        break_close_tolerance_pct=float(getattr(args, "break_close_tolerance_pct", 0.004)),
        break_hold_bars=int(getattr(args, "break_hold_bars", 2)),
        reject_downtrend_symbols=bool(getattr(args, "reject_downtrend_symbols", True)),
        reject_downtrend_levels=bool(getattr(args, "reject_downtrend_levels", True)),
        max_levels_per_symbol=int(getattr(args, "max_levels_per_symbol", 4)),
        reject_pierced_levels=bool(getattr(args, "reject_pierced_levels", True)),
        max_level_pierce_pct=float(getattr(args, "max_level_pierce_pct", 0.015)),
        save_empty_charts=bool(getattr(args, "save_empty_charts", False)),
        progress_every_symbols=int(getattr(args, "progress_every_symbols", 5)),
        progress_min_seconds=float(getattr(args, "progress_min_seconds", 5.0)),
    )
