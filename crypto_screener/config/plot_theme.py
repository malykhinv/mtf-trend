from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlotTheme:
    """Настройки оформления графиков для стратегий."""

    # region Параметры вывода.
    output_dir: str = ".generated/plot"
    width_inches: float = 12
    height_inches: float = 12
    dpi: int = 110
    # endregion

    # region Цвета и фон.
    tick_color: str = "white"
    title_color: str = "white"
    color_up: str = "#078772"
    color_down: str = "#d42f2f"
    background_color: str = "#0f0f0f"
    grid_color: str = "#2f2f2f"
    volume_color: str = "#2f2f2f"
    main_high_swing_color: str = "#ffe082"
    cascade_swing_color: str = "#64b5f6"
    cascade_level_color: str = "#64b5f6"
    resistance_swing_color: str = "#ef5350"
    support_swing_color: str = "#66bb6a"
    common_swing_color: str = "#663366"
    growth_phase_color: str = "#808080"
    entry_sl_color: str = "#8b1a1a"
    entry_tp_color: str = "#0b3b2e"
    entry_pc_color: str = "#1c54b2"
    entry_be_color: str = "#ffb74d"
    # endregion

    # region Прозрачность и z-order.
    cascade_level_alpha: float = 0.7
    cascade_level_zorder: int = 1
    grid_linewidth: float = 0.6
    grid_alpha: float = 0.4
    growth_phase_alpha: float = 0.05
    swing_marker_closed_alpha: float = 0.25
    swing_marker_open_alpha: float = 1.0
    swing_zorder: int = 3
    candle_wick_zorder: int = 1
    candle_body_zorder: int = 2
    volume_alpha: float = 0.5
    volume_zorder: int = 1
    entry_risk_zone_alpha: float = 0.14
    entry_reward_zone_alpha: float = 0.12
    entry_zone_alpha: float = 0.12
    entry_zone_zorder: int = 0
    # endregion

    # region Размеры и отступы.
    title_pad: int = 12
    tick_labelsize: int = 9
    price_pad_ratio: float = 0.05
    price_pad_min: float = 1e-3
    y_offset_ratio: float = 0.015
    y_offset_min: float = 1e-4
    volume_pad_ratio: float = 0.05
    swing_marker_size: int = 32
    candle_width_multiplier: float = 0.6
    candle_wick_linewidth: float = 1.1
    candle_body_min_height: float = 1e-5
    candle_body_x_offset_ratio: float = 0.5
    candle_fallback_min_times: int = 2
    candle_fallback_interval_minutes: int = 1
    minutes_in_day: int = 24 * 60
    height_ratios: tuple[int, ...] = field(default_factory=lambda: (3, 1))
    subplot_hspace: float = 0.03
    entry_zone_min_height: float = 1e-5
    cascade_level_linewidth: float = 1.6
    cascade_level_linestyle: str = "--"
    x_axis_label_rotation: int = 0
    # endregion

    # region Форматирование.
    x_axis_time_format: str = "%d %b %H:%M"
    x_axis_minticks: int = 4
    x_axis_maxticks: int = 8
    price_decimals_high: int = 2
    price_decimals_mid: int = 4
    price_decimals_low: int = 6
    price_high_threshold: float = 100
    price_mid_threshold: float = 1
    default_symbol: str = "asset"
    postmortem_extra_bars: int = 5
    # endregion


ppo_plot_theme = PlotTheme()
gu_plot_theme = PlotTheme()
