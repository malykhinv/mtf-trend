import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol, Type, runtime_checkable

import matplotlib
import numpy as np
from matplotlib.figure import Figure

from crypto_screener.config import app_cfg, ppo_plot_theme
from crypto_screener.config.plot_theme import PlotTheme
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Setup, Trade, Unfilled
from crypto_screener.domain.models.setup_data import Ppo, SetupData
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_levels import TradeLevels
from crypto_screener.domain.swing_detector import add_swings

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from matplotlib.axes import Axes
PlotHandler = Callable[
    [
        str,
        "PlotData",
        Optional[TradeLevels],
        Optional[list[Bar]],
        Optional[datetime],
        Optional[Context],
        Optional[str],
        bool,
        PlotTheme,
    ],
    Path,
]


@runtime_checkable
class PlotData(Protocol):
    symbol: str
    timeframe: Timeframe
    bars: list[Bar]


@dataclass(frozen=True)
class PlotStrategy:
    data_type: Type[PlotData]
    handler: PlotHandler
    theme: PlotTheme


# region Private.
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
        case Trade(data=data, trade_levels=trade_levels):
            setup_name = setup.name
        case Capture(data=data) | Unfilled(data=data):
            setup_name = setup.name
            trade_levels = None
        case _:
            raise TypeError(f"Неизвестный тип сетапа: {type(setup).__name__}")
    strategy = _find_strategy(setup.data)
    if not strategy:
        return

    return strategy.handler(
        setup_name,
        setup.data,
        trade_levels,
        postmortem_bars,
        detection_time,
        context,
        subdir,
        draw_entry_zones,
        strategy.theme,
    )

def _plot_ppo(
        setup_name: str,
        data: Ppo,
        trade_levels: Optional[TradeLevels],
        postmortem_bars: Optional[list[Bar]],
        detection_time: Optional[datetime],
        context: Optional[Context],
        subdir: Optional[str],
        draw_entry_zones: bool,
        theme: PlotTheme,
):
    if not data.symbol or not data.bars:
        raise ValueError("Недостаточно данных для построения графика.")

    trimmed_postmortem = _trim_postmortem_bars(postmortem_bars, trade_levels, theme)

    combined_bars = [*data.bars, *(trimmed_postmortem or [])]

    if not combined_bars:
        raise ValueError("Недостаточно данных для построения графика.")

    fig, price_ax, volume_ax = _build_figure(theme)

    times = [_datetime_to_mpl(bar.time) for bar in combined_bars]
    candle_width = _get_candle_width(times, theme)

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

    y_offset = max((max_price - min_price) * theme.y_offset_ratio, theme.y_offset_min)

    if data.main_low_swing and data.main_high_swing:
        start_time = _datetime_to_mpl(data.main_low_swing.time)
        end_time = _datetime_to_mpl(data.main_high_swing.time)
        price_ax.axvspan(
            xmin=min(start_time, end_time),
            xmax=max(start_time, end_time),
            ymin=0,
            ymax=1,
            color=theme.growth_phase_color,
            alpha=theme.growth_phase_alpha,
            zorder=0
        )
        price_ax.axhline(
            y=data.main_low_swing.extremum_price,
            xmin=0,
            xmax=1,
            color=theme.growth_phase_color,
            alpha=theme.growth_phase_alpha * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )
        price_ax.axhline(
            y=data.main_high_swing.extremum_price,
            xmin=0,
            xmax=1,
            color=theme.growth_phase_color,
            alpha=theme.growth_phase_alpha * 1.5,
            linestyle='--',
            linewidth=0.8,
            zorder=0
        )

    _draw_candles(price_ax, combined_bars, times, candle_width, theme)

    if draw_entry_zones and entry_price is not None and trade_levels:
        entry_time = _get_entry_time(trimmed_postmortem, detection_time, data.bars)
        horizon_time = _get_horizon_time(trimmed_postmortem, combined_bars)
        _draw_entry_zones(
            ax=price_ax,
            trade_levels=trade_levels,
            entry_time=entry_time,
            horizon_time=horizon_time,
            postmortem_bars=trimmed_postmortem or [],
            theme=theme,
        )
    _format_ax(price_ax, times, min_price, max_price, theme)

    volumes = _draw_volume(volume_ax, combined_bars, times, candle_width, theme)
    _format_volume_ax(volume_ax, volumes, theme)
    _format_time_axis(volume_ax, theme)

    if data.cascade_swings:
        _draw_cascade_level(price_ax, data.cascade_swings, combined_bars, theme)
        _draw_swing_group(
            ax=price_ax,
            swings=data.cascade_swings,
            color=theme.cascade_swing_color,
            y_offset=y_offset,
            theme=theme,
        )
    if data.resistance_swings:
        _draw_swing_group(
            ax=price_ax,
            swings=data.resistance_swings,
            color=theme.resistance_swing_color,
            y_offset=y_offset,
            theme=theme,
        )
    if data.support_swings:
        _draw_swing_group(
            ax=price_ax,
            swings=data.support_swings,
            color=theme.support_swing_color,
            y_offset=y_offset,
            theme=theme,
        )
    if data.main_high_swing:
        _draw_swing_group(
            ax=price_ax,
            swings=[data.main_high_swing],
            color=theme.main_high_swing_color,
            y_offset=y_offset,
            theme=theme,
        )
    if data.main_low_swing:
        _draw_swing_group(
            ax=price_ax,
            swings=[data.main_low_swing],
            color=theme.main_high_swing_color,
            y_offset=y_offset,
            theme=theme,
        )
    if not data.cascade_swings and not data.resistance_swings and not data.support_swings:
        base_bars = add_swings(data.bars, data.timeframe)
        swings = [bar.swing for bar in base_bars if bar.swing]
        _draw_swing_group(
            ax=price_ax,
            swings=swings,
            color=theme.common_swing_color,
            y_offset=y_offset,
            theme=theme,
        )

    if detection_time:
        detection_mpl = _datetime_to_mpl(detection_time)
        for ax in (price_ax, volume_ax):
            ax.axvline(
                detection_mpl,
                color=theme.grid_color,
                linestyle="--",
                linewidth=1.2,
                alpha=0.8,
                zorder=0,
            )

    price_ax.set_title(
        label=f"{data.symbol.upper()} • {data.timeframe.tf}",
        color=theme.title_color,
        pad=theme.title_pad,
    )

    fig.tight_layout()

    length = len(combined_bars)
    output_path = _resolve_output_path(
        name=data.symbol,
        setup_name=setup_name,
        context=context,
        timeframe=data.timeframe,
        time=combined_bars[-1].time,
        length=length,
        theme=theme,
        subdir=subdir,
    )
    fig.savefig(
        fname=output_path,
        facecolor=theme.background_color,
        dpi=theme.dpi,
        bbox_inches="tight"
    )
    plt.close(fig)

    return output_path


