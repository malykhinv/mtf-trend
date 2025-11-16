import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import matplotlib
from matplotlib.figure import Figure

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe


# region Private.
def _build_figure() -> tuple[Figure, Any]:
    fig, ax = plt.subplots(figsize=(cfg.PLOT_WIDTH_INCHES, cfg.PLOT_HEIGHT_INCHES), dpi=cfg.PLOT_DPI)
    fig.patch.set_facecolor(cfg.PLOT_BACKGROUND_COLOR)
    ax.set_facecolor(cfg.PLOT_BACKGROUND_COLOR)
    ax.grid(True, color=cfg.PLOT_GRID_COLOR, linestyle=":", linewidth=0.6, alpha=0.4)
    for spine in ax.spines.values():
        spine.set_color(cfg.PLOT_GRID_COLOR)
    ax.tick_params(colors="white", labelsize=9)
    return fig, ax


def _draw_candles(
        ax,
        bars: list[Bar],
        times: list[float],
        width: float
):
    for bar, time_value in zip(bars, times):
        color = cfg.PLOT_COLOR_UP if bar.close >= bar.open else cfg.PLOT_COLOR_DOWN
        ax.plot([time_value, time_value], [bar.low, bar.high], color=color, linewidth=1.1, zorder=1)
        body_height = max(abs(bar.close - bar.open), 1e-5)
        ax.add_patch(
            Rectangle(
                xy=(time_value - width / 2, min(bar.open, bar.close)),
                width=width,
                height=body_height,
                facecolor=color,
                edgecolor=color,
                zorder=2,
            )
        )


def _format_ax(
        ax,
        times: list[float],
        min_price: float,
        max_price: float
):
    pad = max((max_price - min_price) * 0.05, 1e-3)
    ax.set_ylim(min_price - pad, max_price + pad)

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.DateFormatter(cfg.PLOT_X_AXIS_TIME_FORMAT, tz=cfg.TIMEZONE)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    for label in ax.get_xticklabels():
        label.set_rotation(0)

    ax.yaxis.set_major_formatter(FuncFormatter(_format_price))

    width = _get_candle_width(times)
    ax.set_xlim(times[0] - width, times[-1] + width)


def _draw_swing_group(
        ax,
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
            edgecolors="white",
            linewidths=0.6,
            alpha=0.85 if not swing.is_open else 0.55,
            zorder=3,
        )


def _get_candle_width(times: list[float]) -> float:
    if len(times) < 2:
        return (1 / (24 * 60)) * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER
    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    avg_interval = sum(intervals) / len(intervals)
    return avg_interval * cfg.PLOT_CANDLE_WIDTH_MULTIPLIER


def _format_price(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 100:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_HIGH}f}"
    if abs_value >= 1:
        return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_MID}f}"
    return f"{value:,.{cfg.PLOT_PRICE_DECIMALS_LOW}f}"


def _datetime_to_mpl(time: datetime) -> float:
    return mdates.date2num(time)


def _resolve_output_path(
        symbol: str,
        timeframe: Timeframe,
        time: datetime
) -> Path:
    base_dir = Path(cfg.PLOT_OUTPUT_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = re.sub(r"[^A-Za-z0-9_-]+", "-", symbol).strip("-") or "asset"
    filename = f"{safe_symbol}_{timeframe.tf}_{time:%Y%m%d_%H%M%S}.png"
    return base_dir / filename


# endregion

def plot(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        main_high_swing: Optional[Swing],
        cascade_swings: list[Swing],
        resistance_swings: list[Swing],
        support_swings: list[Swing]
):
    if not symbol or not bars:
        raise ValueError("Недостаточно данных для построения графика.")

    fig, ax = _build_figure()

    times = [_datetime_to_mpl(bar.time) for bar in bars]
    candle_width = _get_candle_width(times)

    min_price = min(b.low for b in bars)
    max_price = max(b.high for b in bars)
    y_offset = max((max_price - min_price) * 0.015, 1e-4)

    _draw_candles(ax, bars, times, candle_width)
    _format_ax(ax, times, min_price, max_price)

    _draw_swing_group(
        ax,
        swings=cascade_swings,
        color=cfg.PLOT_CASCADE_SWING_COLOR,
        y_offset=y_offset
    )
    _draw_swing_group(
        ax,
        swings=resistance_swings,
        color=cfg.PLOT_RESISTANCE_SWING_COLOR,
        y_offset=y_offset
    )
    _draw_swing_group(
        ax,
        swings=support_swings,
        color=cfg.PLOT_SUPPORT_SWING_COLOR,
        y_offset=y_offset
    )
    if main_high_swing:
        _draw_swing_group(
            ax,
            swings=[main_high_swing],
            color=cfg.PLOT_MAIN_HIGH_SWING_COLOR,
            y_offset=y_offset
        )

    ax.set_title(f"{symbol.upper()} • {timeframe.tf}", color="white", pad=12)

    legend = ax.legend(loc="upper left", fontsize=9)
    if legend:
        for text in legend.get_texts():
            text.set_color("white")

    fig.tight_layout()

    output_path = _resolve_output_path(symbol, timeframe, bars[-1].time)
    fig.savefig(output_path, facecolor=cfg.PLOT_BACKGROUND_COLOR, dpi=cfg.PLOT_DPI, bbox_inches="tight")
    plt.close(fig)

    return output_path
