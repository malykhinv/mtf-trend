"""Визуализация уровней и ретестов для MTF-стратегии пробоя."""

from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from domain.enums.timeframe import Timeframe
from domain.models.retest_plot_span import RetestPlotSpan
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
        start = pd.Timestamp(annotated["datetime"].iloc[0]).strftime("%Y%m%d")
        end = pd.Timestamp(annotated["datetime"].iloc[-1]).strftime("%Y%m%d")
        return f"{start}_{end}"

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
        return self._strategy.prepare_annotated_multi_tf_data(
            prepared_multi_tf=prepared,
            lookback=params.lookback,
        )

    def plot_daily_levels(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
        output_dir: Path | str,
    ) -> Path | None:
        """Строит 2 панели: 15m OHLC + дневные уровни high/low."""
        annotated = self._load_annotated(symbol=symbol, params=params)
        if annotated.empty:
            return None

        fig, (ax_top, ax_bottom) = plt.subplots(
            2,
            1,
            figsize=(16, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 2]},
        )

        ax_top.plot(annotated["datetime"], annotated["close"], label="15m close", color="black", linewidth=1.0)
        ax_top.plot(annotated["datetime"], annotated["level_high"], label="1d level_high", color="green", linewidth=1.2)
        ax_top.plot(annotated["datetime"], annotated["level_low"], label="1d level_low", color="red", linewidth=1.2)
        ax_top.set_title(f"{symbol}: 15m candles with 1d levels")
        ax_top.grid(alpha=0.3)
        ax_top.legend(loc="upper left")

        daily_levels = (
            annotated[["datetime", "level_high", "level_low"]]
            .drop_duplicates(subset=["datetime", "level_high", "level_low"])
            .sort_values("datetime")
        )
        ax_bottom.plot(daily_levels["datetime"], daily_levels["level_high"], label="1d high", color="green")
        ax_bottom.plot(daily_levels["datetime"], daily_levels["level_low"], label="1d low", color="red")
        ax_bottom.fill_between(
            daily_levels["datetime"],
            daily_levels["level_low"],
            daily_levels["level_high"],
            color="lightgray",
            alpha=0.35,
            label="daily range",
        )
        ax_bottom.set_title("Daily high/low levels")
        ax_bottom.grid(alpha=0.3)
        ax_bottom.legend(loc="upper left")

        ax_bottom.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.autofmt_xdate()

        date_range = self._format_date_range(annotated)
        output_path = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol) / f"daily_levels_{date_range}.png"
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        return output_path

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
            ax.plot(annotated["datetime"], annotated["close"], color="black", linewidth=1.0, label="15m close")
            ax.axhline(span.level_price, color="royalblue", linestyle="--", linewidth=1.2, label="daily level")

            x_start = mdates.date2num(span.retest_start_time.to_pydatetime())
            x_end = mdates.date2num(span.retest_end_time.to_pydatetime())
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
            ax.xaxis_date()
            ax.grid(alpha=0.3)
            ax.legend(loc="upper left")
            ax.set_title(f"{symbol} retest {span.side.value} ({span.status}) @ {span.retest_start_time}")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M"))
            fig.autofmt_xdate()

            timestamp = span.retest_start_time.strftime("%Y%m%d_%H%M%S")
            output_path = symbol_dir / f"retest_{timestamp}_{span.side.value}_{span.status}.png"
            fig.tight_layout()
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            saved_paths.append(output_path)

        return saved_paths
