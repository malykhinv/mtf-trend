"""Parameter-grid backtest runner and metrics calculator."""

from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any
import logging

import pandas as pd

from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from strategy.breakout.config import BREAKOUT_PARAMETER_GRID, PARAMETER_GRID_SIZE, TARGET_PARAMETER_COMBINATIONS
from vectorbt_runner.backtest_summary import BacktestSummary
from vectorbt_runner.data_preparer import DataPreparer

try:
    import vectorbt as vbt
except ImportError:  # pragma: no cover - environment dependent
    vbt = None


logger = logging.getLogger(__name__)


class BacktestRunner:
    """Runs strategy over parameter combinations and stores CSV output."""

    def __init__(self, results_dir: Path, results_file_name: str) -> None:
        self._results_dir = Path(results_dir)
        self._results_file_name = results_file_name

    def build_parameter_grid(self) -> list[dict[str, Any]]:
        lookback = BREAKOUT_PARAMETER_GRID["lookback"]
        volume_mult = BREAKOUT_PARAMETER_GRID["volume_mult"]
        retest_window = BREAKOUT_PARAMETER_GRID["retest_window"]
        retest_zone = BREAKOUT_PARAMETER_GRID["retest_zone"]
        min_rr = BREAKOUT_PARAMETER_GRID["min_rr"]
        sl_mode = BREAKOUT_PARAMETER_GRID["sl_mode"]
        tp2_mult = BREAKOUT_PARAMETER_GRID["tp2_mult"]

        # combos = |lookback| × |volume_mult| × |retest_window| × |retest_zone| × |min_rr| × |sl_mode| × |tp2_mult| = 6×3×3×3×3×2×6 = 5832
        return [
            {
                "lookback": lb,
                "volume_mult": vm,
                "retest_window": rw,
                "retest_zone": rz,
                "min_rr": rr,
                "sl_mode": sl,
                "tp2_mult": tp2,
            }
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

    def run(self, strategy: Any, symbol_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        self._ensure_vectorbt_available()

        rows: list[dict[str, Any]] = []
        grid = self.build_parameter_grid()

        for params in grid:
            all_trades: list[TradeResult] = []
            for symbol, frame in symbol_frames.items():
                cfg = {**params, "symbol": symbol}
                trades = strategy.generate_events(frame, cfg)
                all_trades.extend(trades)

            row = self._build_metrics_row(params, all_trades)
            rows.append(row)

        results = pd.DataFrame(rows).sort_values("profit_factor", ascending=False).reset_index(drop=True)
        self._save_results(results)
        return results

    def build_summary(self, results: pd.DataFrame) -> BacktestSummary:
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

        profitable = int((results["profit_factor"] > 1.0).sum()) if not results.empty else 0
        best_pf = float(results["profit_factor"].max()) if not results.empty else 0.0
        return BacktestSummary(
            total_combinations=int(len(results)),
            profitable_combinations=profitable,
            best_pf=best_pf,
        )

    def _ensure_vectorbt_available(self) -> None:
        if vbt is None:
            msg = (
                "vectorbt is not installed in the current environment. "
                "Install it (for example: pip install vectorbt) and retry run-backtest."
            )
            raise RuntimeError(msg)

    def _build_metrics_row(self, params: dict[str, Any], trades: list[TradeResult]) -> dict[str, Any]:
        if not trades:
            return {
                **params,
                "profit_factor": 0.0,
                "pnl_percent": 0.0,
                "win_rate": 0.0,
                "trades_count": 0,
                "max_dd": 0.0,
                "sl_count": 0,
                "be_count": 0,
                "tp1_be_count": 0,
                "tp2_count": 0,
            }

        prepared = DataPreparer.prepare_vectorbt_inputs(trades)
        portfolio = vbt.Portfolio.from_signals(
            close=prepared.close,
            entries=prepared.entries,
            exits=prepared.exits,
            direction="longonly",
            init_cash=100.0,
            size=1.0,
            size_type="amount",
            fees=0.0,
            slippage=0.0,
            freq="1min",
        )

        pnl_values = [trade.pnl for trade in trades]
        pnl_percent = sum(trade.pnl_percent.value for trade in trades)

        pf = portfolio.trades.profit_factor()
        win_rate = portfolio.trades.win_rate()
        trades_count = int(portfolio.trades.count())

        max_dd = portfolio.drawdowns.max_drawdown()
        if pd.isna(max_dd):
            max_dd = 0.0
        else:
            max_dd = abs(float(max_dd))

        if pd.isna(pf):
            profits = sum(value for value in pnl_values if value > 0)
            losses = abs(sum(value for value in pnl_values if value < 0))
            pf = profits / losses if losses > 0 else (99.0 if profits > 0 else 0.0)

        if pd.isna(win_rate):
            wins = sum(1 for value in pnl_values if value > 0)
            win_rate = wins / len(trades)

        result_types = [trade.result_type for trade in trades]
        return {
            **params,
            "profit_factor": round(float(pf), 4),
            "pnl_percent": round(float(pnl_percent), 4),
            "win_rate": round(float(win_rate), 4),
            "trades_count": trades_count,
            "max_dd": round(float(max_dd), 6),
            "sl_count": result_types.count(TradeResultType.SL),
            "be_count": result_types.count(TradeResultType.BE),
            "tp1_be_count": result_types.count(TradeResultType.TP1_BE),
            "tp2_count": result_types.count(TradeResultType.TP2),
        }

    def _save_results(self, results: pd.DataFrame) -> None:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        results.to_csv(self._results_dir / self._results_file_name, index=False)
