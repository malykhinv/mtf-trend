"""Визуализация уровней и ретестов для MTF-стратегии пробоя."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from domain.enums.position_side import PositionSide
from domain.enums.timeframe import Timeframe
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import BreakoutParams
from strategy.breakout.pending_breakout import PendingBreakout
from strategy.breakout.pending_retest import PendingRetest
from vectorbt_runner.data_preparer import DataPreparer
from vectorbt_runner.mtf_frames import SymbolMtfFrames


@dataclass(frozen=True, slots=True)
class RetestPlotEvent:
    """Снимок события ретеста для построения графика."""

    symbol: str
    side: PositionSide
    timestamp: pd.Timestamp
    level_price: float
    retest_low: float
    retest_high: float
    x_start: pd.Timestamp
    x_end: pd.Timestamp


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
    def _build_mtf_frames(symbol_data: dict[Timeframe, pd.DataFrame]) -> SymbolMtfFrames:
        return SymbolMtfFrames(
            levels_timeframe=Timeframe.D1,
            entry_timeframe=Timeframe.M15,
            levels_frame=symbol_data.get(Timeframe.D1, pd.DataFrame()),
            entry_frame=symbol_data.get(Timeframe.M15, pd.DataFrame()),
        )

    @staticmethod
    def _resolve_candle_end(annotated: pd.DataFrame, idx: int) -> pd.Timestamp:
        current = pd.Timestamp(annotated.iloc[idx]["datetime"])
        if idx + 1 < len(annotated):
            return pd.Timestamp(annotated.iloc[idx + 1]["datetime"])
        if idx > 0:
            previous = pd.Timestamp(annotated.iloc[idx - 1]["datetime"])
            return current + (current - previous)
        return current + pd.Timedelta(minutes=15)

    def _load_annotated(self, symbol: str, params: BreakoutParams) -> pd.DataFrame:
        symbol_data = self._data_preparer.load_symbol_data_multi(
            symbol,
            (params.levels_timeframe, params.entry_timeframe),
        )
        mtf_frames = self._build_mtf_frames(symbol_data)
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

    def _collect_retests(self, annotated: pd.DataFrame, params: BreakoutParams) -> list[RetestPlotEvent]:
        events: list[RetestPlotEvent] = []
        pending_breakout: PendingBreakout | None = None
        pending_retest: PendingRetest | None = None
        retest_window_candles = max(
            1,
            self._strategy._hours_to_candles(params.retest_window_hours, params.entry_timeframe),
        )

        for idx in range(len(annotated)):
            row = annotated.iloc[idx]

            if pending_retest is not None:
                pending_retest = None
                continue

            if pending_breakout is not None:
                breakout_idx = pending_breakout.breakout_idx
                if idx - breakout_idx > retest_window_candles:
                    pending_breakout = None
                elif self._strategy._is_retest_candle(row=row, breakout=pending_breakout, params=params):
                    volume_check = self._strategy._evaluate_volume_regime(
                        annotated=annotated,
                        breakout=pending_breakout,
                        breakout_idx=breakout_idx,
                        retest_idx=idx,
                        volume_mult=params.volume_mult,
                    )
                    extra_filters = self._strategy._extra_retest_filter_metrics(
                        row=row,
                        breakout=pending_breakout,
                        params=params,
                    )
                    if volume_check["is_ok"] and extra_filters["is_ok"]:
                        pending_retest = PendingRetest(
                            breakout=pending_breakout,
                            retest_idx=idx,
                            retest_low=float(row["low"]),
                            retest_high=float(row["high"]),
                            confirmation_end_idx=idx + max(1, int(params.confirmation_bars)),
                            volume_before=float(volume_check["v_before"]),
                            volume_after=float(volume_check["v_after"]),
                            volume_threshold=float(volume_check["threshold"]),
                            volume_filter_passed=bool(volume_check["is_ok"]),
                        )
                        events.append(
                            RetestPlotEvent(
                                symbol=params.symbol,
                                side=pending_retest.breakout.side,
                                timestamp=pd.Timestamp(row["datetime"]),
                                level_price=pending_retest.breakout.level.price.value,
                                retest_low=pending_retest.retest_low,
                                retest_high=pending_retest.retest_high,
                                x_start=pd.Timestamp(row["datetime"]),
                                x_end=self._resolve_candle_end(annotated, idx),
                            )
                        )
                        pending_breakout = None

            if pending_breakout is None:
                level_high = float(row["level_high"])
                level_low = float(row["level_low"])
                breakout_long = float(row["close"]) > level_high
                breakout_short = float(row["close"]) < level_low
                if breakout_long:
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=self._strategy._build_level(
                            price=level_high,
                            side=PositionSide.LONG,
                            row=row,
                            lookback=params.lookback,
                            volume_before=self._strategy._average_volume_before(
                                annotated=annotated,
                                breakout_idx=idx,
                                level_start_time=row["level_start_time"],
                            ),
                        ),
                        breakout_extreme=float(row["low"]),
                        side=PositionSide.LONG,
                        level_start_time=row["level_start_time"],
                    )
                elif breakout_short:
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=self._strategy._build_level(
                            price=level_low,
                            side=PositionSide.SHORT,
                            row=row,
                            lookback=params.lookback,
                            volume_before=self._strategy._average_volume_before(
                                annotated=annotated,
                                breakout_idx=idx,
                                level_start_time=row["level_start_time"],
                            ),
                        ),
                        breakout_extreme=float(row["high"]),
                        side=PositionSide.SHORT,
                        level_start_time=row["level_start_time"],
                    )

        return events

    def plot_retests(
        self,
        *,
        symbol: str,
        params: BreakoutParams,
        output_dir: Path | str,
    ) -> list[Path]:
        """Строит отдельные графики ретестов с уровнем и прямоугольником зоны."""
        annotated = self._load_annotated(symbol=symbol, params=params)
        if annotated.empty:
            return []

        events = self._collect_retests(annotated=annotated, params=params)
        if not events:
            return []

        symbol_dir = self._resolve_symbol_output_dir(output_dir=output_dir, symbol=symbol)
        saved_paths: list[Path] = []
        for event in events:
            fig, ax = plt.subplots(1, 1, figsize=(14, 6))
            ax.plot(annotated["datetime"], annotated["close"], color="black", linewidth=1.0, label="15m close")
            ax.axhline(event.level_price, color="royalblue", linestyle="--", linewidth=1.2, label="daily level")

            x_start = mdates.date2num(event.x_start.to_pydatetime())
            x_end = mdates.date2num(event.x_end.to_pydatetime())
            rect = Rectangle(
                (x_start, event.retest_low),
                max(x_end - x_start, 1e-9),
                event.retest_high - event.retest_low,
                facecolor="orange",
                alpha=0.35,
                edgecolor="darkorange",
                linewidth=1.0,
                label="retest zone",
            )
            ax.add_patch(rect)
            ax.xaxis_date()
            ax.grid(alpha=0.3)
            ax.legend(loc="upper left")
            ax.set_title(f"{symbol} retest {event.side.value} @ {event.timestamp}")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M"))
            fig.autofmt_xdate()

            timestamp = event.timestamp.strftime("%Y%m%d_%H%M%S")
            output_path = symbol_dir / f"retest_{timestamp}_{event.side.value}.png"
            fig.tight_layout()
            fig.savefig(output_path, dpi=150)
            plt.close(fig)
            saved_paths.append(output_path)

        return saved_paths
