import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
from matplotlib.figure import Figure

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
        y = swing.price + y_offset if swing.type == SwingType.HIGH else swing.price - y_offset
        ax.scatter(
            _datetime_to_mpl(swing.time),
            y,
            marker=marker,
            s=cfg.PLOT_SWING_MARKER_SIZE,
            color=color,
            alpha=cfg.PLOT_SWING_MARKER_CLOSED_ALPHA if not swing.is_open else cfg.PLOT_SWING_MARKER_OPEN_ALPHA,
            zorder=cfg.PLOT_SWING_ZORDER,
        )


def _get_candle_width(times: list[float]) -> float:
    if len(times) < cfg.PLOT_CANDLE_FALLBACK_MIN_TIMES:
        minutes_ratio = cfg.PLOT_CANDLE_FALLBACK_INTERVAL_MINUTES / cfg.PLOT_MINUTES_IN_DAY
        return minutes_ratio * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER
    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    avg_interval = sum(intervals) / len(intervals)
    return avg_interval * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER


def _format_price(value: float, _: object) -> str:
    abs_value = abs(value)
    if abs_value >= cfg.PLOT_PRICE_HIGH_THRESHOLD:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_HIGH}f}"
    if abs_value >= cfg.PLOT_PRICE_MID_THRESHOLD:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_MID}f}"
    return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_LOW}f}"


def _format_volume(value: float, _: object) -> str:
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


def _draw_volume(ax: Axes, bars: list[Bar], times: list[float], width: float) -> list[float]:
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


def _format_volume_ax(ax: Axes, volumes: list[float]):
    if not volumes:
        return
    max_volume = max(volumes)
    pad = max_volume * cfg.PLOT_VOLUME_PAD_RATIO if max_volume > 0 else cfg.PLOT_VOLUME_PAD_RATIO
    ax.set_ylim(0, max_volume + pad)
    ax.yaxis.set_major_formatter(FuncFormatter(_format_volume))


def _resolve_output_path(
        name: str,
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
    clean_name = re.sub(r"[^A-Za-z0-9-_.]+", " ", name.split(":", 1)[0])
    filename = f"{clean_name} {timeframe.tf} {length} {time:%d.%m.%Y %H.%M.%S}.png"
    return output_dir / filename


# endregion

def plot(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        main_high_swing: Optional[Swing] = None,
        main_low_swing: Optional[Swing] = None,
        cascade_swings: Optional[list[Swing]] = None,
        resistance_swings: Optional[list[Swing]] = None,
        support_swings: Optional[list[Swing]] = None,
        subdir: Optional[str] = None,
        setup_name: Optional[str] = None
):
    if not symbol or not bars:
        raise ValueError("Недостаточно данных для построения графика.")

    fig, price_ax, volume_ax = _build_figure()

    times = [_datetime_to_mpl(bar.time) for bar in bars]
    candle_width = _get_candle_width(times)

    min_price = min(bar.low for bar in bars)
    max_price = max(bar.high for bar in bars)
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
            y=main_low_swing.price,
            xmin=0,
            xmax=1,
            color=cfg.PLOT_GROWTH_PHASE_COLOR,
            alpha=cfg.PLOT_GROWTH_PHASE_ALPHA * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )
        price_ax.axhline(
            y=main_high_swing.price,
            xmin=0,
            xmax=1,
            color=cfg.PLOT_GROWTH_PHASE_COLOR,
            alpha=cfg.PLOT_GROWTH_PHASE_ALPHA * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )

    _draw_candles(price_ax, bars, times, candle_width)
    _format_ax(price_ax, times, min_price, max_price)

    volumes = _draw_volume(volume_ax, bars, times, candle_width)
    _format_volume_ax(volume_ax, volumes)
    _format_time_axis(volume_ax)

    if cascade_swings:
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
        bars = add_swings(bars, timeframe)
        swings = [bar.swing for bar in bars if bar.swing]
        _draw_swing_group(
            ax=price_ax,
            swings=swings,
            color=cfg.PLOT_COMMON_SWING_COLOR,
            y_offset=y_offset
        )

    price_ax.set_title(
        label=f"{symbol.upper()} • {timeframe.tf}",
        color=cfg.PLOT_TITLE_COLOR,
        pad=cfg.PLOT_TITLE_PAD
    )

    fig.tight_layout()

    length = len(bars)
    output_path = _resolve_output_path(setup_name or symbol, timeframe, bars[-1].time, length, subdir)
    fig.savefig(
        fname=output_path,
        facecolor=cfg.PLOT_BACKGROUND_COLOR,
        dpi=cfg.PLOT_DPI,
        bbox_inches="tight"
    )
    plt.close(fig)

    return output_path
