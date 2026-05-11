"""Hourly overhead level diagnostics for pump-continuation review."""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote

import numpy as np
import pandas as pd

from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe


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
    save_empty_charts: bool = False


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
    strength_score: float
    context: str
    trend_state: str
    chart_path: str


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


def _cluster_prices(pivots: list[tuple[int, float]], tolerance_pct: float) -> list[float]:
    clusters: list[list[tuple[int, float]]] = []
    for pivot in sorted(pivots, key=lambda item: item[1]):
        _, price = pivot
        attached = False
        for cluster in clusters:
            center = float(np.median([item[1] for item in cluster]))
            if abs(price - center) / center <= tolerance_pct:
                cluster.append(pivot)
                attached = True
                break
        if not attached:
            clusters.append([pivot])
    return [float(np.median([item[1] for item in cluster])) for cluster in clusters]


def _touch_reactions(
    frame: pd.DataFrame,
    *,
    level_price: float,
    tolerance_pct: float,
    min_bounce_pct: float,
    bounce_lookahead_bars: int,
) -> list[TouchReaction]:
    touches: list[TouchReaction] = []
    band_low = level_price * (1.0 - tolerance_pct)
    band_high = level_price * (1.0 + tolerance_pct)
    for idx, row in frame.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
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
    return touches


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
    level_prices = _cluster_prices(pivots, config.touch_tolerance_pct)
    metrics: list[HourlyLevelMetric] = []

    for level_price in level_prices:
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

        if config.reject_downtrend_levels:
            first_valid = min(touch.timestamp_ms for touch in valid_touches)
            segment = frame.loc[(frame["timestamp"] >= first_valid) & (frame["timestamp"] <= last_valid_touch_ts)]
            if len(segment) >= 72 and _trend_state(segment) == "downtrend":
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
                strength_score=float(strength),
                context=context,
                trend_state=trend,
                chart_path=chart_path,
            )
        )
    metrics.sort(key=lambda item: (item.context != "bullish_target", item.distance_pct, -item.strength_score))
    return metrics, trend, "ok" if metrics else "no_valid_overhead_levels"


def _draw_candles(ax: object, frame: pd.DataFrame) -> None:
    import matplotlib.patches as patches

    width = 0.60
    for idx, row in enumerate(frame.itertuples(index=False)):
        open_price = float(getattr(row, "open"))
        high = float(getattr(row, "high"))
        low = float(getattr(row, "low"))
        close = float(getattr(row, "close"))
        edge = "#00a070" if close >= open_price else "#d84a5f"
        ax.vlines(idx, low, high, linewidth=0.8, color=edge, alpha=0.80)
        body_low = min(open_price, close)
        body_height = max(abs(close - open_price), max(high - low, 1e-12) * 0.015)
        rect = patches.Rectangle(
            (idx - width / 2, body_low),
            width,
            body_height,
            linewidth=0.7,
            edgecolor=edge,
            facecolor=edge,
            alpha=0.70,
        )
        ax.add_patch(rect)