PLOT_STRATEGIES: list[PlotStrategy] = [
    PlotStrategy(data_type=Ppo, handler=_plot_ppo, theme=ppo_plot_theme),
]


def register_plot_strategy(strategy: PlotStrategy) -> None:
    PLOT_STRATEGIES.append(strategy)


def _find_strategy(data: SetupData) -> Optional[PlotStrategy]:
    for strategy in PLOT_STRATEGIES:
        if isinstance(data, strategy.data_type):
            return strategy
    return None


def _style_ax(ax: Axes, theme: PlotTheme) -> None:
    ax.set_facecolor(theme.background_color)
    ax.grid(
        visible=True,
        color=theme.grid_color,
        linestyle=":",
        linewidth=theme.grid_linewidth,
        alpha=theme.grid_alpha,
    )
    for spine in ax.spines.values():
        spine.set_color(theme.grid_color)
    ax.tick_params(colors=theme.tick_color, labelsize=theme.tick_labelsize)


def _build_figure(theme: PlotTheme) -> tuple[Figure, Axes, Axes]:
    fig, (price_ax, volume_ax) = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(theme.width_inches, theme.height_inches),
        dpi=theme.dpi,
        gridspec_kw={
            "height_ratios": theme.height_ratios,
            "hspace": theme.subplot_hspace,
        },
        sharex=True,
    )
    fig.patch.set_facecolor(theme.background_color)
    _style_ax(price_ax, theme)
    _style_ax(volume_ax, theme)
    return fig, price_ax, volume_ax


def _draw_candles(
        ax: Axes,
        bars: list[Bar],
        times: list[float],
        width: float,
        theme: PlotTheme,
):
    for bar, time_value in zip(bars, times):
        color = theme.color_up if bar.close >= bar.open else theme.color_down
        ax.plot(
            [time_value, time_value],
            [bar.low, bar.high],
            color=color,
            linewidth=theme.candle_wick_linewidth,
            zorder=theme.candle_wick_zorder,
        )
        body_height = max(abs(bar.close - bar.open), theme.candle_body_min_height)
        ax.add_patch(
            Rectangle(
                xy=(time_value - width * theme.candle_body_x_offset_ratio, min(bar.open, bar.close)),
                width=width,
                height=body_height,
                facecolor=color,
                edgecolor=color,
                zorder=theme.candle_body_zorder,
            )
        )


def _format_ax(
        ax: Axes,
        times: list[float],
        min_price: float,
        max_price: float,
        theme: PlotTheme,
):
    pad = max((max_price - min_price) * theme.price_pad_ratio, theme.price_pad_min)
    ax.set_ylim(min_price - pad, max_price + pad)
    width = _get_candle_width(times, theme)
    ax.set_xlim(times[0] - width, times[-1] + width)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _format_price(value, theme)))
    ax.tick_params(labelbottom=False)


def _format_time_axis(ax: Axes, theme: PlotTheme):
    locator = mdates.AutoDateLocator(
        minticks=theme.x_axis_minticks,
        maxticks=theme.x_axis_maxticks,
    )
    formatter = mdates.DateFormatter(
        fmt=theme.x_axis_time_format,
        tz=app_cfg.TIMEZONE
    )
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    for label in ax.get_xticklabels():
        label.set_rotation(theme.x_axis_label_rotation)


