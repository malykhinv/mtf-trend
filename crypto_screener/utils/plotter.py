import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
import numpy as np
from matplotlib.figure import Figure

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setups.ppo import Capture, Setup, Trade, Unfilled
from crypto_screener.domain.models.trade_levels import TradeLevels
from crypto_screener.domain.swing_detector import add_swings

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from matplotlib.axes import Axes
from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe


# region Private.
def _style_ax(ax: Axes) -> None:
    ax.set_facecolor(cfg.PLOT_BACKGROUND_COLOR)
    ax.grid(
        visible=True,
        color=cfg.PLOT_GRID_COLOR,
        linestyle=":",
        linewidth=cfg.PLOT_GRID_LINEWIDTH,
        alpha=cfg.PLOT_GRID_ALPHA
    )
    for spine in ax.spines.values():
        spine.set_color(cfg.PLOT_GRID_COLOR)
    ax.tick_params(colors=cfg.PLOT_TICK_COLOR, labelsize=cfg.PLOT_TICK_LABELSIZE)


def _build_figure() -> tuple[Figure, Axes, Axes]:
    fig, (price_ax, volume_ax) = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(cfg.PLOT_WIDTH_INCHES, cfg.PLOT_HEIGHT_INCHES),
        dpi=cfg.PLOT_DPI,
        gridspec_kw={
            "height_ratios": cfg.PLOT_HEIGHT_RATIOS,
            "hspace": cfg.PLOT_SUBPLOT_HSPACE
        },
        sharex=True,
    )
    fig.patch.set_facecolor(cfg.PLOT_BACKGROUND_COLOR)
    _style_ax(price_ax)
    _style_ax(volume_ax)
    return fig, price_ax, volume_ax


def _draw_candles(
        ax: Axes,
        bars: list[Bar],
        times: list[float],
        width: float
):
    for bar, time_value in zip(bars, times):
        color = cfg.PLOT_COLOR_UP if bar.close >= bar.open else cfg.PLOT_COLOR_DOWN
        ax.plot(
            [time_value, time_value],
            [bar.low, bar.high],
            color=color,
            linewidth=cfg.PLOT_CANDLE_WICK_LINEWIDTH,
            zorder=cfg.PLOT_CANDLE_WICK_ZORDER
        )
        body_height = max(abs(bar.close - bar.open), cfg.PLOT_CANDLE_BODY_MIN_HEIGHT)
        ax.add_patch(
            Rectangle(
                xy=(time_value - width * cfg.PLOT_CANDLE_BODY_X_OFFSET_RATIO, min(bar.open, bar.close)),
                width=width,
                height=body_height,
                facecolor=color,
                edgecolor=color,
                zorder=cfg.PLOT_CANDLE_BODY_ZORDER,
            )
        )


def _format_ax(
        ax: Axes,
        times: list[float],
        min_price: float,
        max_price: float
):
    pad = max((max_price - min_price) * cfg.PLOT_PRICE_PAD_RATIO, cfg.PLOT_PRICE_PAD_MIN)
    ax.set_ylim(min_price - pad, max_price + pad)
    width = _get_candle_width(times)
    ax.set_xlim(times[0] - width, times[-1] + width)
    ax.yaxis.set_major_formatter(FuncFormatter(_format_price))
    ax.tick_params(labelbottom=False)


def _format_time_axis(ax: Axes):
    locator = mdates.AutoDateLocator(
        minticks=cfg.PLOT_X_AXIS_MINTICKS,
        maxticks=cfg.PLOT_X_AXIS_MAXTICKS
    )
    formatter = mdates.DateFormatter(
        fmt=cfg.PLOT_X_AXIS_TIME_FORMAT,
        tz=cfg.TIMEZONE
    )
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    for label in ax.get_xticklabels():
        label.set_rotation(cfg.PLOT_X_AXIS_LABEL_ROTATION)


