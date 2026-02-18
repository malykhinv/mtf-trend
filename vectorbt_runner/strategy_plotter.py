"""Визуализация уровней и ретестов для MTF-стратегии пробоя."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Rectangle

from domain.enums.timeframe import Timeframe
from domain.models.retest_plot_span import RetestPlotSpan
from domain.models.trade_plot_span import TradePlotSpan
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import BreakoutParams
from vectorbt_runner.data_preparer import DataPreparer
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class StrategyPlotter:
    """Read-only визуализатор уровней старшего ТФ и ретестов младшего ТФ."""

    def __init__(self, data_preparer: DataPreparer, strategy: BreakoutStrategy) -> None:
        self._data_preparer = data_preparer
        self._strategy = strategy

    @staticmethod
    def _resolve_symbol_output_dir(output_dir: Path | str, symbol: str) -> Path:
        symbol_dir = Path(output_dir) / symbol
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
        _ = fmt

        def _format_timestamp(value: float, _position: float) -> str:
            return str(int(value))

        return FuncFormatter(_format_timestamp)

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

        fig, (ax_top, ax_bottom) = plt.subplots(
            2,
            1,
            figsize=(16, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 2]},
        )

        plot_time = annotated["timestamp"]
        ax_top.plot(plot_time, annotated["close"], label="15m close", color="black", linewidth=1.0)
        ax_top.plot(plot_time, annotated["level_high"], label="1d level_high", color="green", linewidth=1.2)
        ax_top.plot(plot_time, annotated["level_low"], label="1d level_low", color="red", linewidth=1.2)
        ax_top.set_title(f"{symbol}: 15m candles with 1d levels")
        ax_top.grid(alpha=0.3)
        ax_top.legend(loc="upper left")

        ax_bottom.plot(daily_levels["timestamp"], daily_levels["level_high"], label="Daily level high", color="green")
        ax_bottom.plot(daily_levels["timestamp"], daily_levels["level_low"], label="Daily level low", color="red")
        ax_bottom.fill_between(
            daily_levels["timestamp"],
            daily_levels["level_low"],
            daily_levels["level_high"],
            color="lightgray",
            alpha=0.35,
            label="Daily range",
        )
        ax_bottom.set_title("Daily high/low levels (1 point per day)")
        ax_bottom.grid(alpha=0.3)
        ax_bottom.legend(loc="upper left")

        ax_bottom.xaxis.set_major_formatter(self._timestamp_formatter("%Y-%m-%d"))
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

        symbol_spans = [span for span in retest_spans if span.symbol == symbol]
        if not symbol_spans:
            return []

        symbol_dir = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol)
        saved_paths: list[Path] = []
        for span in symbol_spans:
            fig, ax = plt.subplots(1, 1, figsize=(14, 6))
            plot_time = annotated["timestamp"]
            ax.plot(plot_time, annotated["close"], color="black", linewidth=1.0, label="15m close")
            ax.axhline(span.level_price, color="royalblue", linestyle="--", linewidth=1.2, label="daily level")

            x_start = span.retest_start_timestamp_ms
            x_end = span.retest_end_timestamp_ms
            rect = Rectangle(
                (x_start, span.retest_low),
                max(x_end - x_start, 1e-9),
                span.retest_high - span.retest_low,
                facecolor="orange",
                alpha=0.35,
                edgecolor="darkorange",
                linewidth=1.0,
                label=f"retest zone ({span.status})",
            )
            ax.add_patch(rect)
            ax.grid(alpha=0.3)
            ax.legend(loc="upper left")
            start_str = str(span.retest_start_timestamp_ms)
            ax.set_title(f"{symbol} retest {span.side.value} ({span.status}) @ {start_str}")
            ax.xaxis.set_major_formatter(self._timestamp_formatter("%Y-%m-%d %H:%M"))
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
        annotated = self._load_annotated(symbol=symbol, params=params)
        if annotated.empty:
            return []

        symbol_trades = [span for span in trade_spans if span.symbol == symbol]
        if not symbol_trades:
            return []

        symbol_dir = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol)
        saved_paths: list[Path] = []

        for span in symbol_trades:
            trade_window = self._select_trade_window(
                annotated,
                entry_timestamp_ms=span.entry_timestamp_ms,
                exit_timestamp_ms=span.exit_timestamp_ms,
            )
            if trade_window.empty:
                continue

            fig, ax = plt.subplots(1, 1, figsize=(14, 6))
            plot_time = trade_window["timestamp"]
            ax.plot(plot_time, trade_window["close"], color="black", linewidth=1.0, label="15m close")

            ax.axhline(span.entry_price, color="royalblue", linestyle="-", linewidth=1.2, label="entry")
            ax.axhline(span.stop_loss, color="red", linestyle="--", linewidth=1.2, label="stop_loss")
            ax.axhline(span.take_profit_1, color="green", linestyle="--", linewidth=1.2, label="tp1")
            ax.axhline(span.take_profit_2, color="darkgreen", linestyle="--", linewidth=1.2, label="tp2")

            ax.scatter([span.entry_timestamp_ms], [span.entry_price], color="blue", marker="^", s=70, label="entry point")
            ax.scatter([span.exit_timestamp_ms], [span.exit_price], color="purple", marker="X", s=70, label="exit point")

            ax.grid(alpha=0.3)
            ax.legend(loc="upper left")
            ax.set_title(f"{symbol} trade {span.side.value}: result={span.result_type}")
            ax.xaxis.set_major_formatter(self._timestamp_formatter("%Y-%m-%d %H:%M"))
            fig.autofmt_xdate(rotation=30)

            output_path = symbol_dir / f"trade_{span.entry_timestamp_ms}_{span.side.value}_{span.result_type}.png"
            fig.tight_layout()
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            saved_paths.append(output_path)

        return saved_paths