def _draw_swing_group(
        ax: Axes,
        swings: list[Swing],
        *,
        color: str,
        y_offset: float,
        theme: PlotTheme,
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
            s=theme.swing_marker_size,
            color=color,
            alpha=theme.swing_marker_closed_alpha if not swing.is_open else theme.swing_marker_open_alpha,
            zorder=theme.swing_zorder,
        )


def _draw_cascade_level(
        ax: Axes,
        cascade_swings: list[Swing],
        bars: list[Bar],
        theme: PlotTheme,
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
        color=theme.cascade_level_color,
        linewidth=theme.cascade_level_linewidth,
        linestyles=theme.cascade_level_linestyle,
        alpha=theme.cascade_level_alpha,
        zorder=theme.cascade_level_zorder,
    )


def _get_candle_width(times: list[float], theme: PlotTheme) -> float:
    if len(times) < theme.candle_fallback_min_times:
        minutes_ratio = theme.candle_fallback_interval_minutes / theme.minutes_in_day
        return minutes_ratio * theme.candle_width_multiplier
    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    avg_interval = sum(intervals) / len(intervals)
    return avg_interval * theme.candle_width_multiplier


def _format_price(value: float, theme: PlotTheme) -> str:
    abs_value = abs(value)
    if abs_value >= theme.price_high_threshold:
        return f"{value:,.{theme.price_decimals_high}f}"
    if abs_value >= theme.price_mid_threshold:
        return f"{value:,.{theme.price_decimals_mid}f}"
    return f"{value:,.{theme.price_decimals_low}f}"


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
        width: float,
        theme: PlotTheme,
) -> list[float]:
    volumes = [bar.volume for bar in bars]
    colors = [theme.volume_color for _ in bars]
    ax.bar(
        times,
        volumes,
        width=width,
        color=colors,
        alpha=theme.volume_alpha,
        align="center",
        zorder=theme.volume_zorder,
    )
    return volumes


def _format_volume_ax(
        ax: Axes,
        volumes: list[float],
        theme: PlotTheme,
):
    if not volumes:
        return
    max_volume = max(volumes)
    pad = max_volume * theme.volume_pad_ratio if max_volume > 0 else theme.volume_pad_ratio
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
        theme: PlotTheme,
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

    extra_bars = theme.postmortem_extra_bars
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
        theme: PlotTheme,
):
    start_mpl = _datetime_to_mpl(entry_time)
    end_mpl = _datetime_to_mpl(end_time)
    if end_mpl <= start_mpl:
        return

    lower_price = min(entry_price, target_price)
    height = max(abs(target_price - entry_price), theme.entry_zone_min_height)

    ax.add_patch(
        Rectangle(
            xy=(start_mpl, lower_price),
            width=end_mpl - start_mpl,
            height=height,
            facecolor=color,
            edgecolor=color,
            alpha=alpha,
            zorder=theme.entry_zone_zorder,
        )
    )


def _draw_entry_zones(
        ax: Axes,
        trade_levels: TradeLevels,
        entry_time: datetime,
        horizon_time: datetime,
        postmortem_bars: list[Bar],
        theme: PlotTheme,
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
        color=theme.entry_sl_color,
        alpha=theme.entry_risk_zone_alpha,
        theme=theme,
    )

    _draw_entry_zone(
        ax=ax,
        entry_price=entry_price,
        target_price=trade_levels.take_profit_price,
        entry_time=entry_time,
        end_time=tp_end_time,
        color=theme.entry_tp_color,
        alpha=theme.entry_reward_zone_alpha,
        theme=theme,
    )

    targets: list[tuple[float, Optional[datetime], str]] = []

    if trade_levels.partial_close_price is not None:
        targets.append((trade_levels.partial_close_price, target_times["pc"], theme.entry_pc_color))
    if trade_levels.breakeven_price is not None:
        targets.append((trade_levels.breakeven_price, target_times["be"], theme.entry_be_color))

    for target_price, target_time, color in targets:
        end_time = target_time or horizon_time
        _draw_entry_zone(
            ax=ax,
            entry_price=entry_price,
            target_price=target_price,
            entry_time=entry_time,
            end_time=end_time,
            color=color,
            alpha=theme.entry_zone_alpha,
            theme=theme,
        )


def _resolve_output_path(
        name: str,
        setup_name: Optional[str],
        context: Optional[Context],
        timeframe: Timeframe,
        time: datetime,
        length: int,
        theme: PlotTheme,
        subdir: Optional[str] = None,
) -> Path:
    output_dir = theme.output_dir
    if subdir:
        output_dir += f"/{subdir}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_name = (name or theme.default_symbol).split(":", 1)[0]
    name_parts = [resolved_name]
    if setup_name:
        name_parts.append(setup_name)
    if context:
        name_parts.append(context.value)

    full_name = " ".join(part.strip() for part in name_parts if part and part.strip())
    clean_name = re.sub(r"[^A-Za-z0-9-_.]+", " ", full_name)
    filename = f"{clean_name} {timeframe.tf} {length} {time:%d.%m.%Y %H.%M.%S}.png"
    return output_dir / filename


# endregion

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