def _draw_swing_group(
        ax: Axes,
        swings: list[Swing],
        *,
        color: str,
        y_offset: float,
):
    if not swings:
        return

    for swing in swings:
        marker = "v" if swing.type == SwingType.HIGH else "^"
        y = swing.extremum_price + y_offset if swing.type == SwingType.HIGH else swing.extremum_price - y_offset
        ax.scatter(
            _datetime_to_mpl(swing.time),
            y,
            marker=marker,
            s=cfg.PLOT_SWING_MARKER_SIZE,
            color=color,
            alpha=cfg.PLOT_SWING_MARKER_CLOSED_ALPHA if not swing.is_open else cfg.PLOT_SWING_MARKER_OPEN_ALPHA,
            zorder=cfg.PLOT_SWING_ZORDER,
        )


def _draw_cascade_level(
        ax: Axes,
        cascade_swings: list[Swing],
        bars: list[Bar]
):
    if not cascade_swings or not bars:
        return

    level_price = np.mean([swing.extremum_price for swing in cascade_swings])
    start_time = cascade_swings[0].time
    start_index = next((index for index, bar in enumerate(bars) if bar.time == start_time), None)
    if start_index is None:
        return

    end_index = len(bars) - 1
    for index in range(start_index + 1, len(bars)):
        bar = bars[index]
        if bar.open > level_price or bar.close > level_price:
            end_index = index
            break

    start_time_mpl = _datetime_to_mpl(bars[start_index].time)
    end_time_mpl = _datetime_to_mpl(bars[end_index].time)

    ax.hlines(
        y=level_price,
        xmin=start_time_mpl,
        xmax=end_time_mpl,
        color=cfg.PLOT_CASCADE_LEVEL_COLOR,
        linewidth=cfg.PLOT_CASCADE_LEVEL_LINEWIDTH,
        linestyles=cfg.PLOT_CASCADE_LEVEL_LINESTYLE,
        alpha=cfg.PLOT_CASCADE_LEVEL_ALPHA,
        zorder=cfg.PLOT_CASCADE_LEVEL_ZORDER,
    )


def _get_candle_width(times: list[float]) -> float:
    if len(times) < cfg.PLOT_CANDLE_FALLBACK_MIN_TIMES:
        minutes_ratio = cfg.PLOT_CANDLE_FALLBACK_INTERVAL_MINUTES / cfg.PLOT_MINUTES_IN_DAY
        return minutes_ratio * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER
    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    avg_interval = sum(intervals) / len(intervals)
    return avg_interval * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER


def _format_price(
        value: float,
        _: object
) -> str:
    abs_value = abs(value)
    if abs_value >= cfg.PLOT_PRICE_HIGH_THRESHOLD:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_HIGH}f}"
    if abs_value >= cfg.PLOT_PRICE_MID_THRESHOLD:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_MID}f}"
    return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_LOW}f}"


def _format_volume(
        value: float,
        _: object
) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _datetime_to_mpl(time: datetime) -> float:
    return mdates.date2num(time)


def _draw_volume(
        ax: Axes,
        bars: list[Bar],
        times: list[float],
        width: float
) -> list[float]:
    volumes = [bar.volume for bar in bars]
    colors = [cfg.PLOT_VOLUME_COLOR for _ in bars]
    ax.bar(
        times,
        volumes,
        width=width,
        color=colors,
        alpha=cfg.PLOT_VOLUME_ALPHA,
        align="center",
        zorder=cfg.PLOT_VOLUME_ZORDER,
    )
    return volumes


def _format_volume_ax(
        ax: Axes,
        volumes: list[float]
):
    if not volumes:
        return
    max_volume = max(volumes)
    pad = max_volume * cfg.PLOT_VOLUME_PAD_RATIO if max_volume > 0 else cfg.PLOT_VOLUME_PAD_RATIO
    ax.set_ylim(0, max_volume + pad)
    ax.yaxis.set_major_formatter(FuncFormatter(_format_volume))


def _get_entry_time(
        postmortem_bars: Optional[list[Bar]],
        detection_time: Optional[datetime],
        bars: list[Bar]
) -> datetime:
    if detection_time:
        return detection_time
    if postmortem_bars:
        return postmortem_bars[0].time
    return bars[-1].time


def _get_horizon_time(
        postmortem_bars: Optional[list[Bar]],
        combined_bars: list[Bar]
) -> datetime:
    if postmortem_bars:
        return postmortem_bars[-1].time
    return combined_bars[-1].time


