"""Запуск бэктеста по сетке параметров и расчет метрик."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING, Any, NamedTuple, cast

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
SYMBOL_PROGRESS_STEPS = 10
SYMBOL_PROGRESS_MAX_INTERVAL = 50
LONG_SYMBOL_LOG_SECONDS = 10.0


def _resolve_median_metric(values: list[int | float]) -> float | None:
    if not values:
        return None
    return round(float(median(values)), BACKTEST_ROUND_METRICS)


def _format_duration_human(seconds: float) -> str:
    """Преобразует длительность в компактный формат `00ч 00м 00с`."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}ч {minutes:02d}м {secs:02d}с"


def _format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--ч --м --с"
    return _format_duration_human(seconds)


def _build_progress_checkpoints(total: int) -> list[tuple[int, int]]:
    if total <= 0:
        return [(100, 0)]
    checkpoints: list[tuple[int, int]] = []
    progress_step = max(1, total // SYMBOL_PROGRESS_STEPS)
    checked = progress_step
    while checked < total:
        percent = min(99, max(1, int(round((checked / total) * 100))))
        checkpoints.append((percent, checked))
        checked += progress_step
    checkpoints.append((100, total))
    return checkpoints


def _format_progress_line(*, percent: int, checked: int, total: int, eta_seconds: float | None) -> str:
    return f"{percent}% Проверено: {checked} из {total}. ETA: {_format_eta(eta_seconds)}"


def _format_result_summary(*, levels_timeframe: Timeframe, entry_timeframe: Timeframe, row: dict[str, int | float | str | None]) -> str:
    header = f"Анализ {levels_timeframe.value}-{entry_timeframe.value} завершен"
    trades_count = int(row.get("trades_count") or 0)
    if trades_count <= 0:
        return f"{header}\nСделок нет"
    return (
        f"{header}\n"
        f"Сделок: {trades_count}\n"
        f"Винрейт: {float(row.get('win_rate') or 0.0):.2f}\n"
        f"Профит-фактор: {float(row.get('profit_factor') or 0.0):.2f}\n"
        f"Итог: {float(row.get('pnl_percent') or 0.0):.2f}%\n"
        f"Макс. просадка: {float(row.get('max_drawdown_pct') or 0.0):.2f}%\n"
        f"SL: {int(row.get('sl_count') or 0)}\n"
        f"TP1_BE: {int(row.get('tp1_be_count') or 0)}\n"
        f"TP2: {int(row.get('tp2_count') or 0)}"
    )


def _resolve_symbol_progress_interval(symbols_count: int) -> int:
    if symbols_count <= 0:
        return 1
    return max(1, min(SYMBOL_PROGRESS_MAX_INTERVAL, symbols_count // SYMBOL_PROGRESS_STEPS))


def _stage_metric_column_name(stage_id: str) -> str:
    return f"ppa_stage_hits_{stage_id}"


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
    def _resolve_initial_deposit(base_row: dict[str, int | float | str | None]) -> float | None:
        for key in ("bite_deposit", "ppa_deposit", "deposit"):
            raw_deposit = base_row.get(key)
            if raw_deposit is None:
                continue
            try:
                deposit = float(raw_deposit)
            except (TypeError, ValueError):
                continue
            if deposit > 0:
                return deposit
        return None

    @staticmethod
    def _build_trade_metadata_metrics(trades: list[TradeResult]) -> dict[str, int | float | str | None]:
        metadata_rows = [
            trade.metadata
            for trade in trades
            if isinstance(getattr(trade, "metadata", None), dict)
        ]
        if not metadata_rows:
            return {}

        metrics: dict[str, int | float | str | None] = {}
        setup_counts = Counter(
            str(metadata["setup_type"])
            for metadata in metadata_rows
            if metadata.get("setup_type") is not None
        )
        if setup_counts:
            metrics["ppa_setup_lsb_count"] = int(setup_counts.get("LSB", BACKTEST_ZERO_COUNT))
            metrics["ppa_setup_mbb_count"] = int(setup_counts.get("MBB", BACKTEST_ZERO_COUNT))

        runner_final_counts = Counter(
            str(metadata["runner_final_classification"])
            for metadata in metadata_rows
            if metadata.get("runner_final_classification") is not None
        )
        metrics["ppa_runner_success_above_tp1_count"] = int(runner_final_counts.get("success_above_tp1", BACKTEST_ZERO_COUNT))
        metrics["ppa_runner_be_below_tp1_count"] = int(runner_final_counts.get("be_below_tp1", BACKTEST_ZERO_COUNT))
        metrics["ppa_runner_loss_below_entry_count"] = int(runner_final_counts.get("loss_below_entry", BACKTEST_ZERO_COUNT))

        numeric_keys = (
            "entry_range_fraction",
            "aggression_ratio",
            "aggression_volume_mult",
            "aggression_ratio_threshold",
            "aggression_volume_threshold",
            "range_width_atr",
            "range_width_pump_fraction",
            "pump_height_atr",
            "stop_distance_atr",
            "stop_range_fraction",
            "oi_delta",
            "oi_delta_pct",
            "entry_minute_of_hour",
            "mfe_r",
            "mae_r",
            "holding_bars",
        )
        for key in numeric_keys:
            values = [
                float(value)
                for metadata in metadata_rows
                for value in [metadata.get(key)]
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            metric = _resolve_median_metric(values)
            if metric is not None:
                metrics[f"ppa_median_{key}"] = metric

        bool_count_keys = (
            "range_mid_hit",
            "range_high_hit",
            "tp1_hit",
            "tp2_hit",
            "oi_available",
            "oi_supportive",
            "entry_on_1m_boundary",
            "entry_on_5m_boundary",
            "entry_on_30m_boundary",
            "entry_on_60m_boundary",
        )
        for key in bool_count_keys:
            metrics[f"ppa_{key}_count"] = sum(1 for metadata in metadata_rows if bool(metadata.get(key)))

        return metrics

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
                "max_drawdown_pct": BACKTEST_EMPTY_MAX_DD,
                "median_pump_to_peak_bars": None,
                "median_pump_to_peak_minutes": None,
                "sl_count": BACKTEST_ZERO_COUNT,
                "be_count": BACKTEST_ZERO_COUNT,
                "time_exit_profit_count": BACKTEST_ZERO_COUNT,
                "tp1_be_count": BACKTEST_ZERO_COUNT,
                "tp2_count": BACKTEST_ZERO_COUNT,
                "ppa_runner_success_above_tp1_count": BACKTEST_ZERO_COUNT,
                "ppa_runner_be_below_tp1_count": BACKTEST_ZERO_COUNT,
                "ppa_runner_loss_below_entry_count": BACKTEST_ZERO_COUNT,
            }

        profits = BACKTEST_EMPTY_PNL_PERCENT
        losses = BACKTEST_EMPTY_PNL_PERCENT
        wins = BACKTEST_ZERO_COUNT
        pnl_percent = BACKTEST_EMPTY_PNL_PERCENT
        sl_count = BACKTEST_ZERO_COUNT
        be_count = BACKTEST_ZERO_COUNT
        time_exit_profit_count = BACKTEST_ZERO_COUNT
        tp1_be_count = BACKTEST_ZERO_COUNT
        tp2_count = BACKTEST_ZERO_COUNT

        normalized_trades: list[TradeResult] = trades
        for trade in normalized_trades:
            pnl_value = trade.pnl
            trade_pnl_percent = getattr(trade.pnl_percent, "value", trade.pnl_percent)
            pnl_percent += float(trade_pnl_percent)
            if pnl_value > 0:
                profits += pnl_value
                wins += 1
            elif pnl_value < 0:
                losses += abs(pnl_value)

            if trade.result_type == TradeResultType.SL:
                sl_count += 1
            elif trade.result_type == TradeResultType.BE:
                be_count += 1
            elif trade.result_type == TradeResultType.TIME_EXIT_PROFIT:
                time_exit_profit_count += 1
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
            key=lambda trade_row: (trade_row.exit_timestamp_ms, trade_row.entry_timestamp_ms),
        )
        cumulative_pnl = BACKTEST_EMPTY_PNL_PERCENT
        peak_pnl = BACKTEST_EMPTY_PNL_PERCENT
        max_dd = BACKTEST_EMPTY_MAX_DD
        initial_deposit = BacktestRunner._resolve_initial_deposit(base_row)
        peak_equity = initial_deposit
        max_drawdown_pct = BACKTEST_EMPTY_MAX_DD
        pump_to_peak_bars_values = [
            trade.pump_to_peak_bars for trade in normalized_trades if trade.pump_to_peak_bars is not None
        ]
        pump_to_peak_minutes_values = [
            trade.pump_to_peak_minutes for trade in normalized_trades if trade.pump_to_peak_minutes is not None
        ]
        for sorted_trade in sorted_trades:
            cumulative_pnl += sorted_trade.pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            drawdown = peak_pnl - cumulative_pnl
            if drawdown > max_dd:
                max_dd = drawdown
            if initial_deposit is not None:
                current_equity = initial_deposit + cumulative_pnl
                peak_equity = max(peak_equity or initial_deposit, current_equity)
                if peak_equity > 0:
                    drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100
                    if drawdown_pct > max_drawdown_pct:
                        max_drawdown_pct = drawdown_pct

        metadata_metrics = BacktestRunner._build_trade_metadata_metrics(normalized_trades)

        return {
            **base_row,
            "profit_factor": round(float(pf), BACKTEST_ROUND_METRICS),
            "pnl_percent": round(float(pnl_percent), BACKTEST_ROUND_METRICS),
            "win_rate": round(float(win_rate), BACKTEST_ROUND_METRICS),
            "trades_count": trades_count,
            "max_dd": round(float(max_dd), BACKTEST_ROUND_MAX_DD),
            "max_drawdown_pct": round(float(max_drawdown_pct), BACKTEST_ROUND_MAX_DD),
            "median_pump_to_peak_bars": _resolve_median_metric(pump_to_peak_bars_values),
            "median_pump_to_peak_minutes": _resolve_median_metric(pump_to_peak_minutes_values),
            "sl_count": sl_count,
            "be_count": be_count,
            "time_exit_profit_count": time_exit_profit_count,
            "tp1_be_count": tp1_be_count,
            "tp2_count": tp2_count,
            **metadata_metrics,
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
        dataclass_params = cast(Any, params)
        field_names = {field.name for field in fields(dataclass_params)}
        updates: dict[str, object] = {}
        if "symbol" in field_names:
            updates["symbol"] = symbol
        if "levels_timeframe" in field_names:
            updates["levels_timeframe"] = levels_timeframe
        if "entry_timeframe" in field_names:
            updates["entry_timeframe"] = entry_timeframe
        if not updates:
            return params
        return replace(dataclass_params, **updates)

    @staticmethod
    def _extract_diagnostic_counter(diagnostics: dict[str, object]) -> Counter[str]:
        return Counter(
            {
                key: value
                for key, value in diagnostics.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
        )

    def _raise_memory_error(
        self,
        *,
        exc: MemoryError,
        symbol: str | None,
        symbol_idx: int | None,
        symbols_count: int,
        combo_idx: int,
        total_combos: int,
    ) -> None:
        symbol_label = symbol or "портфельный расчёт"
        symbol_position = (
            f"{symbol_idx}/{symbols_count}"
            if symbol_idx is not None and symbols_count
            else "н/д"
        )
        message = (
            "Прогон остановлен: не хватило памяти.\n"
            f"  Последний участок: {symbol_label}.\n"
            f"  Символ: {symbol_position}.\n"
            f"  Комбинация: {combo_idx}/{total_combos}.\n"
            f"  Объём работы: {symbols_count} символов.\n"
            "  Что уменьшить: --top-n, --days или объём диагностики.\n"
            f"  Исходная ошибка: {exc}"
        )
        self._logger.error(message)
        raise MemoryError(
            "Не хватило памяти на расчёт. Уменьши --top-n, --days или объём диагностики."
        ) from exc

    def run(
        self,
        strategy: BaseStrategy[object],
        symbol_frames: dict[str, SymbolMtfFrames],
        *,
        levels_timeframe: Timeframe = Timeframe.D1,
        entry_timeframe: Timeframe = Timeframe.M15,
        stage_metric_ids: tuple[str, ...] | None = None,
        collect_diagnostics: bool | None = None,
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
        collect_diagnostics = self._logger.isEnabledFor(logging.DEBUG) if collect_diagnostics is None else bool(collect_diagnostics)
        collect_stage_metrics = bool(stage_metric_ids)
        tracked_stage_ids = tuple(stage_metric_ids or ())

        diagnostics_method = getattr(strategy, "consume_last_generation_diagnostics", None)

        self._logger.warning("Запуск бектеста")
        self._logger.info("Отобрано %s символов", symbols_count)

        for idx, prepared in enumerate(prepared_grid, start=1):
            all_trades: list[TradeResult] = []
            stage_metric_totals = {stage_id: 0 for stage_id in tracked_stage_ids}
            combo_started_at = perf_counter()
            self._logger.warning("Таймфреймы: %s-%s", levels_timeframe.value, entry_timeframe.value)
            self._logger.info("%s", _format_progress_line(percent=0, checked=0, total=symbols_count, eta_seconds=None))
            progress_checkpoints = _build_progress_checkpoints(symbols_count)
            progress_checkpoint_index = 0
            portfolio_trades: list[TradeResult] | None = None
            try:
                portfolio_trades = strategy.generate_events_portfolio(
                    symbol_frames=symbol_frames,
                    params=prepared.params,
                )
            except MemoryError as exc:
                self._raise_memory_error(
                    exc=exc,
                    symbol=None,
                    symbol_idx=None,
                    symbols_count=symbols_count,
                    combo_idx=idx,
                    total_combos=total,
                )

            if portfolio_trades is not None:
                all_trades.extend(portfolio_trades)
                if progress_checkpoints:
                    self._logger.info(
                        "%s",
                        _format_progress_line(
                            percent=100,
                            checked=symbols_count,
                            total=symbols_count,
                            eta_seconds=0.0,
                        ),
                    )
            elif strategy.__class__.__name__ == "BeeBiteStrategy":
                raise RuntimeError("BeeBiteStrategy должен использовать только portfolio pipeline.")
            else:
                for symbol_idx, (symbol, mtf_frames) in enumerate(symbol_frames.items(), start=1):
                    symbol_started_at = perf_counter()
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
                    trades: list[TradeResult] | None = None
                    try:
                        trades = strategy.generate_events_multi_tf(
                            mtf_frames=mtf_frames,
                            params=cfg,
                            **(
                                {
                                    "collect_diagnostics": collect_diagnostics,
                                    "collect_stage_metrics": collect_stage_metrics,
                                    **(context or {}),
                                }
                            ),
                        )
                    except MemoryError as exc:
                        self._raise_memory_error(
                            exc=exc,
                            symbol=symbol,
                            symbol_idx=symbol_idx,
                            symbols_count=symbols_count,
                            combo_idx=idx,
                            total_combos=total,
                        )
                    if trades is None:
                        trades = []
                    all_trades.extend(trades)
                    if (collect_diagnostics or collect_stage_metrics) and callable(diagnostics_method):
                        diagnostics_raw = diagnostics_method()
                        if isinstance(diagnostics_raw, dict):
                            if collect_stage_metrics:
                                stage_hits_raw = diagnostics_raw.get("stage_hits")
                                if isinstance(stage_hits_raw, dict):
                                    for stage_id in tracked_stage_ids:
                                        stage_metric_totals[stage_id] += int(stage_hits_raw.get(stage_id, 0) or 0)

                    while progress_checkpoint_index < len(progress_checkpoints) and symbol_idx >= progress_checkpoints[progress_checkpoint_index][1]:
                        progress_percent, checked_symbols = progress_checkpoints[progress_checkpoint_index]
                        combo_elapsed_seconds = perf_counter() - combo_started_at
                        combo_eta_seconds = (
                            (combo_elapsed_seconds / symbol_idx) * (symbols_count - symbol_idx)
                            if symbol_idx and symbols_count
                            else None
                        )
                        self._logger.info(
                            "%s",
                            _format_progress_line(
                                percent=progress_percent,
                                checked=checked_symbols,
                                total=symbols_count,
                                eta_seconds=combo_eta_seconds,
                            ),
                        )
                        progress_checkpoint_index += 1

            row_params = self._inject_runtime_fields(
                prepared.params,
                symbol="*",
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
            row = self._build_metrics_row(strategy.params_to_row(row_params), all_trades)
            if collect_stage_metrics:
                for stage_id in tracked_stage_ids:
                    row[_stage_metric_column_name(stage_id)] = int(stage_metric_totals.get(stage_id, 0))
            rows.append(row)
            self._logger.warning("%s", _format_result_summary(levels_timeframe=levels_timeframe, entry_timeframe=entry_timeframe, row=row))

        results = (
            pd.DataFrame(rows)
            .sort_values("profit_factor", ascending=BACKTEST_SORT_ASCENDING)
            .reset_index(drop=True)
        )

        self._save_results(results)
        return results

    @staticmethod
    def build_summary(results: pd.DataFrame) -> BacktestSummary:
        """Собирает краткую сводку по результатам бэктеста."""
        profitable = int((results["profit_factor"] > BACKTEST_PROFITABLE_PF_THRESHOLD).sum()) if not results.empty else BACKTEST_ZERO_COUNT
        best_pf = float(results["profit_factor"].max()) if not results.empty else BACKTEST_EMPTY_PF
        return BacktestSummary(
            total_combinations=int(len(results)),
            profitable_combinations=profitable,
            best_pf=best_pf,
        )
