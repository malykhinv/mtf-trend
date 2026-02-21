"""Запуск бэктеста по сетке параметров и расчет метрик."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, NamedTuple

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
from vectorbt_runner.backtest_summary import BacktestSummary
from vectorbt_runner.mtf_frames import SymbolMtfFrames

if TYPE_CHECKING:
    from strategy.base_strategy import BaseStrategy


module_logger = logging.getLogger(__name__)
PROGRESS_LOG_EVERY = 100
DIAGNOSTIC_TOP_N = 5
ZERO_ENTRY_REJECTION_KEYS = (
    "retest_rejected_by_volume",
    "retest_rejected_by_extra_filters",
    "retest_confirmation_not_received",
    "retest_confirmation_expired",
)


def _format_duration_human(seconds: float) -> str:
    """Преобразует длительность в человекоудобный формат `Hh Mm Ss`."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


class PreparedGridParams(NamedTuple):
    """Предвычисленная конфигурация сетки без symbol-specific полей."""

    params: object
    params_signature: str


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

    @staticmethod
    def _build_metrics_row(
        base_row: dict[str, int | float | str | None],
        trades: list[TradeResult],
    ) -> dict[str, int | float | str | None]:
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

        profits = BACKTEST_EMPTY_PNL_PERCENT
        losses = BACKTEST_EMPTY_PNL_PERCENT
        wins = BACKTEST_ZERO_COUNT
        pnl_percent = BACKTEST_EMPTY_PNL_PERCENT
        sl_count = BACKTEST_ZERO_COUNT
        be_count = BACKTEST_ZERO_COUNT
        tp1_be_count = BACKTEST_ZERO_COUNT
        tp2_count = BACKTEST_ZERO_COUNT

        normalized_trades: list[TradeResult] = trades
        for trade in normalized_trades:
            pnl_value = trade.pnl
            pnl_percent += float(trade.pnl_percent.value)
            if pnl_value > 0:
                profits += pnl_value
                wins += 1
            elif pnl_value < 0:
                losses += abs(pnl_value)

            if trade.result_type == TradeResultType.SL:
                sl_count += 1
            elif trade.result_type == TradeResultType.BE:
                be_count += 1
            elif trade.result_type == TradeResultType.TP1_BE:
                tp1_be_count += 1
            elif trade.result_type == TradeResultType.TP2:
                tp2_count += 1

        if losses > 0:
            pf = profits / losses
        elif profits > 0:
            pf = BACKTEST_PF_FALLBACK_WHEN_NO_LOSSES
        else:
            pf = BACKTEST_EMPTY_PF

        win_rate = wins / len(normalized_trades)
        trades_count = len(normalized_trades)

        sorted_trades = sorted(
            normalized_trades,
            key=lambda trade: (trade.exit_timestamp_ms, trade.entry_timestamp_ms),
        )
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

        return {
            **base_row,
            "profit_factor": round(float(pf), BACKTEST_ROUND_METRICS),
            "pnl_percent": round(float(pnl_percent), BACKTEST_ROUND_METRICS),
            "win_rate": round(float(win_rate), BACKTEST_ROUND_METRICS),
            "trades_count": trades_count,
            "max_dd": round(float(max_dd), BACKTEST_ROUND_MAX_DD),
            "sl_count": sl_count,
            "be_count": be_count,
            "tp1_be_count": tp1_be_count,
            "tp2_count": tp2_count,
        }

    def _save_results(self, results: pd.DataFrame) -> None:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        results.to_csv(self._results_dir / self._results_file_name, index=False)

    @staticmethod
    def _params_signature(
        strategy: BaseStrategy[object],
        params: object,
    ) -> str:
        params_row = strategy.params_to_row(params)
        return "|".join(f"{key}={params_row[key]}" for key in sorted(params_row.keys()))

    @staticmethod
    def _inject_runtime_fields(
        params: object,
        *,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> object:
        if not is_dataclass(params):
            return params
        field_names = {field.name for field in fields(params)}
        updates: dict[str, object] = {}
        if "symbol" in field_names:
            updates["symbol"] = symbol
        if "levels_timeframe" in field_names:
            updates["levels_timeframe"] = levels_timeframe
        if "entry_timeframe" in field_names:
            updates["entry_timeframe"] = entry_timeframe
        if not updates:
            return params
        return replace(params, **updates)

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

        self._logger.debug(
            "запуск-бэктеста: нулевые_входы_при_наличии_ретестов ключей=(symbol+полная_конфигурация_сетки)=%s/%s доля_ключей=%.4f ретестов=%s/%s доля_ретестов=%.4f %s",
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
            self._logger.debug(
                "запуск-бэктеста: проблемный_ключ symbol=%s grid_params=%s retests_found=%s trades_generated=%s retest_rejected_by_volume=%s retest_rejected_by_extra_filters=%s retest_confirmation_not_received=%s retest_confirmation_expired=%s",
                symbol,
                params_signature,
                counter.get("retests_found", BACKTEST_ZERO_COUNT),
                counter.get("trades_generated", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_volume", BACKTEST_ZERO_COUNT),
                counter.get("retest_rejected_by_extra_filters", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_not_received", BACKTEST_ZERO_COUNT),
                counter.get("retest_confirmation_expired", BACKTEST_ZERO_COUNT),
            )

    def run(
        self,
        strategy: BaseStrategy[object],
        symbol_frames: dict[str, SymbolMtfFrames],
        *,
        levels_timeframe: Timeframe = Timeframe.D1,
        entry_timeframe: Timeframe = Timeframe.M15,
    ) -> pd.DataFrame:
        """Запускает полный расчёт бэктеста в vectorbt."""
        rows: list[dict[str, int | float | str | None]] = []
        grid = strategy.build_parameter_grid()
        prepared_grid = [
            PreparedGridParams(
                params=params,
                params_signature=self._params_signature(strategy, params),
            )
            for params in grid
        ]
        for prepared in prepared_grid:
            strategy.validate_config(prepared.params)

        total = len(prepared_grid)
        symbols_count = len(symbol_frames)
        started_at = perf_counter()
        collect_diagnostics = self._logger.isEnabledFor(logging.DEBUG)

        rejection_diagnostics_total: Counter[str] = Counter()
        rejection_diagnostics_by_key: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        diagnostics_method = getattr(strategy, "consume_last_generation_diagnostics", None)

        for idx, prepared in enumerate(prepared_grid, start=1):
            all_trades: list[TradeResult] = []
            portfolio_trades = strategy.generate_events_portfolio(
                symbol_frames=symbol_frames,
                params=prepared.params,
            )
            if portfolio_trades is not None:
                all_trades.extend(portfolio_trades)
            elif strategy.__class__.__name__ == "BeeBiteStrategy":
                raise RuntimeError("BeeBiteStrategy должен использовать только portfolio pipeline.")
            else:
                for symbol, mtf_frames in symbol_frames.items():
                    cfg = self._inject_runtime_fields(
                        prepared.params,
                        symbol=symbol,
                        levels_timeframe=levels_timeframe,
                        entry_timeframe=entry_timeframe,
                    )
                    context = strategy.prepare_symbol_context(
                        symbol=symbol,
                        mtf_frames=mtf_frames,
                        params=cfg,
                    )
                    trades = strategy.generate_events_multi_tf(
                        mtf_frames=mtf_frames,
                        params=cfg,
                        **(context or {}),
                    )
                    all_trades.extend(trades)

                    if collect_diagnostics and callable(diagnostics_method):
                        diagnostics_raw = diagnostics_method()
                        if isinstance(diagnostics_raw, dict):
                            diagnostics_counter = self._extract_diagnostic_counter(diagnostics_raw)
                            rejection_diagnostics_total.update(diagnostics_counter)
                            raw_context = diagnostics_raw.get("context")
                            context_symbol = symbol
                            if isinstance(raw_context, dict) and isinstance(raw_context.get("symbol"), str):
                                context_symbol = raw_context["symbol"]
                            key = (context_symbol, prepared.params_signature)
                            rejection_diagnostics_by_key[key].update(diagnostics_counter)

            row = self._build_metrics_row(strategy.params_to_row(prepared.params), all_trades)
            rows.append(row)

            if idx % PROGRESS_LOG_EVERY == 0 or idx == total:
                elapsed_seconds = perf_counter() - started_at
                progress = (idx / total) * 100 if total else BACKTEST_ZERO_COUNT
                eta_seconds = (elapsed_seconds / idx) * (total - idx) if idx else BACKTEST_ZERO_COUNT
                self._logger.info(
                    "run-progress: grid=%s/%s (%.1f%%), symbols=%s, elapsed=%s, eta=%s",
                    idx,
                    total,
                    progress,
                    symbols_count,
                    _format_duration_human(elapsed_seconds),
                    _format_duration_human(eta_seconds),
                )

        results = (
            pd.DataFrame(rows)
            .sort_values("profit_factor", ascending=BACKTEST_SORT_ASCENDING)
            .reset_index(drop=True)
        )

        combinations_with_trades = int((results["trades_count"] > BACKTEST_ZERO_COUNT).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        combinations_without_trades = int((results["trades_count"] == BACKTEST_ZERO_COUNT).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        total_combinations = int(len(results)) if not results.empty else BACKTEST_ZERO_COUNT
        total_trades = int(results["trades_count"].sum()) if not results.empty else BACKTEST_ZERO_COUNT
        average_trades_per_combination = (
            total_trades / total_combinations
            if total_combinations
            else float(BACKTEST_ZERO_COUNT)
        )
        median_trades_per_combination = (
            float(results["trades_count"].median())
            if not results.empty
            else float(BACKTEST_ZERO_COUNT)
        )
        no_trades_share = (
            combinations_without_trades / len(results)
            if not results.empty
            else BACKTEST_ZERO_COUNT
        )
        self._logger.info(
            "запуск-бэктеста: покрытие сделками: со_сделками=%s без_сделок=%s сумма_сделок_по_сетке=%s среднее_сделок_на_комбинацию=%.4f медиана_сделок_на_комбинацию=%.4f доля_без_сделок=%.4f",
            combinations_with_trades,
            combinations_without_trades,
            total_trades,
            average_trades_per_combination,
            median_trades_per_combination,
            no_trades_share,
        )
        if collect_diagnostics and rejection_diagnostics_total:
            diagnostic_parts = [
                f"{name}={value}"
                for name, value in rejection_diagnostics_total.most_common()
                if value > BACKTEST_ZERO_COUNT
            ]
            self._logger.info(
                "запуск-бэктеста: диагностика_отброшенных_входов_итого %s",
                ", ".join(diagnostic_parts),
            )

        if collect_diagnostics:
            self._log_zero_entry_with_retests(dict(rejection_diagnostics_by_key))

        self._save_results(results)
        return results

    def build_summary(self, results: pd.DataFrame) -> BacktestSummary:
        """Собирает краткую сводку по результатам бэктеста."""
        profitable = int((results["profit_factor"] > BACKTEST_PROFITABLE_PF_THRESHOLD).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        best_pf = float(results["profit_factor"].max()) if not results.empty else BACKTEST_EMPTY_PF
        return BacktestSummary(
            total_combinations=int(len(results)),
            profitable_combinations=profitable,
            best_pf=best_pf,
        )