def _find_target_times(
        trade_levels: TradeLevels,
        future_bars: list[Bar]
) -> dict[str, Optional[datetime]]:
    stop_loss_price = trade_levels.stop_loss_price
    take_profit_price = trade_levels.take_profit_price
    partial_close_price = trade_levels.partial_close_price
    breakeven_price = trade_levels.breakeven_price

    target_times: dict[str, Optional[datetime]] = {"sl": None, "tp": None, "pc": None, "be": None}
    has_partial_close = False

    for bar in future_bars:
        if not has_partial_close:
            if bar.low <= stop_loss_price:
                target_times["sl"] = bar.time
                break

            if partial_close_price is not None and bar.high >= partial_close_price:
                target_times["pc"] = bar.time
                has_partial_close = True
                if bar.high >= take_profit_price:
                    target_times["tp"] = bar.time
                    break
                continue

            if bar.high >= take_profit_price:
                target_times["tp"] = bar.time
                break
        else:
            if breakeven_price is not None and bar.low <= breakeven_price:
                target_times["be"] = bar.time
                break

    return target_times


def _trim_postmortem_bars(
        postmortem_bars: Optional[list[Bar]],
        trade_levels: Optional[TradeLevels],
) -> Optional[list[Bar]]:
    if not postmortem_bars or not trade_levels:
        return postmortem_bars

    target_times = _find_target_times(trade_levels, postmortem_bars)
    deal_result_time = next(
        (target_times[target] for target in ("sl", "tp", "be", "pc") if target_times[target]),
        None,
    )

    if not deal_result_time:
        return postmortem_bars

    extra_bars = cfg.PLOT_POSTMORTEM_EXTRA_BARS
    for index, bar in enumerate(postmortem_bars):
        if bar.time >= deal_result_time:
            last_index = min(len(postmortem_bars), index + extra_bars + 1)
            return postmortem_bars[:last_index]

    return postmortem_bars


def _draw_entry_zone(
        ax: Axes,
        entry_price: float,
        target_price: float,
        entry_time: datetime,
        end_time: datetime,
        *,
        color: str,
        alpha: float,
):
    start_mpl = _datetime_to_mpl(entry_time)
    end_mpl = _datetime_to_mpl(end_time)
    if end_mpl <= start_mpl:
        return

    lower_price = min(entry_price, target_price)
    height = max(abs(target_price - entry_price), cfg.PLOT_ENTRY_ZONE_MIN_HEIGHT)

    ax.add_patch(Rectangle(
        xy=(start_mpl, lower_price),
        width=end_mpl - start_mpl,
        height=height,
        facecolor=color,
        edgecolor=color,
        alpha=alpha,
        zorder=cfg.PLOT_ENTRY_ZONE_ZORDER,
    ))


def _draw_entry_zones(
        ax: Axes,
        trade_levels: TradeLevels,
        entry_time: datetime,
        horizon_time: datetime,
        postmortem_bars: list[Bar],
):
    target_times = _find_target_times(trade_levels, postmortem_bars)
    entry_price = trade_levels.entry_price

    sl_end_time = next(
        (time for time in (target_times["sl"], target_times["tp"], target_times["pc"], target_times["be"]) if time),
        horizon_time,
    )
    tp_end_time = next(
        (time for time in (target_times["tp"], target_times["pc"], target_times["be"]) if time),
        horizon_time,
    )

    _draw_entry_zone(
        ax=ax,
        entry_price=entry_price,
        target_price=trade_levels.stop_loss_price,
        entry_time=entry_time,
        end_time=sl_end_time,
        color=cfg.PLOT_ENTRY_SL_COLOR,
        alpha=cfg.PLOT_ENTRY_RISK_ZONE_ALPHA,
    )

    _draw_entry_zone(
        ax=ax,
        entry_price=entry_price,
        target_price=trade_levels.take_profit_price,
        entry_time=entry_time,
        end_time=tp_end_time,
        color=cfg.PLOT_ENTRY_TP_COLOR,
        alpha=cfg.PLOT_ENTRY_REWARD_ZONE_ALPHA,
    )

    targets: list[tuple[float, Optional[datetime], str]] = []

    if trade_levels.partial_close_price is not None:
        targets.append((trade_levels.partial_close_price, target_times["pc"], cfg.PLOT_ENTRY_PC_COLOR))
    if trade_levels.breakeven_price is not None:
        targets.append((trade_levels.breakeven_price, target_times["be"], cfg.PLOT_ENTRY_BE_COLOR))

    for target_price, target_time, color in targets:
        end_time = target_time or horizon_time
        _draw_entry_zone(
            ax=ax,
            entry_price=entry_price,
            target_price=target_price,
            entry_time=entry_time,
            end_time=end_time,
            color=color,
            alpha=cfg.PLOT_ENTRY_ZONE_ALPHA,
        )