def save_hourly_level_chart(symbol: str, frame_1h: pd.DataFrame, levels: list[HourlyLevelMetric], output_path: Path, chart_bars: int) -> None:
    import matplotlib.pyplot as plt

    chart_frame = frame_1h.tail(chart_bars).copy().reset_index(drop=True)
    if chart_frame.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(15, 7))
    _draw_candles(ax, chart_frame)
    latest_close = float(chart_frame["close"].iloc[-1])
    ax.axhline(latest_close, linestyle="--", linewidth=0.8, alpha=0.55)
    for level in levels:
        level_color = "#f0b429" if level.context == "bullish_target" else "#c084fc"
        ax.axhline(level.level_price, linewidth=1.2, alpha=0.85, color=level_color)
        label = (
            f"{level.level_price:.8g} | {level.context} | "
            f"touches={level.valid_touch_count} | dist={level.distance_pct:.1%} | "
            f"bounce={level.max_reaction_pct:.1%}"
        )
        ax.text(
            max(len(chart_frame) - 1, 0),
            level.level_price,
            label,
            va="bottom",
            ha="right",
            fontsize=8,
            color=level_color,
        )
    tick_step = max(len(chart_frame) // 8, 1)
    tick_positions = list(range(0, len(chart_frame), tick_step))
    tick_labels = [_timestamp_to_utc(int(chart_frame["timestamp"].iloc[pos]))[5:16].replace("T", " ") for pos in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=30, ha="right")
    ax.set_title(f"{symbol} 1h overhead levels")
    ax.set_xlim(-1, len(chart_frame) + 1)
    ax.grid(True, alpha=0.20)
    fig.tight_layout()
    fig.savefig(output_path, dpi=140)
    plt.close(fig)


def _write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_hourly_level_scan(config: HourlyLevelScanConfig) -> dict[str, object]:
    output_dir = config.output_dir
    charts_dir = output_dir / "charts"
    symbols = list(config.symbols) if config.symbols else _discover_cached_symbols(config.cache_dir, config.source_timeframe)
    metrics_rows: list[dict[str, object]] = []
    status_rows: list[dict[str, object]] = []

    for symbol in symbols:
        try:
            source_frame, source_path = _read_cached_frame(config.cache_dir, symbol, config.source_timeframe)
            if source_frame.empty:
                status_rows.append(asdict(SymbolScanStatus(symbol, "skipped", "source_cache_missing_or_empty", source_path=str(source_path))))
                continue
            frame_1h = _aggregate_to_1h(source_frame, config.source_timeframe)
            frame_1h = _trim_to_recent_window(frame_1h, config.days, config.lookback_bars)
            if frame_1h.empty:
                status_rows.append(
                    asdict(
                        SymbolScanStatus(
                            symbol=symbol,
                            status="skipped",
                            reason="empty_after_1h_aggregation",
                            rows_source=len(source_frame),
                            rows_1h=0,
                            source_path=str(source_path),
                        )
                    )
                )
                continue
            safe_name = symbol.replace("/", "_").replace(":", "_")
            chart_path = charts_dir / f"{safe_name}_1h_levels.png"
            levels, trend, reason = find_hourly_overhead_levels(
                frame_1h,
                symbol=symbol,
                config=config,
                chart_path=str(chart_path),
            )
            if levels or config.save_empty_charts:
                save_hourly_level_chart(symbol, frame_1h, levels, chart_path, config.chart_bars)
            metrics_rows.extend(asdict(level) for level in levels)
            status_rows.append(
                asdict(
                    SymbolScanStatus(
                        symbol=symbol,
                        status="ok" if levels else "skipped",
                        reason=reason,
                        rows_source=len(source_frame),
                        rows_1h=len(frame_1h),
                        levels_found=len(levels),
                        trend_state=trend,
                        source_path=str(source_path),
                    )
                )
            )
        except Exception as exc:
            status_rows.append(
                asdict(
                    SymbolScanStatus(
                        symbol=symbol,
                        status="failed",
                        reason=f"scan_failed:{type(exc).__name__}",
                    )
                )
            )

    summary_path = output_dir / "hourly_levels_summary.csv"
    status_path = output_dir / "hourly_levels_status.csv"
    metrics_fieldnames = list(HourlyLevelMetric.__dataclass_fields__.keys())
    status_fieldnames = list(SymbolScanStatus.__dataclass_fields__.keys())
    _write_csv(summary_path, metrics_rows, metrics_fieldnames)
    _write_csv(status_path, status_rows, status_fieldnames)
    return {
        "symbols_scanned": len(symbols),
        "levels_found": len(metrics_rows),
        "symbols_with_levels": len({str(row["symbol"]) for row in metrics_rows}),
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
        save_empty_charts=bool(getattr(args, "save_empty_charts", False)),
    )
