"""Parameter-grid backtest runner and metrics calculator."""

from __future__ import annotations

from itertools import product
from pathlib import Path
import logging

import pandas as pd

from constants import (
    BACKTEST_EMPTY_MAX_DD,
    BACKTEST_EMPTY_PF,
    BACKTEST_EMPTY_PNL_PERCENT,
    BACKTEST_EMPTY_TRADES_COUNT,
    BACKTEST_EMPTY_WIN_RATE,
    BACKTEST_PF_FALLBACK_WHEN_NO_LOSSES,
    BACKTEST_PROFITABLE_PF_THRESHOLD,
    BACKTEST_ROUND_MAX_DD,
    BACKTEST_ROUND_METRICS,
    BACKTEST_SORT_ASCENDING,
    BACKTEST_ZERO_COUNT,
)
from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.breakout.config import BREAKOUT_PARAMETER_GRID, PARAMETER_GRID_SIZE, TARGET_PARAMETER_COMBINATIONS, BreakoutParams
from vectorbt_runner.backtest_summary import BacktestSummary


logger = logging.getLogger(__name__)


class BacktestRunner:
    """Runs strategy over parameter combinations and stores CSV output."""

    def __init__(self, results_dir: Path, results_file_name: str) -> None:
        self._results_dir = Path(results_dir)
        self._results_file_name = results_file_name

    @staticmethod
    def build_parameter_grid() -> list[BreakoutParams]:
        lookback = BREAKOUT_PARAMETER_GRID["lookback"]
        volume_mult = BREAKOUT_PARAMETER_GRID["volume_mult"]
        retest_window = BREAKOUT_PARAMETER_GRID["retest_window"]
        retest_zone = BREAKOUT_PARAMETER_GRID["retest_zone"]
        min_rr = BREAKOUT_PARAMETER_GRID["min_rr"]
        sl_mode = BREAKOUT_PARAMETER_GRID["sl_mode"]
        tp2_mult = BREAKOUT_PARAMETER_GRID["tp2_mult"]

        # combos = |lookback| × |volume_mult| × |retest_window| × |retest_zone| × |min_rr| × |sl_mode| × |tp2_mult| = 6×3×3×3×3×2×6 = 5832
        return [
            BreakoutParams(
                lookback=int(lb),
                volume_mult=float(vm),
                retest_window=int(rw),
                retest_zone=float(rz),
                min_rr=float(rr),
                sl_mode=sl,
                tp2_mult=float(tp2),
                symbol="",
            )
            for lb, vm, rw, rz, rr, sl, tp2 in product(
                lookback,
                volume_mult,
                retest_window,
                retest_zone,
                min_rr,
                sl_mode,
                tp2_mult,
            )
        ]

    def run(self, strategy: BaseStrategy[BreakoutParams], symbol_frames: dict[str, dict[str, pd.DataFrame]]) -> pd.DataFrame:
        rows: list[dict[str, int | float | str]] = []
        grid = self.build_parameter_grid()

        for params in grid:
            all_trades: list[TradeResult] = []
            for symbol, frames_by_tf in symbol_frames.items():
                cfg = BreakoutParams(
                    lookback=params.lookback,
                    volume_mult=params.volume_mult,
                    retest_window=params.retest_window,
                    retest_zone=params.retest_zone,
                    min_rr=params.min_rr,
                    sl_mode=params.sl_mode,
                    tp2_mult=params.tp2_mult,
                    symbol=symbol,
                )
                higher_tf_key = cfg.levels_timeframe.value
                lower_tf_key = cfg.entry_timeframe.value
                if higher_tf_key not in frames_by_tf or lower_tf_key not in frames_by_tf:
                    continue
                trades = strategy.generate_events_multi_tf(
                    higher_tf_data=frames_by_tf[higher_tf_key],
                    lower_tf_data=frames_by_tf[lower_tf_key],
                    params=cfg,
                )
                all_trades.extend(trades)

            row = self._build_metrics_row(params, all_trades)
            rows.append(row)

        results = (
            pd.DataFrame(rows)
            .sort_values("profit_factor", ascending=BACKTEST_SORT_ASCENDING)
            .reset_index(drop=True)
        )
        self._save_results(results)
        return results

    @staticmethod
    def build_summary(results: pd.DataFrame) -> BacktestSummary:
        if PARAMETER_GRID_SIZE != TARGET_PARAMETER_COMBINATIONS:
            logger.warning(
                "run-backtest: расчетная мощность сетки=%s отличается от целевой=%s",
                PARAMETER_GRID_SIZE,
                TARGET_PARAMETER_COMBINATIONS,
            )
        if len(results) != TARGET_PARAMETER_COMBINATIONS:
            logger.warning(
                "run-backtest: фактическое число комбинаций=%s отличается от целевого=%s",
                len(results),
                TARGET_PARAMETER_COMBINATIONS,
            )

        profitable = int((results["profit_factor"] > BACKTEST_PROFITABLE_PF_THRESHOLD).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        best_pf = float(results["profit_factor"].max()) if not results.empty else BACKTEST_EMPTY_PF
        return BacktestSummary(
            total_combinations=int(len(results)),
            profitable_combinations=profitable,
            best_pf=best_pf,
        )

    # region Private

    @staticmethod
    def _build_metrics_row(params: BreakoutParams, trades: list[TradeResult]) -> dict[str, int | float | str]:
        base_row: dict[str, int | float | str] = {
            "lookback": params.lookback,
            "volume_mult": params.volume_mult,
            "retest_window": params.retest_window,
            "retest_zone": params.retest_zone,
            "min_rr": params.min_rr,
            "sl_mode": params.sl_mode.value,
            "tp2_mult": params.tp2_mult,
        }

        if not trades:
            return {
                **base_row,
                "profit_factor": BACKTEST_EMPTY_PF,
                "pnl_percent": BACKTEST_EMPTY_PNL_PERCENT,
                "win_rate": BACKTEST_EMPTY_WIN_RATE,
                "trades_count": BACKTEST_EMPTY_TRADES_COUNT,
                "max_dd": BACKTEST_EMPTY_MAX_DD,
                "sl_count": BACKTEST_ZERO_COUNT,
                "be_count": BACKTEST_ZERO_COUNT,
                "tp1_be_count": BACKTEST_ZERO_COUNT,
                "tp2_count": BACKTEST_ZERO_COUNT,
            }

        pnl_values = [trade.pnl for trade in trades]
        pnl_percent = sum(trade.pnl_percent.value for trade in trades)
        profits = sum(value for value in pnl_values if value > 0)
        losses = abs(sum(value for value in pnl_values if value < 0))

        if losses > 0:
            pf = profits / losses
        elif profits > 0:
            pf = BACKTEST_PF_FALLBACK_WHEN_NO_LOSSES
        else:
            pf = BACKTEST_EMPTY_PF

        wins = sum(1 for value in pnl_values if value > 0)
        win_rate = wins / len(trades)

        trades_count = len(trades)
        if trades_count != len(trades):
            msg = (
                "trades_count mismatch detected: derived trades_count "
                f"({trades_count}) differs from trades list length ({len(trades)})."
            )
            raise RuntimeError(msg)

        sorted_trades = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time))
        cumulative_pnl = BACKTEST_EMPTY_PNL_PERCENT
        peak_pnl = BACKTEST_EMPTY_PNL_PERCENT
        max_dd = BACKTEST_EMPTY_MAX_DD
        for trade in sorted_trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = peak_pnl - cumulative_pnl
            if drawdown > max_dd:
                max_dd = drawdown

        result_types = [trade.result_type for trade in trades]
        return {
            **base_row,
            "profit_factor": round(float(pf), BACKTEST_ROUND_METRICS),
            "pnl_percent": round(float(pnl_percent), BACKTEST_ROUND_METRICS),
            "win_rate": round(float(win_rate), BACKTEST_ROUND_METRICS),
            "trades_count": trades_count,
            "max_dd": round(float(max_dd), BACKTEST_ROUND_MAX_DD),
            "sl_count": result_types.count(TradeResultType.SL),
            "be_count": result_types.count(TradeResultType.BE),
            "tp1_be_count": result_types.count(TradeResultType.TP1_BE),
            "tp2_count": result_types.count(TradeResultType.TP2),
        }

    def _save_results(self, results: pd.DataFrame) -> None:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        results.to_csv(self._results_dir / self._results_file_name, index=False)

    # endregion Private