def _resolve_output_path(
        name: str,
        setup_name: Optional[str],
        context: Optional[Context],
        timeframe: Timeframe,
        time: datetime,
        length: int,
        subdir: Optional[str] = None
) -> Path:
    output_dir = cfg.PLOT_OUTPUT_DIR
    if subdir:
        output_dir += f"/{subdir}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name_parts = [name.split(":", 1)[0]]
    if setup_name:
        name_parts.append(setup_name)
    if context:
        name_parts.append(context.value)

    full_name = " ".join(part.strip() for part in name_parts if part and part.strip())
    clean_name = re.sub(r"[^A-Za-z0-9-_.]+", " ", full_name)
    filename = f"{clean_name} {timeframe.tf} {length} {time:%d.%m.%Y %H.%M.%S}.png"
    return output_dir / filename


# endregion

def _plot(
        setup: Setup,
        *,
        postmortem_bars: Optional[list[Bar]],
        detection_time: Optional[datetime],
        context: Optional[Context],
        subdir: Optional[str],
        draw_entry_zones: bool,
):
    match setup:
        case Trade(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_high_swing=main_high_swing,
            main_low_swing=main_low_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
            trade_levels=trade_levels,
        ):
            setup_name = setup.name
        case Capture(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_high_swing=main_high_swing,
            main_low_swing=main_low_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
        ) | Unfilled(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_high_swing=main_high_swing,
            main_low_swing=main_low_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
        ):
            setup_name = setup.name
            trade_levels = None
        case _:
            raise TypeError(f"Неизвестный тип сетапа: {type(setup).__name__}")

    if not symbol or not bars:
        raise ValueError("Недостаточно данных для построения графика.")

    trimmed_postmortem = _trim_postmortem_bars(postmortem_bars, trade_levels)

    combined_bars = [*bars, *(trimmed_postmortem or [])]

    if not combined_bars:
        raise ValueError("Недостаточно данных для построения графика.")

    fig, price_ax, volume_ax = _build_figure()

    times = [_datetime_to_mpl(bar.time) for bar in combined_bars]
    candle_width = _get_candle_width(times)

    min_price = min(bar.low for bar in combined_bars)
    max_price = max(bar.high for bar in combined_bars)

    entry_price = trade_levels.entry_price if trade_levels else None
    if trade_levels and entry_price is not None:
        trade_prices = [trade_levels.take_profit_price, trade_levels.stop_loss_price, entry_price]
        if trade_levels.partial_close_price is not None:
            trade_prices.append(trade_levels.partial_close_price)
        if trade_levels.breakeven_price is not None:
            trade_prices.append(trade_levels.breakeven_price)
        min_price = min(min_price, *trade_prices)
        max_price = max(max_price, *trade_prices)

    y_offset = max((max_price - min_price) * cfg.PLOT_Y_OFFSET_RATIO, cfg.PLOT_Y_OFFSET_MIN)

    if main_low_swing and main_high_swing:
        start_time = _datetime_to_mpl(main_low_swing.time)
        end_time = _datetime_to_mpl(main_high_swing.time)
        price_ax.axvspan(
            xmin=min(start_time, end_time),
            xmax=max(start_time, end_time),
            ymin=0,
            ymax=1,
            color=cfg.PLOT_GROWTH_PHASE_COLOR,
            alpha=cfg.PLOT_GROWTH_PHASE_ALPHA,
            zorder=0
        )
        price_ax.axhline(
            y=main_low_swing.extremum_price,
            xmin=0,
            xmax=1,
            color=cfg.PLOT_GROWTH_PHASE_COLOR,
            alpha=cfg.PLOT_GROWTH_PHASE_ALPHA * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )
        price_ax.axhline(
            y=main_high_swing.extremum_price,
            xmin=0,
            xmax=1,
            color=cfg.PLOT_GROWTH_PHASE_COLOR,
            alpha=cfg.PLOT_GROWTH_PHASE_ALPHA * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )

    _draw_candles(price_ax, combined_bars, times, candle_width)

    if draw_entry_zones and entry_price is not None and trade_levels:
        entry_time = _get_entry_time(trimmed_postmortem, detection_time, bars)
        horizon_time = _get_horizon_time(trimmed_postmortem, combined_bars)
        _draw_entry_zones(
            ax=price_ax,
            trade_levels=trade_levels,
            entry_time=entry_time,
            horizon_time=horizon_time,
            postmortem_bars=trimmed_postmortem or [],
        )
    _format_ax(price_ax, times, min_price, max_price)

    volumes = _draw_volume(volume_ax, combined_bars, times, candle_width)
    _format_volume_ax(volume_ax, volumes)
    _format_time_axis(volume_ax)

    if cascade_swings:
        _draw_cascade_level(price_ax, cascade_swings, combined_bars)
        _draw_swing_group(
            ax=price_ax,
            swings=cascade_swings,
            color=cfg.PLOT_CASCADE_SWING_COLOR,
            y_offset=y_offset
        )
    if resistance_swings:
        _draw_swing_group(
            ax=price_ax,
            swings=resistance_swings,
            color=cfg.PLOT_RESISTANCE_SWING_COLOR,
            y_offset=y_offset
        )
    if support_swings:
        _draw_swing_group(
            ax=price_ax,
            swings=support_swings,
            color=cfg.PLOT_SUPPORT_SWING_COLOR,
            y_offset=y_offset
        )
    if main_high_swing:
        _draw_swing_group(
            ax=price_ax,
            swings=[main_high_swing],
            color=cfg.PLOT_MAIN_HIGH_SWING_COLOR,
            y_offset=y_offset
        )
    if main_low_swing:
        _draw_swing_group(
            ax=price_ax,
            swings=[main_low_swing],
            color=cfg.PLOT_MAIN_HIGH_SWING_COLOR,
            y_offset=y_offset
        )
    if not cascade_swings and not resistance_swings and not support_swings:
        base_bars = add_swings(bars, timeframe)
        swings = [bar.swing for bar in base_bars if bar.swing]
        _draw_swing_group(
            ax=price_ax,
            swings=swings,
            color=cfg.PLOT_COMMON_SWING_COLOR,
            y_offset=y_offset
        )

    if detection_time:
        detection_mpl = _datetime_to_mpl(detection_time)
        for ax in (price_ax, volume_ax):
            ax.axvline(
                detection_mpl,
                color=cfg.PLOT_GRID_COLOR,
                linestyle="--",
                linewidth=1.2,
                alpha=0.8,
                zorder=0,
            )

    price_ax.set_title(
        label=f"{symbol.upper()} • {timeframe.tf}",
        color=cfg.PLOT_TITLE_COLOR,
        pad=cfg.PLOT_TITLE_PAD
    )

    fig.tight_layout()

    length = len(combined_bars)
    output_path = _resolve_output_path(
        name=symbol,
        setup_name=setup_name,
        context=context,
        timeframe=timeframe,
        time=combined_bars[-1].time,
        length=length,
        subdir=subdir,
    )
    fig.savefig(
        fname=output_path,
        facecolor=cfg.PLOT_BACKGROUND_COLOR,
        dpi=cfg.PLOT_DPI,
        bbox_inches="tight"
    )
    plt.close(fig)

    return output_path


def plot(
        setup: Setup,
        detection_time: Optional[datetime] = None,
        context: Optional[Context] = None,
        subdir: Optional[str] = None,
):
    return _plot(
        setup=setup,
        postmortem_bars=None,
        detection_time=detection_time,
        context=context,
        subdir=subdir,
        draw_entry_zones=False,
    )


def plot_postmortem(
        setup: Setup,
        postmortem_bars: Optional[list[Bar]] = None,
        detection_time: Optional[datetime] = None,
        context: Optional[Context] = None,
        subdir: Optional[str] = None,
):
    return _plot(
        setup=setup,
        postmortem_bars=postmortem_bars,
        detection_time=detection_time,
        context=context,
        subdir=subdir,
        draw_entry_zones=True,
    )
