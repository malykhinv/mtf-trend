"""Parameter-grid backtest runner and metrics calculator."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult


@dataclass(slots=True)
class BacktestSummary:
    total_combinations: int
    profitable_combinations: int
    best_pf: float


class BacktestRunner:
    """Runs strategy over parameter combinations and stores CSV output."""

    def __init__(self, results_dir: Path, results_file_name: str) -> None:
        self._results_dir = Path(results_dir)
        self._results_file_name = results_file_name

    def build_parameter_grid(self) -> list[dict[str, Any]]:
        lookback = [13, 21, 34]
        volume_mult = [1.2, 1.5, 2.0]
        retest_window = [2, 4, 6]
        retest_zone = [0.002, 0.003]
        min_rr = [1.0, 1.5, 2.0]
        sl_mode = ["LEVEL", "BREAKOUT_EXTREME"]
        tp2_mult = [1.25, 1.5, 2.0]

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
        profitable = int((results["profit_factor"] > 1.0).sum()) if not results.empty else 0
        best_pf = float(results["profit_factor"].max()) if not results.empty else 0.0
        return BacktestSummary(
            total_combinations=int(len(results)),
            profitable_combinations=profitable,
            best_pf=best_pf,
        )

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

        pnl_values = [trade.pnl for trade in trades]
        profits = sum(value for value in pnl_values if value > 0)
        losses = abs(sum(value for value in pnl_values if value < 0))
        pf = profits / losses if losses > 0 else (99.0 if profits > 0 else 0.0)

        wins = sum(1 for value in pnl_values if value > 0)
        pnl_percent = sum(trade.pnl_percent.value for trade in trades)
        equity = pd.Series(pnl_values).cumsum()
        drawdown = float((equity.cummax() - equity).max()) if not equity.empty else 0.0

        result_types = [trade.result_type for trade in trades]
        return {
            **params,
            "profit_factor": round(float(pf), 4),
            "pnl_percent": round(float(pnl_percent), 4),
            "win_rate": round(wins / len(trades), 4),
            "trades_count": len(trades),
            "max_dd": round(drawdown, 6),
            "sl_count": result_types.count(TradeResultType.SL),
            "be_count": result_types.count(TradeResultType.BE),
            "tp1_be_count": result_types.count(TradeResultType.TP1_BE),
            "tp2_count": result_types.count(TradeResultType.TP2),
        }

    def _save_results(self, results: pd.DataFrame) -> None:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        results.to_csv(self._results_dir / self._results_file_name, index=False)
