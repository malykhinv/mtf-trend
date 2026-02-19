"""Визуализация уровней и ретестов для MTF-стратегии пробоя."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle

from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe
from domain.models.retest_plot_span import RetestPlotSpan
from domain.models.trade_plot_span import TradePlotSpan
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import BreakoutParams
from vectorbt_runner.data_preparer import DataPreparer
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class StrategyPlotter:
    """Read-only визуализатор уровней старшего ТФ и ретестов младшего ТФ."""

    TV_BG = "#0b0f14"
    TV_GRID = "#1f2937"
    TV_TEXT = "#d1d5db"
    TV_BULL = "#089981"
    TV_BEAR = "#f23645"
    TV_ENTRY = "#4ea4dc"

    def __init__(self, data_preparer: DataPreparer, strategy: BreakoutStrategy) -> None:
        self._data_preparer = data_preparer
        self._strategy = strategy

    @staticmethod
    def _resolve_symbol_output_dir(output_dir: Path | str, symbol: str) -> Path:
        encoded_symbol = ParquetStorage.encode_symbol_for_path(symbol)
        symbol_dir = Path(output_dir) / encoded_symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        return symbol_dir

    @staticmethod
    def _format_date_range(annotated: pd.DataFrame) -> str:
        if annotated.empty:
            return "empty"
        start_ms = int(annotated["timestamp"].iloc[0])
        end_ms = int(annotated["timestamp"].iloc[-1])
        return f"{start_ms}_{end_ms}"

    @staticmethod
    def _timestamp_formatter(fmt: str) -> FuncFormatter:
        return FuncFormatter(lambda value, _position: mdates.num2date(value).strftime(fmt))

    @staticmethod
    def _prepare_plot_frame(frame: pd.DataFrame) -> pd.DataFrame:
        plot_frame = frame.copy()
        plot_frame["plot_time"] = pd.to_datetime(plot_frame["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
        return plot_frame

    @staticmethod
    def _apply_dark_theme(ax: plt.Axes) -> None:
        ax.set_facecolor(StrategyPlotter.TV_BG)
        ax.grid(color=StrategyPlotter.TV_GRID, alpha=0.16, linestyle="-", linewidth=0.6)
        ax.tick_params(colors=StrategyPlotter.TV_TEXT, labelsize=9)
        for spine in ax.spines.values():
            spine.set_visible(False)

    @staticmethod
    def _add_price_zone(
        ax: plt.Axes,
        *,
        x_start: pd.Timestamp,
        x_end: pd.Timestamp,
        price: float,
        label: str,
        facecolor: str,
        edgecolor: str,
        text_color: str,
        vertical_padding: float,
    ) -> None:
        x0 = mdates.date2num(x_start)
        width = max(mdates.date2num(x_end) - x0, 1e-9)
        y0 = price - vertical_padding
        height = max(vertical_padding * 2.0, 1e-9)
        zone = FancyBboxPatch(
            (x0, y0),
            width,
            height,
            boxstyle="round,pad=0.002,rounding_size=0.008",
            linewidth=0.0,
            facecolor=facecolor,
            edgecolor="none",
            alpha=0.26,
            zorder=2.5,
        )
        ax.add_patch(zone)
        ax.text(
            x0 + width / 2.0,
            price,
            f"{label} {price:.4f}",
            color=text_color,
            fontsize=8,
            fontweight="semibold",
            va="center",
            ha="center",
            zorder=4,
            bbox={
                "boxstyle": "round,pad=0.22,rounding_size=0.08",
                "facecolor": "#111827",
                "edgecolor": "none",
                "alpha": 0.7,
            },
        )

    @staticmethod
    def _add_styled_legend(ax: plt.Axes, handles: list[Line2D]) -> None:
        legend = ax.legend(
            handles=handles,
            loc="upper left",
            frameon=True,
            fancybox=True,
            framealpha=0.85,
            edgecolor="none",
            labelcolor=StrategyPlotter.TV_TEXT,
            fontsize=9,
            ncol=2,
        )
        legend.get_frame().set_facecolor("#111827")

    @staticmethod
    def _plot_candles(ax: plt.Axes, frame: pd.DataFrame) -> None:
        if frame.empty:
            return

        plot_time = np.asarray(mdates.date2num(frame["plot_time"]), dtype=float)
        open_prices = frame["open"].to_numpy(dtype=float, copy=False)
        high_prices = frame["high"].to_numpy(dtype=float, copy=False)
        low_prices = frame["low"].to_numpy(dtype=float, copy=False)
        close_prices = frame["close"].to_numpy(dtype=float, copy=False)

        if len(plot_time) > 1:
            candle_width = float((plot_time[1] - plot_time[0]) * 0.68)
        else:
            candle_width = 0.005

        is_bull = close_prices >= open_prices
        wick_colors = np.where(is_bull, StrategyPlotter.TV_BULL, StrategyPlotter.TV_BEAR)
        body_colors = np.where(is_bull, StrategyPlotter.TV_BULL, StrategyPlotter.TV_BEAR)

        ax.vlines(plot_time, low_prices, high_prices, color=wick_colors, linewidth=1.0, zorder=2)

        body_bottom = np.minimum(open_prices, close_prices)
        body_height = np.maximum(np.abs(close_prices - open_prices), 1e-9)
        ax.bar(
            plot_time,
            body_height,
            width=candle_width,
            bottom=body_bottom,
            color=body_colors,
            edgecolor="none",
            linewidth=0.0,
            align="center",
            zorder=3,
        )

    @staticmethod
    def _format_time_axis(ax: plt.Axes) -> None:
        locator = mdates.AutoDateLocator(minticks=5, maxticks=8)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    @staticmethod
    def _build_mtf_frames(
        symbol_data: dict[Timeframe, pd.DataFrame],
        *,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> SymbolMtfFrames:
        return SymbolMtfFrames(
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            levels_frame=symbol_data.get(levels_timeframe, pd.DataFrame()),
            entry_frame=symbol_data.get(entry_timeframe, pd.DataFrame()),
        )

    def _load_annotated(self, symbol: str, params: BreakoutParams) -> pd.DataFrame:
        annotated, _ = self._load_annotated_and_daily_levels(symbol=symbol, params=params)
        return annotated

    def _load_annotated_and_daily_levels(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        symbol_data = self._data_preparer.load_symbol_data_multi(
            symbol,
            (params.levels_timeframe, params.entry_timeframe),
        )
        mtf_frames = self._build_mtf_frames(
            symbol_data,
            levels_timeframe=params.levels_timeframe,
            entry_timeframe=params.entry_timeframe,
        )
        prepared = self._strategy.prepare_multi_tf_data(
            mtf_frames=mtf_frames,
            levels_timeframe=params.levels_timeframe,
            entry_timeframe=params.entry_timeframe,
        )
        annotated = self._strategy.prepare_annotated_multi_tf_data(
            lower_base=prepared[1],
            lookback=params.lookback,
        )
        daily_levels = self._build_daily_levels_frame(
            higher_base=prepared[0],
            annotated=annotated,
            lookback=params.lookback,
        )
        return annotated, daily_levels

    @staticmethod
    def _build_daily_levels_frame(
        *,
        higher_base: pd.DataFrame,
        annotated: pd.DataFrame,
        lookback: int,
    ) -> pd.DataFrame:
        daily_from_levels = pd.DataFrame(columns=["timestamp", "level_high", "level_low"])
        if not higher_base.empty:
            daily_from_levels = higher_base.copy()
            daily_from_levels["level_high"] = daily_from_levels["high"].rolling(window=lookback).max().shift(1)
            daily_from_levels["level_low"] = daily_from_levels["low"].rolling(window=lookback).min().shift(1)
            daily_from_levels = (
                daily_from_levels.dropna(subset=["level_high", "level_low"])
                .sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"], keep="last")[["timestamp", "level_high", "level_low"]]
                .reset_index(drop=True)
            )
        if not daily_from_levels.empty:
            return daily_from_levels

        if annotated.empty:
            return pd.DataFrame(columns=["timestamp", "level_high", "level_low"])

        daily_from_annotated = annotated.copy()
        if "level_high" not in daily_from_annotated.columns or "level_low" not in daily_from_annotated.columns:
            return pd.DataFrame(columns=["timestamp", "level_high", "level_low"])
        return (
            daily_from_annotated.sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")[["timestamp", "level_high", "level_low"]]
            .reset_index(drop=True)
        )

    def plot_daily_levels(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
        output_dir: Path | str,
    ) -> Path | None:
        """Строит 2 панели: 15m OHLC + дневные уровни high/low."""
        annotated, daily_levels = self._load_annotated_and_daily_levels(symbol=symbol, params=params)
        if annotated.empty:
            return None

        annotated = self._prepare_plot_frame(annotated)
        daily_levels = self._prepare_plot_frame(daily_levels)

        fig, (ax_top, ax_bottom) = plt.subplots(
            2,
            1,
            figsize=(16, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 2]},
        )
        fig.patch.set_facecolor("#020617")

        self._apply_dark_theme(ax_top)
        self._apply_dark_theme(ax_bottom)

        plot_time = annotated["plot_time"]
        self._plot_candles(ax_top, annotated)
        high_line, = ax_top.plot(plot_time, annotated["level_high"], color="#38bdf8", linewidth=1.4)
        low_line, = ax_top.plot(plot_time, annotated["level_low"], color="#f97316", linewidth=1.4)
        ax_top.set_title(f"{symbol}: MTF levels overview", color="#f8fafc", fontsize=13, fontweight="bold")
        self._add_styled_legend(
            ax_top,
            [
                Line2D([0], [0], color="#22c55e", linewidth=6, label="Bull candle"),
                Line2D([0], [0], color="#ef4444", linewidth=6, label="Bear candle"),
                Line2D([0], [0], color=high_line.get_color(), linewidth=2.2, label="1D level high"),
                Line2D([0], [0], color=low_line.get_color(), linewidth=2.2, label="1D level low"),
            ],
        )

        daily_high_line, = ax_bottom.plot(daily_levels["plot_time"], daily_levels["level_high"], color="#38bdf8", linewidth=1.5)
        daily_low_line, = ax_bottom.plot(daily_levels["plot_time"], daily_levels["level_low"], color="#f97316", linewidth=1.5)
        ax_bottom.fill_between(
            daily_levels["plot_time"],
            daily_levels["level_low"],
            daily_levels["level_high"],
            color="#94a3b8",
            alpha=0.18,
        )
        ax_bottom.set_title("Daily high/low bands", color="#f8fafc", fontsize=11)
        self._add_styled_legend(
            ax_bottom,
            [
                Line2D([0], [0], color=daily_high_line.get_color(), linewidth=2.2, label="Daily high"),
                Line2D([0], [0], color=daily_low_line.get_color(), linewidth=2.2, label="Daily low"),
                Line2D([0], [0], color="#94a3b8", linewidth=6, alpha=0.5, label="Daily range"),
            ],
        )

        ax_bottom.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
        ax_bottom.xaxis.set_major_formatter(self._timestamp_formatter("%Y-%m-%d"))
        ax_bottom.set_ylabel("Price", color="#cbd5e1")
        fig.autofmt_xdate(rotation=30)

        date_range = self._format_date_range(annotated)
        output_path = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol) / f"daily_levels_{date_range}.png"
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        return output_path


    @staticmethod
    def _select_trade_window(
        annotated: pd.DataFrame,
        *,
        entry_timestamp_ms: int,
        exit_timestamp_ms: int,
        padding_bars: int = 40,
    ) -> pd.DataFrame:
        if annotated.empty:
            return annotated
        timestamps = annotated["timestamp"].to_numpy(dtype="int64", copy=False)
        start_idx = max(int(timestamps.searchsorted(entry_timestamp_ms, side="left")) - padding_bars, 0)
        end_idx = min(int(timestamps.searchsorted(exit_timestamp_ms, side="right")) + padding_bars, len(annotated))
        return annotated.iloc[start_idx:end_idx].reset_index(drop=True)

    def plot_retests(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
        output_dir: Path | str,
        retest_spans: list[RetestPlotSpan],
    ) -> list[Path]:
        """Строит отдельные графики ретестов по заранее вычисленным диапазонам."""
        annotated = self._load_annotated(symbol=symbol, params=params)
        if annotated.empty:
            return []
        annotated = self._prepare_plot_frame(annotated)

        symbol_spans = [span for span in retest_spans if span.symbol == symbol]
        if not symbol_spans:
            return []

        symbol_dir = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol)
        saved_paths: list[Path] = []
        for span in symbol_spans:
            fig, ax = plt.subplots(1, 1, figsize=(14, 6))
            fig.patch.set_facecolor("#020617")
            self._apply_dark_theme(ax)
            plot_time = annotated["plot_time"]
            self._plot_candles(ax, annotated)
            ax.hlines(
                y=span.level_price,
                xmin=plot_time.iloc[0],
                xmax=plot_time.iloc[-1],
                color="#38bdf8",
                linestyle="--",
                linewidth=1.2,
            )

            x_start = pd.to_datetime(span.retest_start_timestamp_ms, unit="ms")
            x_end = pd.to_datetime(span.retest_end_timestamp_ms, unit="ms")
            x_start_num = mdates.date2num(x_start)
            x_end_num = mdates.date2num(x_end)

            rect = Rectangle(
                (x_start_num, span.retest_low),
                max(x_end_num - x_start_num, 1e-9),
                span.retest_high - span.retest_low,
                facecolor="#f59e0b",
                alpha=0.28,
                edgecolor="#f97316",
                linewidth=1.0,
            )
            ax.add_patch(rect)
            self._add_styled_legend(
                ax,
                [
                    Line2D([0], [0], color="#22c55e", linewidth=6, label="Bull candle"),
                    Line2D([0], [0], color="#ef4444", linewidth=6, label="Bear candle"),
                    Line2D([0], [0], color="#38bdf8", linestyle="--", linewidth=2, label="Daily level"),
                    Line2D([0], [0], color="#f59e0b", linewidth=6, alpha=0.6, label=f"Retest ({span.status})"),
                ],
            )
            start_str = str(span.retest_start_timestamp_ms)
            ax.set_title(f"{symbol} retest {span.side.value} ({span.status}) @ {start_str}", color="#f8fafc", fontweight="bold")
            ax.set_ylabel("Price", color="#cbd5e1")
            ax.xaxis.set_major_formatter(self._timestamp_formatter("%Y-%m-%d %H:%M"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
            fig.autofmt_xdate(rotation=30)

            timestamp = span.retest_start_timestamp_ms
            output_path = symbol_dir / f"retest_{timestamp}_{span.side.value}_{span.status}.png"
            fig.tight_layout()
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            saved_paths.append(output_path)

        return saved_paths


    def plot_trade_setups(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
        output_dir: Path | str,
        trade_spans: list[TradePlotSpan],
    ) -> list[Path]:
        """Строит отдельные графики сделок с точками входа/выхода и уровнями TP/SL."""
        annotated, higher_tf_levels = self._load_annotated_and_daily_levels(symbol=symbol, params=params)
        if annotated.empty:
            return []
        annotated = self._prepare_plot_frame(annotated)
        higher_tf_levels = self._prepare_plot_frame(higher_tf_levels)

        symbol_data = self._data_preparer.load_symbol_data_multi(
            symbol,
            (params.levels_timeframe,),
        )
        higher_tf_frame = self._prepare_plot_frame(symbol_data.get(params.levels_timeframe, pd.DataFrame()))

        symbol_trades = [span for span in trade_spans if span.symbol == symbol]
        if not symbol_trades:
            return []

        symbol_dir = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol)
        saved_paths: list[Path] = []

        for idx, span in enumerate(symbol_trades, start=1):
            trade_window = self._select_trade_window(
                annotated,
                entry_timestamp_ms=span.entry_timestamp_ms,
                exit_timestamp_ms=span.exit_timestamp_ms,
            )
            if trade_window.empty:
                continue

            fig, (ax_top, ax_bottom) = plt.subplots(
                2,
                1,
                figsize=(16, 9),
                gridspec_kw={"height_ratios": [3, 2]},
            )
            fig.patch.set_facecolor(self.TV_BG)
            self._apply_dark_theme(ax_top)
            self._apply_dark_theme(ax_bottom)
            self._plot_candles(ax_top, trade_window)

            entry_time = pd.to_datetime(span.entry_timestamp_ms, unit="ms")
            exit_time = pd.to_datetime(span.exit_timestamp_ms, unit="ms")
            entry_label = pd.to_datetime(span.entry_timestamp_ms, unit="ms", utc=True).strftime("%Y%m%d_%H%M")
            exit_label = pd.to_datetime(span.exit_timestamp_ms, unit="ms", utc=True).strftime("%Y%m%d_%H%M")

            entry_bar_width_days = 0.0
            if len(trade_window) > 1:
                trade_days = np.asarray(mdates.date2num(trade_window["plot_time"]), dtype=float)
                entry_bar_width_days = float((trade_days[1] - trade_days[0]) * 15.0)
            if entry_bar_width_days <= 0.0:
                entry_bar_width_days = 0.8
            zone_end = entry_time + pd.to_timedelta(entry_bar_width_days, unit="D")

            ax_top.hlines(span.entry_price, entry_time, zone_end, color=self.TV_ENTRY, linestyle="-", linewidth=1.8)

            price_range = float(trade_window["high"].max() - trade_window["low"].min())
            vertical_padding = max(price_range * 0.005, 1e-9)
            self._add_price_zone(
                ax_top,
                x_start=entry_time,
                x_end=zone_end,
                price=span.stop_loss,
                label="SL",
                facecolor="#a6324a",
                edgecolor="none",
                text_color="#fecaca",
                vertical_padding=vertical_padding,
            )
            self._add_price_zone(
                ax_top,
                x_start=entry_time,
                x_end=zone_end,
                price=span.take_profit_1,
                label="TP1",
                facecolor="#21875e",
                edgecolor="none",
                text_color="#dcfce7",
                vertical_padding=vertical_padding,
            )
            self._add_price_zone(
                ax_top,
                x_start=entry_time,
                x_end=zone_end,
                price=span.take_profit_2,
                label="TP2",
                facecolor="#0f6d54",
                edgecolor="none",
                text_color="#bbf7d0",
                vertical_padding=vertical_padding,
            )

            ax_top.scatter([entry_time], [span.entry_price], color=self.TV_ENTRY, marker="^", s=80, zorder=5)
            ax_top.scatter([exit_time], [span.exit_price], color="#a855f7", marker="X", s=80, zorder=5)

            if not higher_tf_frame.empty:
                higher_window = higher_tf_frame[
                    (higher_tf_frame["timestamp"] >= span.entry_timestamp_ms - 7 * 24 * 60 * 60 * 1000)
                    & (higher_tf_frame["timestamp"] <= span.exit_timestamp_ms + 7 * 24 * 60 * 60 * 1000)
                ].reset_index(drop=True)
                if higher_window.empty:
                    higher_window = higher_tf_frame
                self._plot_candles(ax_bottom, higher_window)

                level_slice = higher_tf_levels[
                    (higher_tf_levels["timestamp"] >= higher_window["timestamp"].min())
                    & (higher_tf_levels["timestamp"] <= higher_window["timestamp"].max())
                ]
                if not level_slice.empty:
                    ax_bottom.plot(
                        level_slice["plot_time"],
                        level_slice["level_high"],
                        color=self.TV_ENTRY,
                        linewidth=1.3,
                        linestyle="--",
                        alpha=0.9,
                    )
                    ax_bottom.plot(
                        level_slice["plot_time"],
                        level_slice["level_low"],
                        color="#f59e0b",
                        linewidth=1.2,
                        linestyle="--",
                        alpha=0.8,
                    )

            self._add_styled_legend(
                ax_top,
                [
                    Line2D([0], [0], color=self.TV_BULL, linewidth=6, label="Bull candle"),
                    Line2D([0], [0], color=self.TV_BEAR, linewidth=6, label="Bear candle"),
                    Line2D([0], [0], color=self.TV_ENTRY, linewidth=2.5, label="Entry"),
                    Line2D([0], [0], color="#a6324a", linewidth=6, alpha=0.6, label="SL zone"),
                    Line2D([0], [0], color="#21875e", linewidth=6, alpha=0.6, label="TP1 zone"),
                    Line2D([0], [0], color="#0f6d54", linewidth=6, alpha=0.6, label="TP2 zone"),
                    Line2D([0], [0], marker="^", color=self.TV_ENTRY, linestyle="None", markersize=9, label="Entry candle"),
                    Line2D([0], [0], marker="X", color="#a855f7", linestyle="None", markersize=9, label="Exit candle"),
                ],
            )
            self._add_styled_legend(
                ax_bottom,
                [
                    Line2D([0], [0], color=self.TV_BULL, linewidth=6, label="Bull candle"),
                    Line2D([0], [0], color=self.TV_BEAR, linewidth=6, label="Bear candle"),
                    Line2D([0], [0], color=self.TV_ENTRY, linestyle="--", linewidth=2, label="Higher TF level high"),
                    Line2D([0], [0], color="#f59e0b", linestyle="--", linewidth=2, label="Higher TF level low"),
                ],
            )
            ax_top.set_title(
                f"{symbol} • {params.entry_timeframe.value} trade {span.side.value}: result={span.result_type}",
                color="#f8fafc",
                fontweight="bold",
            )
            ax_top.set_ylabel("Price", color=self.TV_TEXT)
            ax_bottom.set_title(f"{params.levels_timeframe.value} context", color="#f8fafc", fontsize=10)
            ax_bottom.set_ylabel("Price", color=self.TV_TEXT)
            self._format_time_axis(ax_top)
            self._format_time_axis(ax_bottom)
            fig.autofmt_xdate(rotation=30)

            output_path = symbol_dir / (
                f"trade_{idx:03d}_{entry_label}_to_{exit_label}_{span.side.value}_{span.result_type}.png"
            )
            fig.tight_layout()
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            saved_paths.append(output_path)

        return saved_paths
