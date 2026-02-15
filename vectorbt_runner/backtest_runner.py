"""Запуск бэктеста по сетке параметров и расчет метрик."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from time import perf_counter

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
from domain.enums.timeframe import Timeframe
from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from strategy.base_strategy import BaseStrategy

from strategy.breakout.config import BREAKOUT_PARAMETER_GRID, PARAMETER_GRID_SIZE, TARGET_PARAMETER_COMBINATIONS, \
    BreakoutParams
from strategy.breakout.breakout_strategy import BreakoutStrategy
from vectorbt_runner.backtest_summary import BacktestSummary
from vectorbt_runner.mtf_frames import SymbolMtfFrames


module_logger = logging.getLogger(__name__)
PROGRESS_LOG_EVERY = 50
DIAGNOSTIC_TOP_N = 5
ZERO_ENTRY_REJECTION_KEYS = (
    "retest_rejected_by_volume",
    "retest_rejected_by_extra_filters",
    "retest_confirmation_not_received",
    "retest_confirmation_expired",
)


class BacktestRunner:
    """Класс."""
    def __init__(
        self,
        results_dir: Path,
        results_file_name: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._results_dir = Path(results_dir)
        self._results_file_name = results_file_name
        self._logger = logger or module_logger

    # region Приватные

    @staticmethod
    def _build_metrics_row(params: BreakoutParams, trades: list[TradeResult]) -> dict[str, int | float | str | None]:
        base_row: dict[str, int | float | str | None] = {
            "lookback": params.lookback,
            "volume_mult": params.volume_mult,
            "retest_window_hours": params.retest_window_hours,
            "retest_zone": params.retest_zone,
            "min_rr": params.min_rr,
            "retest_zone_atr": params.retest_zone_atr,
            "sl_mode": params.sl_mode.value,
            "tp2_mult": params.tp2_mult,
            "min_body_ratio": params.min_body_ratio,
            "min_move_atr": params.min_move_atr,
            "max_retest_depth": params.max_retest_depth,
            "confirmation_bars": params.confirmation_bars,
            "entry_trigger": params.entry_trigger.value,
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
                "обнаружено несоответствие trades_count: вычисленное значение "
                f"({trades_count}) отличается от длины списка сделок ({len(trades)})."
            )
            raise RuntimeError(msg)

        sorted_trades = sorted(trades, key=lambda trade: (trade.exit_timestamp_ms, trade.entry_timestamp_ms))
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

    @staticmethod
    def _params_signature(params: BreakoutParams) -> str:
        return (
            f"lookback={params.lookback}|"
            f"volume_mult={params.volume_mult:.4f}|"
            f"retest_window_hours={params.retest_window_hours}|"
            f"entry_trigger={params.entry_trigger.value}|"
            f"confirmation_bars={params.confirmation_bars}"
        )

    @staticmethod
    def _extract_diagnostic_counter(diagnostics: dict[str, object]) -> Counter[str]:
        return Counter(
            {
                key: value
                for key, value in diagnostics.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
        )

    def _log_zero_entry_with_retests(
        self,
        diagnostics_by_key: dict[tuple[str, str], Counter[str]],
    ) -> None:
        if not diagnostics_by_key:
            return

        keys_with_retests = [
            (key, counter)
            for key, counter in diagnostics_by_key.items()
            if counter.get("retests_found", BACKTEST_ZERO_COUNT) > BACKTEST_ZERO_COUNT
        ]
        if not keys_with_retests:
            return

        problematic = [
            (key, counter)
            for key, counter in keys_with_retests
            if counter.get("trades_generated", BACKTEST_ZERO_COUNT) == BACKTEST_ZERO_COUNT
        ]
        problematic_count = len(problematic)
        keys_with_retests_count = len(keys_with_retests)
        problematic_share = problematic_count / keys_with_retests_count if keys_with_retests_count else BACKTEST_ZERO_COUNT

        total_retests = sum(counter.get("retests_found", BACKTEST_ZERO_COUNT) for _, counter in keys_with_retests)
        problematic_retests = sum(counter.get("retests_found", BACKTEST_ZERO_COUNT) for _, counter in problematic)
        problematic_retests_share = (
            problematic_retests / total_retests
            if total_retests
            else BACKTEST_ZERO_COUNT
        )

        breakdown = {
            name: sum(counter.get(name, BACKTEST_ZERO_COUNT) for _, counter in problematic)
            for name in ZERO_ENTRY_REJECTION_KEYS
        }
        breakdown_message = ", ".join(f"{name}={value}" for name, value in breakdown.items())

        self._logger.info(
            "запуск-бэктеста: нулевые_входы_при_наличии_ретестов ключей=%s/%s доля_ключей=%.4f ретестов=%s/%s доля_ретестов=%.4f %s",
            problematic_count,
            keys_with_retests_count,
            problematic_share,
            problematic_retests,
            total_retests,
            problematic_retests_share,
            breakdown_message,
        )

        if not problematic:
            return

        sorted_problematic = sorted(
            problematic,
            key=lambda item: (
                sum(item[1].get(name, BACKTEST_ZERO_COUNT) for name in ZERO_ENTRY_REJECTION_KEYS),
                item[1].get("retests_found", BACKTEST_ZERO_COUNT),
            ),
            reverse=True,
        )
        detail_limit = len(sorted_problematic) if self._logger.isEnabledFor(logging.DEBUG) else min(DIAGNOSTIC_TOP_N, len(sorted_problematic))
        for (symbol, params_signature), counter in sorted_problematic[:detail_limit]:
            self._logger.info(
                "запуск-бэктеста: проблемный_ключ symbol=%s params=%s retests_found=%s trades_generated=%s retest_rejected_by_volume=%s retest_rejected_by_extra_filters=%s retest_confirmation_not_received=%s retest_confirmation_expired=%s",
                symbol,
                params_signature,
                counter.get("retests_found", BACKTEST_ZERO_COUNT),
                counter.get("trades_generated", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_volume", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_extra_filters", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_not_received", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_expired", BACKTEST_ZERO_COUNT),
            )

        breakdown_by_params: dict[str, Counter[str]] = defaultdict(Counter)
        for (_, params_signature), counter in problematic:
            breakdown_by_params[params_signature].update(counter)

        sorted_by_params = sorted(
            breakdown_by_params.items(),
            key=lambda item: (
                sum(item[1].get(name, BACKTEST_ZERO_COUNT) for name in ZERO_ENTRY_REJECTION_KEYS),
                item[1].get("retests_found", BACKTEST_ZERO_COUNT),
            ),
            reverse=True,
        )
        params_detail_limit = min(DIAGNOSTIC_TOP_N, len(sorted_by_params))
        for params_signature, counter in sorted_by_params[:params_detail_limit]:
            self._logger.info(
                "запуск-бэктеста: проблемный_ключ_параметров params=%s retests_found=%s trades_generated=%s retest_rejected_by_volume=%s retest_rejected_by_extra_filters=%s retest_confirmation_not_received=%s retest_confirmation_expired=%s",
                params_signature,
                counter.get("retests_found", BACKTEST_ZERO_COUNT),
                counter.get("trades_generated", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_volume", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_extra_filters", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_not_received", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_expired", BACKTEST_ZERO_COUNT),
            )

    # endregion Приватные

    @staticmethod
    def build_parameter_grid() -> list[BreakoutParams]:
        """Собирает декартово произведение диапазонов параметров в полный набор конфигураций стратегии."""

        lookback = BREAKOUT_PARAMETER_GRID["lookback"]
        volume_mult = BREAKOUT_PARAMETER_GRID["volume_mult"]
        retest_window_hours = BREAKOUT_PARAMETER_GRID["retest_window_hours"]
        retest_zone = BREAKOUT_PARAMETER_GRID["retest_zone"]
        min_rr = BREAKOUT_PARAMETER_GRID["min_rr"]
        retest_zone_atr = BREAKOUT_PARAMETER_GRID["retest_zone_atr"]
        sl_mode = BREAKOUT_PARAMETER_GRID["sl_mode"]
        tp2_mult = BREAKOUT_PARAMETER_GRID["tp2_mult"]
        min_body_ratio = BREAKOUT_PARAMETER_GRID["min_body_ratio"]
        min_move_atr = BREAKOUT_PARAMETER_GRID["min_move_atr"]
        max_retest_depth = BREAKOUT_PARAMETER_GRID["max_retest_depth"]
        confirmation_bars = BREAKOUT_PARAMETER_GRID["confirmation_bars"]
        entry_trigger = BREAKOUT_PARAMETER_GRID["entry_trigger"]

        return [
            BreakoutParams(
                lookback=int(lb),
                volume_mult=float(vm),
                retest_window_hours=int(rw),
                retest_zone=float(rz),
                min_rr=float(rr),
                retest_zone_atr=float(rza),
                sl_mode=sl,
                tp2_mult=float(tp2),
                min_body_ratio=float(body_ratio),
                min_move_atr=float(min_move),
                max_retest_depth=float(max_depth),
                confirmation_bars=int(confirm_bars),
                entry_trigger=entry_trg,
                symbol="",
            )
            for lb, vm, rw, rz, rza, rr, sl, tp2, body_ratio, min_move, max_depth, confirm_bars, entry_trg in product(
                lookback,
                volume_mult,
                retest_window_hours,
                retest_zone,
                retest_zone_atr,
                min_rr,
                sl_mode,
                tp2_mult,
                min_body_ratio,
                min_move_atr,
                max_retest_depth,
                confirmation_bars,
                entry_trigger,
            )
        ]

    def run(
        self,
        strategy: "BaseStrategy[BreakoutParams]",
        symbol_frames: dict[str, SymbolMtfFrames],
        *,
        levels_timeframe: Timeframe = Timeframe.D1,
        entry_timeframe: Timeframe = Timeframe.M15,
    ) -> pd.DataFrame:
        """Запускает полный расчёт бэктеста в vectorbt."""
        rows: list[dict[str, int | float | str | None]] = []
        grid = self.build_parameter_grid()
        lookbacks = sorted({params.lookback for params in grid})
        total = len(grid)
        symbols_count = len(symbol_frames)
        started_at = perf_counter()

        prepared_symbol_data: dict[str, dict[int, pd.DataFrame]] = {}
        rejection_diagnostics_total: Counter[str] = Counter()
        rejection_diagnostics_by_key: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        if isinstance(strategy, BreakoutStrategy):
            strategy.set_logger(self._logger)
            for symbol, mtf_frames in symbol_frames.items():
                prepared_multi_tf = strategy.prepare_multi_tf_data(
                    mtf_frames=mtf_frames,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                )
                prepared_symbol_data[symbol] = {
                    lookback: strategy.prepare_annotated_multi_tf_data(
                        prepared_multi_tf=prepared_multi_tf,
                        lookback=lookback,
                    )
                    for lookback in lookbacks
                }

        for idx, params in enumerate(grid, start=1):
            all_trades: list[TradeResult] = []
            for symbol, mtf_frames in symbol_frames.items():
                cfg = BreakoutParams(
                    lookback=params.lookback,
                    volume_mult=params.volume_mult,
                    retest_window_hours=params.retest_window_hours,
                    retest_zone=params.retest_zone,
                    min_rr=params.min_rr,
                    retest_zone_atr=params.retest_zone_atr,
                    sl_mode=params.sl_mode,
                    tp2_mult=params.tp2_mult,
                    min_body_ratio=params.min_body_ratio,
                    min_move_atr=params.min_move_atr,
                    max_retest_depth=params.max_retest_depth,
                    confirmation_bars=params.confirmation_bars,
                    entry_trigger=params.entry_trigger,
                    symbol=symbol,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                )
                if isinstance(strategy, BreakoutStrategy):
                    prepared_annotated = prepared_symbol_data[symbol][params.lookback]
                    trades = strategy.generate_events_multi_tf(
                        mtf_frames=mtf_frames,
                        params=cfg,
                        annotated=prepared_annotated,
                    )
                    diagnostics_raw = strategy.consume_last_generation_diagnostics()
                    diagnostics_counter = self._extract_diagnostic_counter(diagnostics_raw)
                    rejection_diagnostics_total.update(diagnostics_counter)
                    context = diagnostics_raw.get("context")
                    context_symbol = symbol
                    if isinstance(context, dict) and isinstance(context.get("symbol"), str):
                        context_symbol = context["symbol"]
                    key = (context_symbol, self._params_signature(cfg))
                    rejection_diagnostics_by_key[key].update(diagnostics_counter)
                else:
                    trades = cast("BaseStrategy[BreakoutParams]", strategy).generate_events_multi_tf(
                        mtf_frames=mtf_frames,
                        params=cfg,
                    )
                all_trades.extend(trades)

            row = self._build_metrics_row(params, all_trades)
            rows.append(row)

            if idx % PROGRESS_LOG_EVERY == 0 or idx == total:
                elapsed_seconds = perf_counter() - started_at
                progress = (idx / total) * 100 if total else BACKTEST_ZERO_COUNT
                eta_seconds = (elapsed_seconds / idx) * (total - idx) if idx else BACKTEST_ZERO_COUNT
                self._logger.info(
                    "run-progress: grid=%s/%s (%.1f%%), symbols=%s, elapsed=%ss, eta=%ss",
                    idx,
                    total,
                    progress,
                    symbols_count,
                    int(elapsed_seconds),
                    int(eta_seconds),
                )

        results = (
            pd.DataFrame(rows)
            .sort_values("profit_factor", ascending=BACKTEST_SORT_ASCENDING)
            .reset_index(drop=True)
        )

        combinations_with_trades = int((results["trades_count"] > BACKTEST_ZERO_COUNT).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        combinations_without_trades = int((results["trades_count"] == BACKTEST_ZERO_COUNT).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        total_trades = int(results["trades_count"].sum()) if not results.empty else BACKTEST_ZERO_COUNT
        no_trades_share = (
            combinations_without_trades / len(results)
            if not results.empty
            else BACKTEST_ZERO_COUNT
        )
        self._logger.info(
            "запуск-бэктеста: покрытие сделками: со_сделками=%s без_сделок=%s всего_сделок=%s доля_без_сделок=%.4f",
            combinations_with_trades,
            combinations_without_trades,
            total_trades,
            no_trades_share,
        )
        if rejection_diagnostics_total:
            diagnostic_parts = [
                f"{name}={value}"
                for name, value in rejection_diagnostics_total.most_common()
                if value > BACKTEST_ZERO_COUNT
            ]
            self._logger.info(
                "запуск-бэктеста: диагностика_отброшенных_входов_итого %s",
                ", ".join(diagnostic_parts),
            )

        self._log_zero_entry_with_retests(dict(rejection_diagnostics_by_key))

        self._save_results(results)
        return results

    def build_summary(self, results: pd.DataFrame) -> BacktestSummary:
        """Собирает краткую сводку по результатам бэктеста."""
        if PARAMETER_GRID_SIZE != TARGET_PARAMETER_COMBINATIONS:
            self._logger.warning(
                "запуск-бэктеста: расчетная мощность сетки=%s отличается от целевой=%s (ожидается 5832)",
                PARAMETER_GRID_SIZE,
                TARGET_PARAMETER_COMBINATIONS,
            )
        if len(results) != TARGET_PARAMETER_COMBINATIONS:
            self._logger.warning(
                "запуск-бэктеста: фактическое число комбинаций=%s отличается от целевого=%s (ожидается 5832)",
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
