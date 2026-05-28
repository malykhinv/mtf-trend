"""HTF/LTF runner discovery for pump-awakening research.

The tool separates two jobs that are easy to mix up:

* future labels: did an HTF anomaly become a +10% runner within the next hour,
  and was the anomaly low broken before that happened;
* executable replay: would a live bot, using only closed LTF candles available
  at the decision time, enter and manage the trade with structural stop/trailing
  and no take-profit.

Artifacts are research-only. They are meant to learn runner nature, not to
declare a live-ready edge from one run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from constants import DEFAULT_CACHE_DIR, DEFAULT_RESULTS_DIR
from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class HtfLtfRunnerDiscoveryConfig:
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "htf_ltf_runner_discovery"
    htf_timeframe: str = "1m"
    ltf_timeframe: str = "5s"
    days: int = 30
    end_timestamp_ms: int | None = None
    baseline_candles: int = 60
    dormancy_candles: int = 30
    pregrowth_candles: int = 5
    min_htf_quote_ratio: float = 5.0
    min_htf_trade_ratio: float = 5.0
    min_htf_return_pct: float = 0.010
    min_dormancy_to_anomaly_quote_ratio: float = 6.0
    min_dormancy_to_anomaly_trade_ratio: float = 5.0
    max_dormancy_range_pct_median: float = 0.004
    min_pregrowth_return_pct: float = 0.002
    max_pregrowth_single_candle_return_pct: float = 0.020
    min_pregrowth_positive_step_share: float = 0.55
    min_pregrowth_oi_change_pct: float = 0.0
    require_pregrowth_oi: bool = False
    runner_horizon_minutes: int = 60
    runner_target_return_pct: float = 0.10
    ltf_min_confirm_candles: int = 6
    ltf_max_confirm_candles: int = 24
    min_ltf_confirm_return_pct: float = 0.004
    min_ltf_quote_pace_ratio: float = 3.0
    min_ltf_trade_pace_ratio: float = 3.0
    min_ltf_taker_buy_share: float | None = None
    min_ltf_second_half_return_pct: float = 0.0
    min_ltf_quote_acceleration: float = 1.0
    min_ltf_trade_acceleration: float = 1.0
    max_entry_drift_pct: float = 0.004
    max_initial_risk_pct: float = 0.05
    structural_stop_buffer_pct: float = 0.0005
    trail_lookback_candles: int = 6
    trail_buffer_pct: float = 0.0005
    max_hold_candles: int = 720
    fee_rate: float = 0.0004
    entry_slippage_pct: float = 0.0005
    exit_slippage_pct: float = 0.0005
    symbol_workers: int = 1
    auto_targeted_ltf_backfill: bool = True
    targeted_backfill_min_htf_quote_ratio: float = 12.0
    targeted_backfill_min_htf_trade_ratio: float = 12.0
    targeted_backfill_min_htf_return_pct: float = 0.0227
    targeted_backfill_min_htf_range_pct: float = 0.030
    targeted_backfill_max_events_per_symbol: int = 20

    def __post_init__(self) -> None:
        if self.htf_timeframe == self.ltf_timeframe:
            raise ValueError("htf_timeframe and ltf_timeframe must differ")
        if _timeframe_ms(self.ltf_timeframe) >= _timeframe_ms(self.htf_timeframe):
            raise ValueError("ltf_timeframe must be lower than htf_timeframe")
        for name in (
            "days",
            "baseline_candles",
            "dormancy_candles",
            "pregrowth_candles",
            "runner_horizon_minutes",
            "ltf_min_confirm_candles",
            "ltf_max_confirm_candles",
            "trail_lookback_candles",
            "max_hold_candles",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.ltf_max_confirm_candles < self.ltf_min_confirm_candles:
            raise ValueError("ltf_max_confirm_candles must be >= ltf_min_confirm_candles")
        if self.targeted_backfill_max_events_per_symbol < 0:
            raise ValueError("targeted_backfill_max_events_per_symbol must be >= 0")



@dataclass(frozen=True, slots=True)
class _StrictLtfWindow:
    frame: pd.DataFrame
    status: str
    start_ms: int
    end_exclusive_ms: int
    expected_step_ms: int
    expected_candles: int
    observed_candles: int
    first_gap_start_ms: int | None
    first_gap_end_ms: int | None
    max_gap_ms: int


class _ProgressLine:
    def __init__(self, *, label: str, total: int, min_interval_seconds: float = 0.5) -> None:
        self.label = label
        self.total = max(0, int(total))
        self.min_interval_seconds = float(min_interval_seconds)
        self.started_at = time.monotonic()
        self.last_emit_at = 0.0
        self.last_len = 0
        self.enabled = bool(getattr(sys.stderr, "isatty", lambda: False)())
        self.next_progress_pct = 0

    def update(self, *, index: int, item: str) -> None:
        if self.total <= 0:
            return
        current_pct = min(100, max(0, int((100.0 * int(index) / self.total) + 0.5)))
        if index < self.total and current_pct < self.next_progress_pct:
            return
        now = time.monotonic()
        if index < self.total and now - self.last_emit_at < self.min_interval_seconds and current_pct <= 0:
            return
        elapsed = max(0.001, now - self.started_at)
        eta_seconds = (elapsed / max(1, index)) * max(0, self.total - index)
        text = (
            f"{self.label}: scanning {index}/{self.total} "
            f"({current_pct:3d}%) symbol={item} eta={_format_duration(eta_seconds)}"
        )
        if self.enabled:
            padding = " " * max(0, self.last_len - len(text))
            print(f"\r{text}{padding}", end="", file=sys.stderr, flush=True)
            self.last_len = len(text)
        else:
            if index == 1 or index == self.total:
                print(text, flush=True)
        self.last_emit_at = now
        self.next_progress_pct = current_pct + 1

    def finish(self) -> None:
        if self.enabled and self.last_len:
            print(file=sys.stderr, flush=True)


def run_htf_ltf_runner_discovery(
    config: HtfLtfRunnerDiscoveryConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
) -> Path:
    started_at = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    selected_symbols = tuple(_resolve_symbols(config.cache_dir, config.htf_timeframe, symbols))
    storage = ParquetStorage(config.cache_dir)
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    end_ms = _resolve_end_timestamp_ms(config, storage, selected_symbols)
    start_ms = int(end_ms) - int(config.days) * 24 * 60 * 60 * 1000

    targeted_ltf_plan = pd.DataFrame()
    targeted_ltf_fetch = pd.DataFrame()
    targeted_ltf_materialize = pd.DataFrame()
    if _targeted_ltf_backfill_required(config):
        targeted_phase_frames: list[tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]] = []
        pre_entry_plan, pre_entry_windows_by_symbol = _build_targeted_ltf_backfill_plan(
            storage=storage,
            symbols=selected_symbols,
            start_ms=start_ms,
            end_ms=end_ms,
            config=config,
            progress_label=f"{progress_label or 'runner discovery'} targeted pre-entry plan",
        )
        pre_entry_fetch, pre_entry_materialize = _ensure_targeted_ltf_backfill(
            cache_dir=config.cache_dir,
            ltf_timeframe=config.ltf_timeframe,
            windows_by_symbol=pre_entry_windows_by_symbol,
            progress_label=f"{progress_label or 'runner discovery'} targeted pre-entry LTF",
        )
        targeted_phase_frames.append(("pre_entry", pre_entry_plan, pre_entry_fetch, pre_entry_materialize))

        post_entry_plan, post_entry_windows_by_symbol = _build_targeted_ltf_post_entry_backfill_plan(
            storage=storage,
            symbols=selected_symbols,
            start_ms=start_ms,
            end_ms=end_ms,
            config=config,
            progress_label=f"{progress_label or 'runner discovery'} targeted post-entry plan",
        )
        post_entry_fetch, post_entry_materialize = _ensure_targeted_ltf_backfill(
            cache_dir=config.cache_dir,
            ltf_timeframe=config.ltf_timeframe,
            windows_by_symbol=post_entry_windows_by_symbol,
            progress_label=f"{progress_label or 'runner discovery'} targeted post-entry LTF",
        )
        targeted_phase_frames.append(("post_entry", post_entry_plan, post_entry_fetch, post_entry_materialize))
        targeted_ltf_plan = _concat_targeted_phase_frames(targeted_phase_frames, frame_index=1)
        targeted_ltf_fetch = _concat_targeted_phase_frames(targeted_phase_frames, frame_index=2)
        targeted_ltf_materialize = _concat_targeted_phase_frames(targeted_phase_frames, frame_index=3)

    candidate_rows: list[dict[str, object]] = []
    entry_window_rows: list[dict[str, object]] = []
    entry_window_trade_rows: list[dict[str, object]] = []
    signal_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    quality_rows: list[dict[str, object]] = []
    progress = _ProgressLine(
        label=progress_label or f"runner discovery {config.htf_timeframe}/{config.ltf_timeframe}",
        total=len(selected_symbols),
    )

    def _process_symbol(symbol: str) -> dict[str, object]:
        local_storage = ParquetStorage(config.cache_dir)
        htf = _load_frame(local_storage, symbol, config.htf_timeframe, start_ms=start_ms, end_ms=end_ms)
        ltf = _load_frame(
            local_storage,
            symbol,
            config.ltf_timeframe,
            start_ms=start_ms - htf_ms * max(config.baseline_candles, config.dormancy_candles),
            end_ms=end_ms + config.runner_horizon_minutes * 60_000,
        )
        oi = _load_oi_frame(local_storage, symbol, config=config, start_ms=start_ms, end_ms=end_ms)
        local_quality_rows = [_data_quality_row(symbol=symbol, htf=htf, ltf=ltf, oi=oi, config=config)]
        local_candidate_rows: list[dict[str, object]] = []
        local_entry_window_rows: list[dict[str, object]] = []
        local_entry_window_trade_rows: list[dict[str, object]] = []
        local_signal_rows: list[dict[str, object]] = []
        local_trade_rows: list[dict[str, object]] = []
        scanned_rows = 0
        if not htf.empty and not ltf.empty:
            local_candidate_rows, scanned_rows = _collect_symbol_candidates(
                symbol=symbol,
                htf=htf,
                ltf=ltf,
                oi=oi,
                config=config,
            )
            for candidate in local_candidate_rows:
                windows = _build_ltf_entry_windows(candidate, ltf=ltf, oi=oi, config=config)
                local_entry_window_rows.extend(windows)
                for window in windows:
                    if window.get("window_execution_ok") is True:
                        local_entry_window_trade_rows.append(_simulate_no_tp_runner_trade(window, ltf=ltf, config=config))
                signal = _build_first_ltf_signal(candidate, ltf=ltf, oi=oi, config=config)
                if signal is None:
                    continue
                local_signal_rows.append(signal)
                local_trade_rows.append(_simulate_no_tp_runner_trade(signal, ltf=ltf, config=config))
        return {
            "candidates": local_candidate_rows,
            "entry_windows": local_entry_window_rows,
            "entry_window_trades": local_entry_window_trade_rows,
            "signals": local_signal_rows,
            "trades": local_trade_rows,
            "quality": local_quality_rows,
            "scanned_rows": scanned_rows,
        }

    symbol_results: dict[str, dict[str, object]] = {}
    workers = _effective_symbol_workers(config.symbol_workers, total_items=len(selected_symbols))
    if workers <= 1:
        for index, symbol in enumerate(selected_symbols, start=1):
            progress.update(index=index, item=symbol)
            symbol_results[symbol] = _process_symbol(symbol)
    else:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="runner-discovery")
        futures = {executor.submit(_process_symbol, symbol): symbol for symbol in selected_symbols}
        try:
            for index, future in enumerate(as_completed(futures), start=1):
                symbol = futures[future]
                progress.update(index=index, item=symbol)
                symbol_results[symbol] = future.result()
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            progress.finish()
            raise
        else:
            executor.shutdown(wait=True)
    progress.finish()

    for symbol in selected_symbols:
        result = symbol_results.get(symbol, {})
        candidate_rows.extend(result.get("candidates", []))  # type: ignore[arg-type]
        entry_window_rows.extend(result.get("entry_windows", []))  # type: ignore[arg-type]
        entry_window_trade_rows.extend(result.get("entry_window_trades", []))  # type: ignore[arg-type]
        signal_rows.extend(result.get("signals", []))  # type: ignore[arg-type]
        trade_rows.extend(result.get("trades", []))  # type: ignore[arg-type]
        quality_rows.extend(result.get("quality", []))  # type: ignore[arg-type]
    scanned_htf_rows = int(sum(int(result.get("scanned_rows", 0)) for result in symbol_results.values()))

    candidates_frame = pd.DataFrame(candidate_rows)
    entry_windows_frame = pd.DataFrame(entry_window_rows)
    entry_window_trades_frame = pd.DataFrame(entry_window_trade_rows)
    entry_window_trades_live_filtered = _apply_same_symbol_overlap_filter(entry_window_trades_frame)
    signals_frame = pd.DataFrame(signal_rows)
    trades_frame = pd.DataFrame(trade_rows)
    live_filtered = _apply_same_symbol_overlap_filter(trades_frame)
    candidate_rule_scores = _score_candidate_rules(candidates_frame)
    entry_window_rule_scores = _score_entry_window_rules(
        entry_window_trades_frame,
        scope="raw_no_overlap_unfiltered",
        apply_same_symbol_filter=False,
    )
    entry_window_rule_scores_live_filtered = _score_entry_window_rules(
        entry_window_trades_frame,
        scope="per_rule_same_symbol",
        apply_same_symbol_filter=True,
    )
    trade_rule_scores = _score_trade_rules(trades_frame, scope="raw")
    live_trade_rule_scores = _score_trade_rules(live_filtered, scope="live_filtered")
    research_shortlist = _research_shortlist(
        candidate_rule_scores,
        live_trade_rule_scores,
        entry_window_rule_scores_live_filtered=entry_window_rule_scores_live_filtered,
    )

    artifacts = [
        (config.output_dir / "htf_ltf_runner_candidates.csv", candidates_frame),
        (config.output_dir / "htf_ltf_runner_entry_windows.csv", entry_windows_frame),
        (config.output_dir / "htf_ltf_runner_entry_window_trades_raw.csv", entry_window_trades_frame),
        (config.output_dir / "htf_ltf_runner_entry_window_trades_live_filtered.csv", entry_window_trades_live_filtered),
        (config.output_dir / "htf_ltf_runner_entry_window_by_day.csv", _daily_summary(entry_window_trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_entry_window_by_day_live_filtered.csv",
            _daily_summary(entry_window_trades_live_filtered),
        ),
        (config.output_dir / "htf_ltf_runner_entry_window_rule_scores.csv", entry_window_rule_scores),
        (
            config.output_dir / "htf_ltf_runner_entry_window_rule_scores_live_filtered.csv",
            entry_window_rule_scores_live_filtered,
        ),
        (config.output_dir / "htf_ltf_runner_signals.csv", signals_frame),
        (config.output_dir / "htf_ltf_runner_trades_raw.csv", trades_frame),
        (config.output_dir / "htf_ltf_runner_trades_live_filtered.csv", live_filtered),
        (config.output_dir / "htf_ltf_runner_by_day.csv", _daily_summary(trades_frame)),
        (config.output_dir / "htf_ltf_runner_by_day_live_filtered.csv", _daily_summary(live_filtered)),
        (config.output_dir / "htf_ltf_runner_profitability_summary.csv", _summarize_trades(trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_profitability_summary_live_filtered.csv",
            _summarize_trades(live_filtered),
        ),
        (config.output_dir / "htf_ltf_runner_by_setup_nature.csv", _summarize_by_column(trades_frame, "setup_nature")),
        (
            config.output_dir / "htf_ltf_runner_by_setup_nature_live_filtered.csv",
            _summarize_by_column(live_filtered, "setup_nature"),
        ),
        (config.output_dir / "htf_ltf_runner_label_distribution.csv", _label_distribution(candidates_frame)),
        (config.output_dir / "htf_ltf_runner_candidate_rule_scores.csv", candidate_rule_scores),
        (config.output_dir / "htf_ltf_runner_trade_rule_scores.csv", trade_rule_scores),
        (config.output_dir / "htf_ltf_runner_trade_rule_scores_live_filtered.csv", live_trade_rule_scores),
        (config.output_dir / "htf_ltf_runner_research_shortlist.csv", research_shortlist),
        (
            config.output_dir / "htf_ltf_runner_funnel.csv",
            _build_funnel(
                candidates_frame,
                signals_frame,
                trades_frame,
                scanned_htf_rows=scanned_htf_rows,
                entry_windows=entry_windows_frame,
                entry_window_trades=entry_window_trades_frame,
            ),
        ),
        (config.output_dir / "htf_ltf_runner_skip_reasons.csv", _skip_reasons(trades_frame)),
        (config.output_dir / "htf_ltf_runner_top_dependency.csv", _top_dependency(trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_top_dependency_live_filtered.csv",
            _top_dependency(live_filtered),
        ),
        (config.output_dir / "htf_ltf_runner_data_quality_summary.csv", pd.DataFrame(quality_rows)),
        (config.output_dir / "htf_ltf_runner_targeted_ltf_plan.csv", targeted_ltf_plan),
        (config.output_dir / "htf_ltf_runner_targeted_ltf_fetch.csv", targeted_ltf_fetch),
        (config.output_dir / "htf_ltf_runner_targeted_ltf_materialize.csv", targeted_ltf_materialize),
        (config.output_dir / "htf_ltf_runner_honesty_report.csv", _honesty_report(config)),
        (
            config.output_dir / "run_config.csv",
            pd.DataFrame(
                [
                    {
                        **asdict(config),
                        "cache_dir": str(config.cache_dir),
                        "output_dir": str(config.output_dir),
                        "symbols": len(selected_symbols),
                        "start_timestamp_ms": int(start_ms),
                        "start_timestamp_utc": _timestamp_to_utc(start_ms),
                        "end_timestamp_ms": int(end_ms),
                        "end_timestamp_utc": _timestamp_to_utc(end_ms),
                        "execution_model": "next_ltf_open_after_closed_ltf_confirmation",
                        "exit_model": "structural_stop_plus_structural_trailing_no_tp",
                        "future_label_model": "separate_next_hour_10pct_label_not_used_for_entry",
                        "data_access_model": (
                            "two_stage_targeted_aggtrade_1s_backfill_then_strict_ltf_replay"
                            if _targeted_ltf_backfill_required(config)
                            else "cache_only_no_exchange_fetch"
                        ),
                        "seconds_download_required": bool(_targeted_ltf_backfill_required(config)),
                        "targeted_ltf_plan_rows": int(len(targeted_ltf_plan)),
                        "targeted_ltf_fetch_rows": int(len(targeted_ltf_fetch)),
                        "targeted_ltf_materialize_rows": int(len(targeted_ltf_materialize)),
                        "runtime_seconds": round(time.monotonic() - started_at, 3),
                        "scanned_htf_rows": scanned_htf_rows,
                        "candidate_artifact_scope": "htf_anomaly_gate_only",
                        "entry_window_counts": ",".join(str(value) for value in _entry_window_counts(config)),
                        "entry_window_model": "fixed_closed_ltf_windows_next_open_research",
                        "post_entry_simulation_model": "strict_wall_clock_ltf_path_no_gap_hops",
                    }
                ]
            ),
        ),
    ]
    for path, frame in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, encoding="utf-8-sig")

    summary = _metric_map(_summarize_trades(live_filtered))
    print(
        f"{progress.label}: done "
        f"candidates={len(candidates_frame)} signals={len(signals_frame)} "
        f"closed={int(summary.get('closed_trades', 0))} "
        f"avg_net={float(summary.get('avg_net_return', 0.0)):.4%} "
        f"win_rate={float(summary.get('win_rate', 0.0)):.2%}",
        flush=True,
    )
    return config.output_dir



def _targeted_ltf_backfill_required(config: HtfLtfRunnerDiscoveryConfig) -> bool:
    if not bool(config.auto_targeted_ltf_backfill):
        return False
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    return 0 < ltf_ms < 60_000


def _build_targeted_ltf_backfill_plan(
    *,
    storage: ParquetStorage,
    symbols: Iterable[str],
    start_ms: int,
    end_ms: int,
    config: HtfLtfRunnerDiscoveryConfig,
    progress_label: str,
) -> tuple[pd.DataFrame, dict[str, list[tuple[int, int]]]]:
    rows: list[dict[str, object]] = []
    windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    selected_symbols = tuple(symbols)
    progress = _ProgressLine(label=progress_label, total=len(selected_symbols))
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    for index, symbol in enumerate(selected_symbols, start=1):
        progress.update(index=index, item=symbol)
        htf = _load_frame(storage, symbol, config.htf_timeframe, start_ms=start_ms, end_ms=end_ms)
        symbol_rows, windows = _targeted_ltf_backfill_seeds_for_symbol(symbol=symbol, htf=htf, config=config, htf_ms=htf_ms, ltf_ms=ltf_ms)
        rows.extend(symbol_rows)
        if windows:
            windows_by_symbol[symbol] = windows
    progress.finish()
    plan = pd.DataFrame(rows)
    summary = {
        "symbol": "__all__",
        "targeted_ltf_plan_status": "summary",
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "symbols": len(selected_symbols),
        "candidate_seed_rows": int(len(plan.loc[plan.get("targeted_ltf_plan_status", pd.Series(dtype=str)).astype(str).eq("planned")])) if not plan.empty else 0,
        "symbols_with_windows": int(len(windows_by_symbol)),
        "raw_targeted_windows": int(sum(len(windows) for windows in windows_by_symbol.values())),
        "targeted_ltf_phase": "pre_entry",
        "window_model": "strict_htf_anomaly_start_to_max_entry_next_open_only",
        "selection_model": "closed_htf_seed_gate_only",
        "seed_gate": _targeted_ltf_seed_gate_description(config),
        "min_htf_quote_ratio": float(config.targeted_backfill_min_htf_quote_ratio),
        "min_htf_trade_ratio": float(config.targeted_backfill_min_htf_trade_ratio),
        "min_htf_return_pct": float(config.targeted_backfill_min_htf_return_pct),
        "min_htf_range_pct": float(config.targeted_backfill_min_htf_range_pct),
        "max_events_per_symbol": int(config.targeted_backfill_max_events_per_symbol),
    }
    if plan.empty:
        plan = pd.DataFrame([summary])
    else:
        plan = pd.concat([pd.DataFrame([summary]), plan], ignore_index=True, sort=False)
    return plan, windows_by_symbol


def _targeted_ltf_backfill_seeds_for_symbol(
    *,
    symbol: str,
    htf: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
    htf_ms: int,
    ltf_ms: int,
) -> tuple[list[dict[str, object]], list[tuple[int, int]]]:
    empty_result: tuple[list[dict[str, object]], list[tuple[int, int]]] = ([], [])
    if htf.empty or "timestamp" not in htf.columns:
        return empty_result
    prepared = htf.copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    if prepared.empty or "quote_volume" not in prepared.columns or "number_of_trades" not in prepared.columns:
        return empty_result
    quote_series = _numeric_column(prepared, "quote_volume")
    trade_series = _numeric_column(prepared, "number_of_trades")
    prepared["_quote_volume"] = quote_series
    prepared["_number_of_trades"] = trade_series
    min_index = max(config.baseline_candles, config.dormancy_candles, config.pregrowth_candles) + 1
    timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce")
    row_numbers = pd.Series(np.arange(len(prepared)), index=prepared.index)
    positive_quote = prepared["_quote_volume"].where(prepared["_quote_volume"] > 0)
    positive_trades = prepared["_number_of_trades"].where(prepared["_number_of_trades"] > 0)
    baseline_quote = positive_quote.shift(1).rolling(config.baseline_candles, min_periods=1).median()
    baseline_trades = positive_trades.shift(1).rolling(config.baseline_candles, min_periods=1).median()
    htf_return = (pd.to_numeric(prepared["close"], errors="coerce") - pd.to_numeric(prepared["open"], errors="coerce")) / pd.to_numeric(
        prepared["open"],
        errors="coerce",
    )
    htf_range = (pd.to_numeric(prepared["high"], errors="coerce") - pd.to_numeric(prepared["low"], errors="coerce")) / pd.to_numeric(
        prepared["open"],
        errors="coerce",
    )
    quote_ratio = (_numeric_column(prepared, "_quote_volume") / baseline_quote).replace([np.inf, -np.inf], np.nan)
    trade_ratio = (_numeric_column(prepared, "_number_of_trades") / baseline_trades).replace([np.inf, -np.inf], np.nan)
    gate = (
        row_numbers.ge(min_index)
        & quote_ratio.ge(float(config.targeted_backfill_min_htf_quote_ratio))
        & trade_ratio.ge(float(config.targeted_backfill_min_htf_trade_ratio))
        & htf_return.ge(float(config.targeted_backfill_min_htf_return_pct))
        & htf_range.ge(float(config.targeted_backfill_min_htf_range_pct))
    )
    seed_indices = list(np.flatnonzero(gate.to_numpy(dtype=bool, na_value=False)))
    if not seed_indices:
        return empty_result
    scored: list[tuple[float, int]] = []
    for idx in seed_indices:
        score = (
            float(htf_return.iloc[idx]) * 100.0
            + float(htf_range.iloc[idx]) * 25.0
            + math.log1p(max(0.0, float(quote_ratio.iloc[idx]) if np.isfinite(float(quote_ratio.iloc[idx])) else 0.0))
            + math.log1p(max(0.0, float(trade_ratio.iloc[idx]) if np.isfinite(float(trade_ratio.iloc[idx])) else 0.0))
        )
        scored.append((score, int(idx)))
    scored.sort(reverse=True)
    max_events = int(config.targeted_backfill_max_events_per_symbol)
    if max_events > 0:
        seed_indices = [idx for _, idx in scored[:max_events]]
    else:
        seed_indices = [idx for _, idx in scored]
    seed_indices.sort()
    rows: list[dict[str, object]] = []
    windows: list[tuple[int, int]] = []
    window_tail_ms = (int(config.ltf_max_confirm_candles) + 1) * int(ltf_ms)
    for idx in seed_indices:
        row = prepared.iloc[idx]
        ts = int(row["timestamp"])
        htf_close_ms = ts + int(htf_ms)
        window_start = int(ts)
        window_end = int(htf_close_ms + window_tail_ms - 1)
        windows.append((window_start, window_end))
        rows.append(
            {
                "symbol": symbol,
                "targeted_ltf_phase": "pre_entry",
                "targeted_ltf_plan_status": "planned",
                "selection_model": "closed_htf_seed_gate_only",
                "timestamp_ms": ts,
                "timestamp_utc": _timestamp_to_utc(ts),
                "htf_close_ms": htf_close_ms,
                "htf_close_utc": _timestamp_to_utc(htf_close_ms),
                "htf_timeframe": config.htf_timeframe,
                "ltf_timeframe": config.ltf_timeframe,
                "window_start_ms": window_start,
                "window_start_utc": _timestamp_to_utc(window_start),
                "window_end_ms": window_end,
                "window_end_utc": _timestamp_to_utc(window_end),
                "window_ms": int(window_end - window_start + 1),
                "window_model": "htf_anomaly_start_to_max_entry_next_open_only",
                "seed_gate": _targeted_ltf_seed_gate_description(config),
                "htf_return_pct": float(htf_return.iloc[idx]),
                "htf_range_pct": float(htf_range.iloc[idx]),
                "htf_quote_ratio": float(quote_ratio.iloc[idx]),
                "htf_trade_ratio": float(trade_ratio.iloc[idx]),
                "anomaly_quote_volume": float(row["_quote_volume"]),
                "anomaly_number_of_trades": float(row["_number_of_trades"]),
            }
        )
    return rows, windows



def _concat_targeted_phase_frames(
    phase_frames: list[tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    *,
    frame_index: int,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for phase, plan, fetch, materialize in phase_frames:
        source = (plan, fetch, materialize)[frame_index - 1]
        if source.empty:
            continue
        frame = source.copy()
        if "targeted_ltf_phase" not in frame.columns:
            frame.insert(0, "targeted_ltf_phase", phase)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _build_targeted_ltf_post_entry_backfill_plan(
    *,
    storage: ParquetStorage,
    symbols: Iterable[str],
    start_ms: int,
    end_ms: int,
    config: HtfLtfRunnerDiscoveryConfig,
    progress_label: str,
) -> tuple[pd.DataFrame, dict[str, list[tuple[int, int]]]]:
    rows: list[dict[str, object]] = []
    windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    selected_symbols = tuple(symbols)
    progress = _ProgressLine(label=progress_label, total=len(selected_symbols))
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    horizon_ms = int(config.runner_horizon_minutes) * 60_000
    for index, symbol in enumerate(selected_symbols, start=1):
        progress.update(index=index, item=symbol)
        htf = _load_frame(storage, symbol, config.htf_timeframe, start_ms=start_ms, end_ms=end_ms)
        if htf.empty:
            continue
        ltf = _load_frame(
            storage,
            symbol,
            config.ltf_timeframe,
            start_ms=start_ms - htf_ms * max(config.baseline_candles, config.dormancy_candles),
            end_ms=end_ms + horizon_ms,
        )
        if ltf.empty:
            continue
        oi = _load_oi_frame(storage, symbol, config=config, start_ms=start_ms, end_ms=end_ms)
        candidate_rows, _ = _collect_symbol_candidates(symbol=symbol, htf=htf, ltf=ltf, oi=oi, config=config)
        symbol_windows: list[tuple[int, int]] = []
        symbol_rows: list[dict[str, object]] = []
        executable_window_count = 0
        first_signal_count = 0
        for candidate in candidate_rows:
            entry_windows = _build_ltf_entry_windows(candidate, ltf=ltf, oi=oi, config=config)
            for entry_window in entry_windows:
                if entry_window.get("window_execution_ok") is not True:
                    continue
                planned = _post_entry_fetch_window_for_signal(entry_window, config=config, ltf_ms=ltf_ms)
                if planned is None:
                    continue
                window_start, window_end = planned
                symbol_windows.append((window_start, window_end))
                executable_window_count += 1
                symbol_rows.append(
                    _post_entry_plan_row(
                        entry_window,
                        signal_scope="entry_window",
                        window_start=window_start,
                        window_end=window_end,
                        config=config,
                    )
                )
            signal = _build_first_ltf_signal(candidate, ltf=ltf, oi=oi, config=config)
            if signal is None:
                continue
            planned = _post_entry_fetch_window_for_signal(signal, config=config, ltf_ms=ltf_ms)
            if planned is None:
                continue
            window_start, window_end = planned
            symbol_windows.append((window_start, window_end))
            first_signal_count += 1
            symbol_rows.append(
                _post_entry_plan_row(
                    signal,
                    signal_scope="first_signal",
                    window_start=window_start,
                    window_end=window_end,
                    config=config,
                )
            )
        if symbol_windows:
            windows_by_symbol[symbol] = symbol_windows
            rows.extend(symbol_rows)
        if candidate_rows or executable_window_count or first_signal_count:
            rows.append(
                {
                    "targeted_ltf_phase": "post_entry",
                    "symbol": symbol,
                    "targeted_ltf_plan_status": "symbol_summary",
                    "htf_timeframe": config.htf_timeframe,
                    "ltf_timeframe": config.ltf_timeframe,
                    "pre_entry_candidates": int(len(candidate_rows)),
                    "pre_entry_executable_windows": int(executable_window_count),
                    "pre_entry_first_signals": int(first_signal_count),
                    "post_entry_windows": int(len(symbol_windows)),
                    "selection_model": "known_at_entry_ltf_confirmation_and_entry_guards_only",
                    "window_model": "post_entry_fetch_for_executable_pre_entry_signal_and_runner_label",
                }
            )
    progress.finish()
    plan = pd.DataFrame(rows)
    summary = {
        "targeted_ltf_phase": "post_entry",
        "symbol": "__all__",
        "targeted_ltf_plan_status": "summary",
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "symbols": len(selected_symbols),
        "candidate_seed_rows": int(len(plan.loc[plan.get("targeted_ltf_plan_status", pd.Series(dtype=str)).astype(str).eq("planned")])) if not plan.empty else 0,
        "symbols_with_windows": int(len(windows_by_symbol)),
        "raw_targeted_windows": int(sum(len(windows) for windows in windows_by_symbol.values())),
        "window_model": "post_entry_fetch_after_known_at_entry_pre_filter",
        "selection_model": "no_future_labels_no_post_entry_prices",
    }
    if plan.empty:
        plan = pd.DataFrame([summary])
    else:
        plan = pd.concat([pd.DataFrame([summary]), plan], ignore_index=True, sort=False)
    return plan, windows_by_symbol


def _post_entry_fetch_window_for_signal(
    signal: Mapping[str, object],
    *,
    config: HtfLtfRunnerDiscoveryConfig,
    ltf_ms: int,
) -> tuple[int, int] | None:
    entry_ts = _coerce_int(signal.get("entry_timestamp_ms"))
    htf_close_ms = _coerce_int(signal.get("htf_close_ms"))
    if entry_ts is None or htf_close_ms is None:
        return None
    max_hold_ms = int(config.max_hold_candles) * int(ltf_ms)
    runner_horizon_ms = int(config.runner_horizon_minutes) * 60_000
    window_start = int(entry_ts)
    window_end_exclusive = max(int(entry_ts) + max_hold_ms, int(htf_close_ms) + runner_horizon_ms)
    if window_end_exclusive <= window_start:
        return None
    return window_start, int(window_end_exclusive - 1)


def _post_entry_plan_row(
    signal: Mapping[str, object],
    *,
    signal_scope: str,
    window_start: int,
    window_end: int,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    return {
        "targeted_ltf_phase": "post_entry",
        "symbol": signal.get("symbol", ""),
        "targeted_ltf_plan_status": "planned",
        "signal_scope": signal_scope,
        "selection_model": "known_at_entry_ltf_confirmation_and_entry_guards_only",
        "timestamp_ms": signal.get("timestamp_ms", float("nan")),
        "timestamp_utc": signal.get("timestamp_utc", ""),
        "htf_close_ms": signal.get("htf_close_ms", float("nan")),
        "htf_close_utc": signal.get("htf_close_utc", ""),
        "decision_available_timestamp_ms": signal.get("decision_available_timestamp_ms", float("nan")),
        "decision_available_timestamp_utc": signal.get("decision_available_timestamp_utc", ""),
        "entry_timestamp_ms": signal.get("entry_timestamp_ms", float("nan")),
        "entry_timestamp_utc": signal.get("entry_timestamp_utc", ""),
        "confirmation_candles": signal.get("confirmation_candles", float("nan")),
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "window_start_ms": int(window_start),
        "window_start_utc": _timestamp_to_utc(window_start),
        "window_end_ms": int(window_end),
        "window_end_utc": _timestamp_to_utc(window_end),
        "window_ms": int(window_end - window_start + 1),
        "window_model": "post_entry_fetch_for_executable_pre_entry_signal_and_runner_label",
        "entry_drift_pct": signal.get("entry_drift_pct", float("nan")),
        "initial_risk_pct": signal.get("initial_risk_pct", float("nan")),
        "window_execution_ok": signal.get("window_execution_ok", True),
        "window_execution_skip_reason": signal.get("window_execution_skip_reason", ""),
    }


def _coerce_int(value: object) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return int(number)

def _targeted_ltf_seed_gate_description(config: HtfLtfRunnerDiscoveryConfig) -> str:
    return (
        "htf_quote_ratio>={quote:.4g} AND htf_trade_ratio>={trade:.4g} AND "
        "htf_return_pct>={ret:.4%} AND htf_range_pct>={rng:.4%}"
    ).format(
        quote=float(config.targeted_backfill_min_htf_quote_ratio),
        trade=float(config.targeted_backfill_min_htf_trade_ratio),
        ret=float(config.targeted_backfill_min_htf_return_pct),
        rng=float(config.targeted_backfill_min_htf_range_pct),
    )


def _ensure_targeted_ltf_backfill(
    *,
    cache_dir: Path,
    ltf_timeframe: str,
    windows_by_symbol: Mapping[str, Iterable[tuple[int, int]]],
    progress_label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not windows_by_symbol:
        return (
            pd.DataFrame([{"status": "no_targeted_windows", "reason": "strict_htf_seed_gate_selected_zero_windows"}]),
            pd.DataFrame([{"status": "not_run", "reason": "no_targeted_windows"}]),
        )
    from research_tools.anomaly_strategy_backtest import ensure_targeted_aggtrade_subminute_cache

    return ensure_targeted_aggtrade_subminute_cache(
        cache_dir=cache_dir,
        windows_by_symbol=windows_by_symbol,
        target_timeframes=(str(ltf_timeframe),),
        progress_label=progress_label,
    )

def _collect_symbol_candidates(
    *,
    symbol: str,
    htf: pd.DataFrame,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> tuple[list[dict[str, object]], int]:
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    horizon_ms = int(config.runner_horizon_minutes) * 60_000
    rows: list[dict[str, object]] = []
    prepared = htf.copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    if "quote_volume" not in prepared.columns or "number_of_trades" not in prepared.columns:
        return [], 0
    quote_series = _numeric_column(prepared, "quote_volume")
    trade_series = _numeric_column(prepared, "number_of_trades")
    prepared["_quote_volume"] = quote_series
    prepared["_number_of_trades"] = trade_series

    min_index = max(config.baseline_candles, config.dormancy_candles, config.pregrowth_candles) + 1
    timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce")
    max_timestamp = int(timestamps.max()) if not timestamps.empty else 0
    row_numbers = pd.Series(np.arange(len(prepared)), index=prepared.index)
    positive_quote = prepared["_quote_volume"].where(prepared["_quote_volume"] > 0)
    positive_trades = prepared["_number_of_trades"].where(prepared["_number_of_trades"] > 0)
    baseline_quote_fast = positive_quote.shift(1).rolling(config.baseline_candles, min_periods=1).median()
    baseline_trades_fast = positive_trades.shift(1).rolling(config.baseline_candles, min_periods=1).median()
    htf_return_fast = (pd.to_numeric(prepared["close"], errors="coerce") - pd.to_numeric(prepared["open"], errors="coerce")) / pd.to_numeric(
        prepared["open"],
        errors="coerce",
    )
    scanned_mask = (row_numbers >= min_index) & ((timestamps + htf_ms + horizon_ms) <= (max_timestamp + htf_ms))
    anomaly_gate_fast = (
        scanned_mask
        & (_numeric_column(prepared, "_quote_volume") / baseline_quote_fast).ge(config.min_htf_quote_ratio)
        & (_numeric_column(prepared, "_number_of_trades") / baseline_trades_fast).ge(config.min_htf_trade_ratio)
        & htf_return_fast.ge(config.min_htf_return_pct)
    )
    quote_ratio_fast = (_numeric_column(prepared, "_quote_volume") / baseline_quote_fast).replace([np.inf, -np.inf], np.nan)
    trade_ratio_fast = (_numeric_column(prepared, "_number_of_trades") / baseline_trades_fast).replace([np.inf, -np.inf], np.nan)
    prior_spike_context = _prepare_prior_spike_context(
        prepared,
        timestamps=timestamps,
        quote_ratio=quote_ratio_fast,
        trade_ratio=trade_ratio_fast,
        htf_return=htf_return_fast,
    )
    scanned_rows = int(scanned_mask.sum())

    for idx in np.flatnonzero(anomaly_gate_fast.to_numpy(dtype=bool, na_value=False)):
        row = prepared.iloc[idx]
        ts = int(row["timestamp"])
        close_ts = ts + htf_ms
        baseline = prepared.iloc[idx - config.baseline_candles : idx]
        dormancy = prepared.iloc[idx - config.dormancy_candles : idx]
        pregrowth = prepared.iloc[idx - config.pregrowth_candles : idx]
        if baseline.empty or dormancy.empty or pregrowth.empty:
            continue

        anomaly_open = float(row["open"])
        anomaly_high = float(row["high"])
        anomaly_low = float(row["low"])
        anomaly_close = float(row["close"])
        anomaly_quote = float(row["_quote_volume"])
        anomaly_trades = float(row["_number_of_trades"]) if np.isfinite(float(row["_number_of_trades"])) else float("nan")
        baseline_quote = _positive_median(baseline["_quote_volume"])
        baseline_trades = _positive_median(baseline["_number_of_trades"])
        dormancy_quote = _positive_median(dormancy["_quote_volume"])
        dormancy_trades = _positive_median(dormancy["_number_of_trades"])
        quote_ratio = _safe_divide(anomaly_quote, baseline_quote)
        trade_ratio = _safe_divide(anomaly_trades, baseline_trades)
        dormancy_quote_ratio = _safe_divide(anomaly_quote, dormancy_quote)
        dormancy_trade_ratio = _safe_divide(anomaly_trades, dormancy_trades)
        htf_return = _safe_divide(anomaly_close - anomaly_open, anomaly_open)
        htf_range_pct = _safe_divide(anomaly_high - anomaly_low, anomaly_open)
        dormancy_range_pct_median = _positive_median((dormancy["high"].astype(float) - dormancy["low"].astype(float)) / dormancy["open"].astype(float))

        pregrowth_features = _pregrowth_features(pregrowth)
        oi_features = _oi_pregrowth_features(
            oi,
            start_ms=int(pregrowth.iloc[0]["timestamp"]),
            decision_ms=close_ts,
        )

        anomaly_gate = (
            quote_ratio >= config.min_htf_quote_ratio
            and trade_ratio >= config.min_htf_trade_ratio
            and htf_return >= config.min_htf_return_pct
        )
        dormancy_ok = (
            dormancy_quote_ratio >= config.min_dormancy_to_anomaly_quote_ratio
            and dormancy_trade_ratio >= config.min_dormancy_to_anomaly_trade_ratio
            and dormancy_range_pct_median <= config.max_dormancy_range_pct_median
        )
        smooth_price_ok = (
            pregrowth_features["pregrowth_return_pct"] >= config.min_pregrowth_return_pct
            and pregrowth_features["pregrowth_max_single_return_pct"] <= config.max_pregrowth_single_candle_return_pct
            and pregrowth_features["pregrowth_positive_step_share"] >= config.min_pregrowth_positive_step_share
        )
        oi_ok = (
            oi_features["pregrowth_oi_status"] == "ok"
            and oi_features["pregrowth_oi_change_pct"] >= config.min_pregrowth_oi_change_pct
        )
        if not config.require_pregrowth_oi and oi_features["pregrowth_oi_status"] != "ok":
            oi_ok = True
        if not anomaly_gate:
            continue
        label = _future_runner_label(
            ltf,
            start_ms=close_ts,
            horizon_ms=horizon_ms,
            reference_price=anomaly_close,
            anomaly_low=anomaly_low,
            target_return_pct=config.runner_target_return_pct,
            expected_step_ms=ltf_ms,
        )
        htf_ltf_features = _htf_internal_ltf_features(
            ltf,
            start_ms=ts,
            end_ms=close_ts,
            htf_open=anomaly_open,
            htf_close=anomaly_close,
            expected_step_ms=ltf_ms,
        )
        prior_spike_features = _prior_spike_features_for_index(prior_spike_context, idx=idx, current_timestamp_ms=ts)
        status = "ok"
        setup_nature = _setup_nature(
            anomaly_gate=anomaly_gate,
            dormancy_ok=dormancy_ok,
            smooth_price_ok=smooth_price_ok,
            oi_ok=oi_ok,
            quote_ratio=quote_ratio,
            trade_ratio=trade_ratio,
        )
        rows.append(
            {
                "symbol": symbol,
                "status": status,
                "setup_nature": setup_nature,
                "timestamp_ms": ts,
                "timestamp_utc": _timestamp_to_utc(ts),
                "htf_close_ms": close_ts,
                "htf_close_utc": _timestamp_to_utc(close_ts),
                "htf_timeframe": config.htf_timeframe,
                "ltf_timeframe": config.ltf_timeframe,
                "future_label_available_at_entry": False,
                "anomaly_open": anomaly_open,
                "anomaly_high": anomaly_high,
                "anomaly_low": anomaly_low,
                "anomaly_close": anomaly_close,
                "htf_return_pct": htf_return,
                "htf_range_pct": htf_range_pct,
                "anomaly_quote_volume": anomaly_quote,
                "anomaly_number_of_trades": anomaly_trades,
                "baseline_quote_volume_median": baseline_quote,
                "baseline_number_of_trades_median": baseline_trades,
                "htf_quote_ratio": quote_ratio,
                "htf_trade_ratio": trade_ratio,
                "dormancy_quote_volume_median": dormancy_quote,
                "dormancy_number_of_trades_median": dormancy_trades,
                "dormancy_to_anomaly_quote_ratio": dormancy_quote_ratio,
                "dormancy_to_anomaly_trade_ratio": dormancy_trade_ratio,
                "dormancy_range_pct_median": dormancy_range_pct_median,
                "dormancy_ok": dormancy_ok,
                "smooth_price_growth_ok": smooth_price_ok,
                "pregrowth_oi_ok": oi_ok,
                "htf_anomaly_gate": anomaly_gate,
                **prior_spike_features,
                **pregrowth_features,
                **oi_features,
                **htf_ltf_features,
                **label,
            }
        )
    return rows, scanned_rows


def _build_first_ltf_signal(
    candidate: dict[str, object],
    *,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object] | None:
    if candidate.get("status") != "ok":
        return None
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    htf_ms = _timeframe_ms(config.htf_timeframe)
    start_ms = int(candidate["htf_close_ms"])
    end_ms = start_ms + int(config.runner_horizon_minutes) * 60_000
    entry_window = _strict_ltf_window(ltf, start_ms=start_ms, end_exclusive_ms=end_ms, expected_step_ms=ltf_ms)
    segment = entry_window.frame
    if len(segment) <= config.ltf_min_confirm_candles:
        return None
    baseline_quote = float(candidate.get("baseline_quote_volume_median") or float("nan"))
    baseline_trades = float(candidate.get("baseline_number_of_trades_median") or float("nan"))
    anomaly_low = float(candidate["anomaly_low"])
    anomaly_close = float(candidate["anomaly_close"])

    for confirm_count in range(config.ltf_min_confirm_candles, min(config.ltf_max_confirm_candles, len(segment) - 1) + 1):
        closed = segment.head(confirm_count).copy()
        decision_row = closed.iloc[-1]
        decision_ts = int(decision_row["timestamp"])
        decision_available_ts = decision_ts + ltf_ms
        entry_row = segment.iloc[confirm_count]
        entry_ts = int(entry_row["timestamp"])
        if entry_ts < decision_available_ts:
            continue
        signal_features = _ltf_confirmation_features(
            closed,
            baseline_quote=baseline_quote,
            baseline_trades=baseline_trades,
            htf_ms=htf_ms,
        )
        ltf_ok = (
            signal_features["ltf_confirm_return_pct"] >= config.min_ltf_confirm_return_pct
            and signal_features["ltf_quote_pace_ratio"] >= config.min_ltf_quote_pace_ratio
            and signal_features["ltf_trade_pace_ratio"] >= config.min_ltf_trade_pace_ratio
            and signal_features["ltf_second_half_return_pct"] >= config.min_ltf_second_half_return_pct
            and signal_features["ltf_quote_acceleration"] >= config.min_ltf_quote_acceleration
            and signal_features["ltf_trade_acceleration"] >= config.min_ltf_trade_acceleration
            and float(closed["low"].min()) >= anomaly_low
        )
        taker_share = signal_features["ltf_taker_buy_quote_share"]
        if config.min_ltf_taker_buy_share is not None:
            ltf_ok = ltf_ok and np.isfinite(taker_share) and taker_share >= config.min_ltf_taker_buy_share
        if not ltf_ok:
            continue
        raw_entry_price = float(entry_row["open"])
        entry_price = raw_entry_price * (1.0 + config.entry_slippage_pct)
        decision_close = float(decision_row["close"])
        entry_drift = abs(_safe_divide(entry_price - decision_close, decision_close))
        structural_low = min(anomaly_low, float(closed["low"].min()))
        initial_stop = structural_low * (1.0 - config.structural_stop_buffer_pct)
        initial_risk = entry_price - initial_stop
        initial_risk_pct = _safe_divide(initial_risk, entry_price)
        if not np.isfinite(initial_risk) or initial_risk <= 0.0:
            continue
        if entry_drift > config.max_entry_drift_pct:
            continue
        if initial_risk_pct > config.max_initial_risk_pct:
            continue
        oi_at_signal = _oi_asof(oi, decision_available_ts)
        return {
            **candidate,
            "signal_status": "selected",
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "decision_available_timestamp_ms": decision_available_ts,
            "decision_available_timestamp_utc": _timestamp_to_utc(decision_available_ts),
            "decision_close": decision_close,
            "confirmation_candles": confirm_count,
            "entry_timestamp_ms": entry_ts,
            "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
            "raw_entry_price": raw_entry_price,
            "entry_price": entry_price,
            "entry_price_model": "next_ltf_open_plus_adverse_slippage",
            "entry_drift_pct": entry_drift,
            "initial_stop": initial_stop,
            "initial_risk": initial_risk,
            "initial_risk_pct": initial_risk_pct,
            "structural_stop_model": "min_anomaly_low_and_closed_ltf_lows_before_decision_minus_buffer",
            "tp_model": "none",
            "signal_oi_status": oi_at_signal["status"],
            "signal_oi_timestamp_ms": oi_at_signal["timestamp_ms"],
            "signal_oi_available_timestamp_ms": oi_at_signal["available_timestamp_ms"],
            "signal_oi_open_interest": oi_at_signal["open_interest"],
            **_strict_ltf_window_audit(entry_window, prefix="entry_ltf"),
            **signal_features,
        }
    return None


def _build_ltf_entry_windows(
    candidate: dict[str, object],
    *,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> list[dict[str, object]]:
    if candidate.get("status") != "ok":
        return []
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    htf_ms = _timeframe_ms(config.htf_timeframe)
    start_ms = int(candidate["htf_close_ms"])
    end_ms = start_ms + int(config.runner_horizon_minutes) * 60_000
    entry_window = _strict_ltf_window(ltf, start_ms=start_ms, end_exclusive_ms=end_ms, expected_step_ms=ltf_ms)
    segment = entry_window.frame
    baseline_quote = float(candidate.get("baseline_quote_volume_median") or float("nan"))
    baseline_trades = float(candidate.get("baseline_number_of_trades_median") or float("nan"))
    anomaly_low = float(candidate["anomaly_low"])
    rows: list[dict[str, object]] = []

    for confirm_count in _entry_window_counts(config):
        unavailable_reason = _entry_window_unavailable_reason(entry_window, required_candles=int(confirm_count) + 1)
        base_row = {
            **candidate,
            "entry_window_status": unavailable_reason,
            "signal_status": "entry_window_research",
            "confirmation_candles": int(confirm_count),
            "window_future_label_available_at_entry": False,
            "window_uses_future_label_as_entry_filter": False,
            "window_execution_ok": False,
            "window_execution_skip_reason": unavailable_reason,
            **_strict_ltf_window_audit(entry_window, prefix="entry_ltf"),
        }
        if len(segment) <= confirm_count:
            rows.append({**base_row, "window_available_candles": int(len(segment))})
            continue

        closed = segment.head(confirm_count).copy()
        decision_row = closed.iloc[-1]
        decision_ts = int(decision_row["timestamp"])
        decision_available_ts = decision_ts + ltf_ms
        entry_row = segment.iloc[confirm_count]
        entry_ts = int(entry_row["timestamp"])
        signal_features = _ltf_confirmation_features(
            closed,
            baseline_quote=baseline_quote,
            baseline_trades=baseline_trades,
            htf_ms=htf_ms,
        )
        decay_features = _ltf_decay_features(closed)
        oi_at_decision = _oi_asof(oi, decision_available_ts)
        raw_entry_price = float(entry_row["open"])
        entry_price = raw_entry_price * (1.0 + config.entry_slippage_pct)
        decision_close = float(decision_row["close"])
        entry_drift = abs(_safe_divide(entry_price - decision_close, decision_close))
        structural_low = min(anomaly_low, float(pd.to_numeric(closed["low"], errors="coerce").min()))
        initial_stop = structural_low * (1.0 - config.structural_stop_buffer_pct)
        initial_risk = entry_price - initial_stop
        initial_risk_pct = _safe_divide(initial_risk, entry_price)
        low_broke = bool(float(pd.to_numeric(closed["low"], errors="coerce").min()) < anomaly_low)
        oi_change_from_pregrowth = _safe_divide(
            float(oi_at_decision["open_interest"]) - float(candidate.get("pregrowth_oi_end", float("nan"))),
            float(candidate.get("pregrowth_oi_end", float("nan"))),
        )
        execution_skip = ""
        if entry_ts < decision_available_ts:
            execution_skip = "entry_before_decision_available"
        elif low_broke:
            execution_skip = "confirmation_low_broke_anomaly_low"
        elif not np.isfinite(initial_risk) or initial_risk <= 0.0:
            execution_skip = "invalid_initial_risk"
        elif entry_drift > config.max_entry_drift_pct:
            execution_skip = "entry_drift_too_large"
        elif initial_risk_pct > config.max_initial_risk_pct:
            execution_skip = "initial_risk_too_large"
        volume_sustain_ok = (
            not bool(decay_features["ltf_quote_decay_under50"])
            and signal_features["ltf_confirm_return_pct"] >= 0.0
            and signal_features["ltf_quote_pace_ratio"] >= config.min_ltf_quote_pace_ratio
            and signal_features["ltf_trade_pace_ratio"] >= config.min_ltf_trade_pace_ratio
            and signal_features["ltf_quote_acceleration"] >= 0.8
            and signal_features["ltf_trade_acceleration"] >= 0.8
        )
        prior_median_ratio = float(candidate.get("current_vs_prior_spike_median_quote", float("nan")))
        rows.append(
            {
                **base_row,
                "entry_window_status": "ok",
                "window_available_candles": int(len(segment)),
                "decision_timestamp_ms": decision_ts,
                "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
                "decision_available_timestamp_ms": decision_available_ts,
                "decision_available_timestamp_utc": _timestamp_to_utc(decision_available_ts),
                "decision_close": decision_close,
                "entry_timestamp_ms": entry_ts,
                "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
                "raw_entry_price": raw_entry_price,
                "entry_price": entry_price,
                "entry_price_model": "next_ltf_open_plus_adverse_slippage",
                "entry_drift_pct": entry_drift,
                "initial_stop": initial_stop,
                "initial_risk": initial_risk,
                "initial_risk_pct": initial_risk_pct,
                "structural_stop_model": "min_anomaly_low_and_closed_ltf_lows_before_decision_minus_buffer",
                "tp_model": "none",
                "window_low_broke_anomaly_low": low_broke,
                "window_volume_sustain_ok": bool(volume_sustain_ok),
                "window_volume_sustain_above_prior_median_ok": bool(volume_sustain_ok and (not np.isfinite(prior_median_ratio) or prior_median_ratio >= 1.0)),
                "window_volume_sustain_above_prior_150pct_ok": bool(volume_sustain_ok and np.isfinite(prior_median_ratio) and prior_median_ratio >= 1.5),
                "window_oi_status": oi_at_decision["status"],
                "window_oi_timestamp_ms": oi_at_decision["timestamp_ms"],
                "window_oi_available_timestamp_ms": oi_at_decision["available_timestamp_ms"],
                "window_oi_open_interest": oi_at_decision["open_interest"],
                "window_oi_change_from_pregrowth_end_pct": oi_change_from_pregrowth,
                "window_oi_nonnegative_from_pregrowth_end": bool(np.isfinite(oi_change_from_pregrowth) and oi_change_from_pregrowth >= 0.0),
                "window_execution_ok": execution_skip == "",
                "window_execution_skip_reason": execution_skip,
                **signal_features,
                **decay_features,
            }
        )
    return rows


def _simulate_no_tp_runner_trade(
    signal: dict[str, object],
    *,
    ltf: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    entry_ts = int(signal["entry_timestamp_ms"])
    entry_price = float(signal["entry_price"])
    initial_stop = float(signal["initial_stop"])
    initial_risk = entry_price - initial_stop
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return _skipped_trade(signal, "invalid_initial_risk")

    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    max_hold_ms = int(config.max_hold_candles) * ltf_ms
    future_window = _strict_ltf_window(
        ltf,
        start_ms=entry_ts,
        end_exclusive_ms=entry_ts + max_hold_ms,
        expected_step_ms=ltf_ms,
    )
    future = future_window.frame
    window_audit = _strict_ltf_window_audit(future_window, prefix="post_entry_ltf")
    if future.empty:
        return _skipped_trade(signal, _post_entry_window_skip_reason(future_window), **window_audit)

    active_stop = initial_stop
    trail_updates = 0
    max_high = entry_price
    min_low = entry_price
    prior_trailing_lows: list[float] = []
    trailing_lookback = max(1, int(config.trail_lookback_candles))
    exit_reason = ""
    exit_ts = float("nan")
    raw_exit_price = float("nan")
    exit_price = float("nan")
    exit_stop_before_update = active_stop

    for _, row in future.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        new_high_or_equal = high >= max_high
        max_high = max(max_high, high)
        min_low = min(min_low, low)
        if low <= active_stop:
            exit_reason = "initial_stop" if trail_updates == 0 else "structural_trailing_stop"
            exit_ts = candle_ts
            raw_exit_price = active_stop
            exit_price = raw_exit_price * (1.0 - config.exit_slippage_pct)
            exit_stop_before_update = active_stop
            break
        if len(prior_trailing_lows) >= trailing_lookback and new_high_or_equal:
            structural_low = float(min(prior_trailing_lows[-trailing_lookback:]))
            candidate_stop = structural_low * (1.0 - config.trail_buffer_pct)
            if candidate_stop > active_stop and candidate_stop < close:
                active_stop = candidate_stop
                trail_updates += 1

        if np.isfinite(low):
            prior_trailing_lows.append(low)
            if len(prior_trailing_lows) > trailing_lookback:
                prior_trailing_lows.pop(0)

    if not exit_reason:
        if future_window.status != "ok":
            return _skipped_trade(signal, _post_entry_window_skip_reason(future_window), **window_audit)
        exit_reason = "time_exit"
        exit_ts = int(future.iloc[-1]["timestamp"])
        raw_exit_price = float(future.iloc[-1]["close"])
        exit_price = raw_exit_price * (1.0 - config.exit_slippage_pct)
        exit_stop_before_update = active_stop

    gross_return = (exit_price - entry_price) / entry_price
    net_return = gross_return - 2.0 * float(config.fee_rate)
    gross_r = (exit_price - entry_price) / initial_risk
    result = {
        **signal,
        "status": "closed",
        "skip_reason": "",
        "execution_guard": True,
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_reason": exit_reason,
        "exit_raw_price": raw_exit_price,
        "exit_price": exit_price,
        "exit_price_model": "stop_or_time_exit_minus_adverse_slippage_strict_ltf_path",
        "exit_stop_before_update": exit_stop_before_update,
        "trail_updates": trail_updates,
        "final_trailing_stop": active_stop,
        "tp1_hit": False,
        "tp_model": "none",
        "mfe_pct": _safe_divide(max_high - entry_price, entry_price),
        "mae_pct": _safe_divide(min_low - entry_price, entry_price),
        "gross_r": gross_r,
        "gross_return": gross_return,
        "net_return": net_return,
        "win": net_return > 0.0,
        "fee_rate": float(config.fee_rate),
        **window_audit,
    }
    return result


def _skipped_trade(signal: dict[str, object], reason: str, **extra: object) -> dict[str, object]:
    return {
        **signal,
        "status": "skipped",
        "skip_reason": reason,
        "execution_guard": False,
        "exit_timestamp_ms": float("nan"),
        "exit_timestamp_utc": "",
        "exit_reason": "",
        "tp1_hit": False,
        "gross_r": float("nan"),
        "gross_return": float("nan"),
        "net_return": float("nan"),
        **extra,
    }


def _future_runner_label(
    ltf: pd.DataFrame,
    *,
    start_ms: int,
    horizon_ms: int,
    reference_price: float,
    anomaly_low: float,
    target_return_pct: float,
    expected_step_ms: int,
) -> dict[str, object]:
    future_window = _strict_ltf_window(
        ltf,
        start_ms=int(start_ms),
        end_exclusive_ms=int(start_ms + horizon_ms),
        expected_step_ms=expected_step_ms,
    )
    future = future_window.frame
    audit = _strict_ltf_window_audit(future_window, prefix="future_ltf")
    if future.empty or not np.isfinite(reference_price) or reference_price <= 0:
        return {
            "future_label_status": _future_label_missing_status(future_window),
            "runner_10pct_next_hour": False,
            "runner_first_hit_ms": float("nan"),
            "runner_first_hit_utc": "",
            "future_max_return_pct": float("nan"),
            "future_min_return_pct": float("nan"),
            "anomaly_low_broken_before_runner": False,
            "clean_runner_without_low_break": False,
            **audit,
        }
    highs = pd.to_numeric(future["high"], errors="coerce")
    lows = pd.to_numeric(future["low"], errors="coerce")
    hit = future.loc[highs.ge(reference_price * (1.0 + target_return_pct))]
    first_hit_ms = int(hit.iloc[0]["timestamp"]) if not hit.empty else None
    if first_hit_ms is None and future_window.status != "ok":
        return {
            "future_label_status": _future_label_missing_status(future_window),
            "runner_10pct_next_hour": False,
            "runner_first_hit_ms": float("nan"),
            "runner_first_hit_utc": "",
            "future_max_return_pct": float("nan"),
            "future_min_return_pct": float("nan"),
            "anomaly_low_broken_before_runner": False,
            "clean_runner_without_low_break": False,
            **audit,
        }
    max_return = float(highs.max() / reference_price - 1.0)
    min_return = float(lows.min() / reference_price - 1.0)
    before = future.loc[future["timestamp"].astype("int64") <= (first_hit_ms if first_hit_ms is not None else int(start_ms + horizon_ms))]
    low_broken = bool(not before.empty and pd.to_numeric(before["low"], errors="coerce").min() < float(anomaly_low))
    runner = first_hit_ms is not None
    return {
        "future_label_status": "ok",
        "runner_10pct_next_hour": runner,
        "runner_first_hit_ms": first_hit_ms if first_hit_ms is not None else float("nan"),
        "runner_first_hit_utc": _timestamp_to_utc(first_hit_ms) if first_hit_ms is not None else "",
        "future_max_return_pct": max_return,
        "future_min_return_pct": min_return,
        "anomaly_low_broken_before_runner": low_broken,
        "clean_runner_without_low_break": bool(runner and not low_broken),
        **audit,
    }



def _pregrowth_features(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {
            "pregrowth_return_pct": float("nan"),
            "pregrowth_positive_step_share": float("nan"),
            "pregrowth_max_single_return_pct": float("nan"),
            "pregrowth_max_pullback_pct": float("nan"),
        }
    opens = pd.to_numeric(frame["open"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    highs = pd.to_numeric(frame["high"], errors="coerce")
    lows = pd.to_numeric(frame["low"], errors="coerce")
    step_returns = closes.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    first_open = float(opens.iloc[0])
    last_close = float(closes.iloc[-1])
    rolling_peak = highs.cummax()
    pullback = ((rolling_peak - lows) / rolling_peak).replace([np.inf, -np.inf], np.nan)
    return {
        "pregrowth_return_pct": _safe_divide(last_close - first_open, first_open),
        "pregrowth_positive_step_share": float((step_returns > 0).mean()) if not step_returns.empty else 0.0,
        "pregrowth_max_single_return_pct": float(step_returns.max()) if not step_returns.empty else 0.0,
        "pregrowth_max_pullback_pct": float(pullback.max()) if not pullback.empty else 0.0,
    }


def _oi_pregrowth_features(oi: pd.DataFrame, *, start_ms: int, decision_ms: int) -> dict[str, object]:
    start = _oi_asof(oi, int(start_ms))
    end = _oi_asof(oi, int(decision_ms))
    if start["status"] != "ok" or end["status"] != "ok":
        return {
            "pregrowth_oi_status": "missing",
            "pregrowth_oi_change_pct": float("nan"),
            "pregrowth_oi_start": start["open_interest"],
            "pregrowth_oi_end": end["open_interest"],
            "pregrowth_oi_start_available_ms": start["available_timestamp_ms"],
            "pregrowth_oi_end_available_ms": end["available_timestamp_ms"],
        }
    change = _safe_divide(float(end["open_interest"]) - float(start["open_interest"]), float(start["open_interest"]))
    return {
        "pregrowth_oi_status": "ok",
        "pregrowth_oi_change_pct": change,
        "pregrowth_oi_start": start["open_interest"],
        "pregrowth_oi_end": end["open_interest"],
        "pregrowth_oi_start_available_ms": start["available_timestamp_ms"],
        "pregrowth_oi_end_available_ms": end["available_timestamp_ms"],
    }


def _ltf_confirmation_features(
    closed: pd.DataFrame,
    *,
    baseline_quote: float,
    baseline_trades: float,
    htf_ms: int,
) -> dict[str, float]:
    duration_ms = max(1, len(closed) * _infer_step_ms(closed))
    quote = float(_numeric_column(closed, "quote_volume").sum())
    trades = float(_numeric_column(closed, "number_of_trades").sum())
    expected_quote = baseline_quote * duration_ms / htf_ms
    expected_trades = baseline_trades * duration_ms / htf_ms
    first_open = float(closed.iloc[0]["open"])
    last_close = float(closed.iloc[-1]["close"])
    first_half = closed.head(max(1, len(closed) // 2))
    second_half = closed.tail(len(closed) - len(first_half))
    first_quote = float(_numeric_column(first_half, "quote_volume").sum())
    second_quote = float(_numeric_column(second_half, "quote_volume").sum())
    first_trades = float(_numeric_column(first_half, "number_of_trades").sum())
    second_trades = float(_numeric_column(second_half, "number_of_trades").sum())
    taker_quote = _numeric_column(closed, "taker_buy_quote_volume").sum()
    return {
        "ltf_confirm_return_pct": _safe_divide(last_close - first_open, first_open),
        "ltf_quote_volume": quote,
        "ltf_number_of_trades": trades,
        "ltf_quote_pace_ratio": _safe_divide(quote, expected_quote),
        "ltf_trade_pace_ratio": _safe_divide(trades, expected_trades),
        "ltf_second_half_return_pct": _safe_divide(float(second_half.iloc[-1]["close"]) - float(second_half.iloc[0]["open"]), float(second_half.iloc[0]["open"])) if not second_half.empty else float("nan"),
        "ltf_quote_acceleration": _safe_divide(second_quote, first_quote),
        "ltf_trade_acceleration": _safe_divide(second_trades, first_trades),
        "ltf_taker_buy_quote_share": _safe_divide(float(taker_quote), quote),
    }


def _ltf_decay_features(closed: pd.DataFrame) -> dict[str, object]:
    if closed.empty:
        return {
            "ltf_quote_min_adjacent_ratio": float("nan"),
            "ltf_quote_decay_under50": False,
            "ltf_quote_last_first_ratio": float("nan"),
            "ltf_quote_top1_share": float("nan"),
            "ltf_quote_last_share": float("nan"),
            "ltf_trade_min_adjacent_ratio": float("nan"),
            "ltf_trade_decay_under50": False,
            "ltf_trade_last_first_ratio": float("nan"),
        }
    quote = _numeric_column(closed, "quote_volume").replace([np.inf, -np.inf], np.nan)
    trades = _numeric_column(closed, "number_of_trades").replace([np.inf, -np.inf], np.nan)
    quote_values = quote.to_numpy(dtype=float)
    trade_values = trades.to_numpy(dtype=float)
    quote_adjacent = _adjacent_ratios(quote_values)
    trade_adjacent = _adjacent_ratios(trade_values)
    quote_total = float(np.nansum(quote_values))
    trade_total = float(np.nansum(trade_values))
    return {
        "ltf_quote_min_adjacent_ratio": float(np.nanmin(quote_adjacent)) if quote_adjacent.size else float("nan"),
        "ltf_quote_decay_under50": bool(quote_adjacent.size and np.nanmin(quote_adjacent) < 0.5),
        "ltf_quote_last_first_ratio": _safe_divide(float(quote_values[-1]), float(quote_values[0])) if quote_values.size else float("nan"),
        "ltf_quote_top1_share": _safe_divide(float(np.nanmax(quote_values)), quote_total),
        "ltf_quote_last_share": _safe_divide(float(quote_values[-1]), quote_total) if quote_values.size else float("nan"),
        "ltf_trade_min_adjacent_ratio": float(np.nanmin(trade_adjacent)) if trade_adjacent.size else float("nan"),
        "ltf_trade_decay_under50": bool(trade_adjacent.size and np.nanmin(trade_adjacent) < 0.5),
        "ltf_trade_last_first_ratio": _safe_divide(float(trade_values[-1]), float(trade_values[0])) if trade_values.size else float("nan"),
        "ltf_trade_top1_share": _safe_divide(float(np.nanmax(trade_values)), trade_total),
    }


def _adjacent_ratios(values: np.ndarray) -> np.ndarray:
    if len(values) < 2:
        return np.array([], dtype=float)
    previous = values[:-1].astype(float)
    current = values[1:].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = current / previous
    ratios[~np.isfinite(ratios)] = np.nan
    return ratios


def _htf_internal_ltf_features(
    ltf: pd.DataFrame,
    *,
    start_ms: int,
    end_ms: int,
    htf_open: float,
    htf_close: float,
    expected_step_ms: int,
) -> dict[str, object]:
    if ltf.empty or "timestamp" not in ltf.columns:
        return {
            "htf_ltf_status": "missing_ltf_inside_htf",
            "htf_ltf_candles": 0,
            "htf_ltf_quote_volume": float("nan"),
            "htf_ltf_number_of_trades": float("nan"),
            "htf_ltf_quote_top1_share": float("nan"),
            "htf_ltf_trade_top1_share": float("nan"),
            "htf_ltf_green_share": float("nan"),
            "htf_ltf_close_above_mid_share": float("nan"),
            "htf_ltf_second_half_return_pct": float("nan"),
            "htf_ltf_quote_acceleration": float("nan"),
            "htf_ltf_trade_acceleration": float("nan"),
            "htf_ltf_sustained_flow_ok": False,
            "htf_ltf_trade_count_status": "missing",
        }

    ltf_window = _strict_ltf_window(
        ltf,
        start_ms=int(start_ms),
        end_exclusive_ms=int(end_ms),
        expected_step_ms=int(expected_step_ms),
    )
    segment = ltf_window.frame
    if segment.empty or ltf_window.status != "ok":
        return {
            "htf_ltf_status": ltf_window.status if ltf_window.status != "ok" else "missing_ltf_inside_htf",
            "htf_ltf_candles": int(len(segment)),
            "htf_ltf_quote_volume": float("nan"),
            "htf_ltf_number_of_trades": float("nan"),
            "htf_ltf_quote_top1_share": float("nan"),
            "htf_ltf_trade_top1_share": float("nan"),
            "htf_ltf_green_share": float("nan"),
            "htf_ltf_close_above_mid_share": float("nan"),
            "htf_ltf_second_half_return_pct": float("nan"),
            "htf_ltf_quote_acceleration": float("nan"),
            "htf_ltf_trade_acceleration": float("nan"),
            "htf_ltf_sustained_flow_ok": False,
            "htf_ltf_trade_count_status": "missing",
            **_strict_ltf_window_audit(ltf_window, prefix="htf_ltf"),
        }

    quote = _numeric_column(segment, "quote_volume").replace([np.inf, -np.inf], np.nan)
    trades = _numeric_column(segment, "number_of_trades").replace([np.inf, -np.inf], np.nan)
    opens = pd.to_numeric(segment["open"], errors="coerce")
    closes = pd.to_numeric(segment["close"], errors="coerce")
    quote_total = float(quote.sum(min_count=1))
    trade_total = float(trades.sum(min_count=1))
    first_half = segment.head(max(1, len(segment) // 2))
    second_half = segment.tail(len(segment) - len(first_half))
    first_quote = _numeric_column(first_half, "quote_volume").replace([np.inf, -np.inf], np.nan)
    second_quote = _numeric_column(second_half, "quote_volume").replace([np.inf, -np.inf], np.nan)
    first_trades = _numeric_column(first_half, "number_of_trades").replace([np.inf, -np.inf], np.nan)
    second_trades = _numeric_column(second_half, "number_of_trades").replace([np.inf, -np.inf], np.nan)
    midpoint = (float(htf_open) + float(htf_close)) / 2.0
    quote_top1_share = _safe_divide(float(quote.max(skipna=True)), quote_total)
    trade_top1_share = _safe_divide(float(trades.max(skipna=True)), trade_total)
    second_half_return = (
        _safe_divide(float(second_half.iloc[-1]["close"]) - float(second_half.iloc[0]["open"]), float(second_half.iloc[0]["open"]))
        if not second_half.empty
        else float("nan")
    )
    quote_acceleration = _safe_divide(float(second_quote.sum(min_count=1)), float(first_quote.sum(min_count=1)))
    trade_acceleration = _safe_divide(float(second_trades.sum(min_count=1)), float(first_trades.sum(min_count=1)))
    trade_count_status = "ok" if np.isfinite(trade_total) and trade_total > 0 else "missing"
    sustained_flow_ok = (
        np.isfinite(quote_total)
        and quote_total > 0
        and trade_count_status == "ok"
        and np.isfinite(quote_top1_share)
        and quote_top1_share <= 0.55
        and np.isfinite(trade_top1_share)
        and trade_top1_share <= 0.55
        and float((closes > opens).mean()) >= 0.50
        and np.isfinite(second_half_return)
        and second_half_return >= 0.0
        and np.isfinite(quote_acceleration)
        and quote_acceleration >= 0.75
        and np.isfinite(trade_acceleration)
        and trade_acceleration >= 0.75
    )
    return {
        "htf_ltf_status": "ok",
        "htf_ltf_candles": int(len(segment)),
        "htf_ltf_quote_volume": quote_total,
        "htf_ltf_number_of_trades": trade_total,
        "htf_ltf_quote_top1_share": quote_top1_share,
        "htf_ltf_trade_top1_share": trade_top1_share,
        "htf_ltf_green_share": float((closes > opens).mean()),
        "htf_ltf_close_above_mid_share": float((closes >= midpoint).mean()),
        "htf_ltf_second_half_return_pct": second_half_return,
        "htf_ltf_quote_acceleration": quote_acceleration,
        "htf_ltf_trade_acceleration": trade_acceleration,
        "htf_ltf_sustained_flow_ok": bool(sustained_flow_ok),
        "htf_ltf_trade_count_status": trade_count_status,
        **_strict_ltf_window_audit(ltf_window, prefix="htf_ltf"),
    }


def _prepare_prior_spike_context(
    prepared: pd.DataFrame,
    *,
    timestamps: pd.Series,
    quote_ratio: pd.Series,
    trade_ratio: pd.Series,
    htf_return: pd.Series,
) -> dict[str, object]:
    quote = _numeric_column(prepared, "_quote_volume").to_numpy(dtype=float)
    ts = pd.to_numeric(timestamps, errors="coerce").to_numpy(dtype=float)
    spike_mask = (
        quote_ratio.ge(3.0).fillna(False).to_numpy(dtype=bool)
        & trade_ratio.ge(3.0).fillna(False).to_numpy(dtype=bool)
        & htf_return.ge(0.0).fillna(False).to_numpy(dtype=bool)
        & np.isfinite(quote)
        & (quote > 0)
    )
    spike_indices = np.flatnonzero(spike_mask)
    next_quote_ratio = np.full(len(prepared), np.nan, dtype=float)
    valid_next = spike_indices[spike_indices + 1 < len(prepared)]
    with np.errstate(divide="ignore", invalid="ignore"):
        next_quote_ratio[valid_next] = quote[valid_next + 1] / quote[valid_next]
    return {
        "timestamps": ts,
        "quote": quote,
        "spike_indices": spike_indices,
        "next_quote_ratio": next_quote_ratio,
    }


def _prior_spike_features_for_index(context: dict[str, object], *, idx: int, current_timestamp_ms: int) -> dict[str, object]:
    timestamps = context["timestamps"]  # type: ignore[assignment]
    quote = context["quote"]  # type: ignore[assignment]
    spike_indices = context["spike_indices"]  # type: ignore[assignment]
    next_quote_ratio = context["next_quote_ratio"]  # type: ignore[assignment]
    lookback_start = int(current_timestamp_ms) - 24 * 60 * 60 * 1000
    prior = spike_indices[(spike_indices < idx) & (timestamps[spike_indices] >= lookback_start)]  # type: ignore[index]
    if len(prior) == 0:
        return {
            "prior_spike_count_24h": 0,
            "prior_spike_median_quote": float("nan"),
            "prior_spike_max_quote": float("nan"),
            "current_vs_prior_spike_median_quote": float("nan"),
            "current_vs_prior_spike_max_quote": float("nan"),
            "prior_spike_next_decay50_share": float("nan"),
            "prior_spike_median_next_quote_ratio": float("nan"),
            "prior_spike_median_bars_to_decay50": float("nan"),
        }
    prior_quote = quote[prior]  # type: ignore[index]
    current_quote = float(quote[idx])  # type: ignore[index]
    median_quote = float(np.nanmedian(prior_quote))
    max_quote = float(np.nanmax(prior_quote))
    prior_next = next_quote_ratio[prior]  # type: ignore[index]
    bars_to_decay: list[float] = []
    for prior_idx in prior:
        prior_quote_value = float(quote[prior_idx])  # type: ignore[index]
        if not np.isfinite(prior_quote_value) or prior_quote_value <= 0.0:
            continue
        max_known_idx = min(int(idx), int(prior_idx) + 12)
        found = False
        for j in range(int(prior_idx) + 1, max_known_idx + 1):
            if float(quote[j]) < 0.5 * prior_quote_value:  # type: ignore[index]
                bars_to_decay.append(float(j - int(prior_idx)))
                found = True
                break
        if not found:
            bars_to_decay.append(float("nan"))
    return {
        "prior_spike_count_24h": int(len(prior)),
        "prior_spike_median_quote": median_quote,
        "prior_spike_max_quote": max_quote,
        "current_vs_prior_spike_median_quote": _safe_divide(current_quote, median_quote),
        "current_vs_prior_spike_max_quote": _safe_divide(current_quote, max_quote),
        "prior_spike_next_decay50_share": float(np.nanmean(prior_next < 0.5)) if np.isfinite(prior_next).any() else float("nan"),
        "prior_spike_median_next_quote_ratio": float(np.nanmedian(prior_next)) if np.isfinite(prior_next).any() else float("nan"),
        "prior_spike_median_bars_to_decay50": float(np.nanmedian(bars_to_decay)) if np.isfinite(bars_to_decay).any() else float("nan"),
    }


def _score_candidate_rules(candidates: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "rule",
        "events",
        "symbols",
        "runner_10pct_events",
        "runner_10pct_share",
        "clean_runner_events",
        "clean_runner_share",
        "low_break_share",
        "median_htf_quote_ratio",
        "median_htf_trade_ratio",
        "median_pregrowth_return_pct",
        "median_pregrowth_oi_change_pct",
        "median_htf_ltf_quote_top1_share",
        "uses_future_label_as_entry_filter",
        "status",
    ]
    if candidates.empty:
        return pd.DataFrame(columns=columns)

    htf_gate = _bool_series(candidates, "htf_anomaly_gate")
    dormancy = _bool_series(candidates, "dormancy_ok")
    smooth = _bool_series(candidates, "smooth_price_growth_ok")
    oi_growth = _actual_oi_growth_mask(candidates)
    strong_flow = _numeric_series(candidates, "htf_quote_ratio").ge(10.0) & _numeric_series(candidates, "htf_trade_ratio").ge(8.0)
    sustained_ltf = _bool_series(candidates, "htf_ltf_sustained_flow_ok")
    rules: list[tuple[str, pd.Series]] = [
        ("all_htf_anomaly_gate", htf_gate),
        ("dormancy_ok", htf_gate & dormancy),
        ("smooth_price_growth_ok", htf_gate & smooth),
        ("actual_pregrowth_oi_ok", htf_gate & oi_growth),
        ("dormancy_and_smooth_price", htf_gate & dormancy & smooth),
        ("dormancy_smooth_price_oi", htf_gate & dormancy & smooth & oi_growth),
        ("strong_htf_flow", htf_gate & strong_flow),
        ("sustained_internal_ltf_flow", htf_gate & sustained_ltf),
        ("dormant_smooth_sustained_flow", htf_gate & dormancy & smooth & sustained_ltf),
        ("dormant_smooth_oi_sustained_flow", htf_gate & dormancy & smooth & oi_growth & sustained_ltf),
    ]

    rows: list[dict[str, object]] = []
    for rule, mask in rules:
        subset = candidates.loc[mask.fillna(False)].copy()
        runners = _bool_series(subset, "runner_10pct_next_hour")
        clean = _bool_series(subset, "clean_runner_without_low_break")
        low_break = _bool_series(subset, "anomaly_low_broken_before_runner")
        events = int(len(subset))
        rows.append(
            {
                "rule": rule,
                "events": events,
                "symbols": int(subset["symbol"].nunique()) if "symbol" in subset.columns and events else 0,
                "runner_10pct_events": int(runners.sum()) if events else 0,
                "runner_10pct_share": float(runners.mean()) if events else float("nan"),
                "clean_runner_events": int(clean.sum()) if events else 0,
                "clean_runner_share": float(clean.mean()) if events else float("nan"),
                "low_break_share": float(low_break.mean()) if events else float("nan"),
                "median_htf_quote_ratio": _median_column(subset, "htf_quote_ratio"),
                "median_htf_trade_ratio": _median_column(subset, "htf_trade_ratio"),
                "median_pregrowth_return_pct": _median_column(subset, "pregrowth_return_pct"),
                "median_pregrowth_oi_change_pct": _median_column(subset, "pregrowth_oi_change_pct"),
                "median_htf_ltf_quote_top1_share": _median_column(subset, "htf_ltf_quote_top1_share"),
                "uses_future_label_as_entry_filter": False,
                "status": _candidate_rule_status(events, float(clean.mean()) if events else float("nan")),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _score_trade_rules(trades: pd.DataFrame, *, scope: str) -> pd.DataFrame:
    columns = [
        "scope",
        "rule",
        "signals",
        "closed_trades",
        "symbols",
        "win_rate",
        "avg_net_return",
        "median_net_return",
        "sum_net_return",
        "avg_mfe_pct",
        "avg_mae_pct",
        "runner_10pct_label_share",
        "clean_runner_label_share",
        "top20pct_sum_net_return",
        "top20pct_positive_share",
        "top20pct_trade_count",
        "balance_score_0_100",
        "status",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    all_rows = pd.Series(True, index=trades.index)
    dormancy_smooth = _bool_series(trades, "htf_anomaly_gate") & _bool_series(trades, "dormancy_ok") & _bool_series(trades, "smooth_price_growth_ok")
    oi_growth = _actual_oi_growth_mask(trades)
    sustained_ltf = _bool_series(trades, "htf_ltf_sustained_flow_ok")
    low_risk = _numeric_series(trades, "initial_risk_pct").le(0.025)
    strong_acceptance = (
        _numeric_series(trades, "ltf_quote_pace_ratio").ge(5.0)
        & _numeric_series(trades, "ltf_trade_pace_ratio").ge(5.0)
        & _numeric_series(trades, "ltf_second_half_return_pct").ge(0.0)
    )
    taker_share = _numeric_series(trades, "ltf_taker_buy_quote_share")
    buyer_confirmed = taker_share.ge(0.55) & taker_share.notna()
    balanced = dormancy_smooth & sustained_ltf & low_risk & strong_acceptance
    rules: list[tuple[str, pd.Series]] = [
        ("all_selected", all_rows),
        ("dormant_smooth_price", dormancy_smooth),
        ("dormant_smooth_price_oi", dormancy_smooth & oi_growth),
        ("sustained_internal_ltf_flow", sustained_ltf),
        ("dormant_smooth_sustained_flow", dormancy_smooth & sustained_ltf),
        ("low_initial_risk_le_2p5pct", low_risk),
        ("ltf_strong_acceptance", strong_acceptance),
        ("ltf_buyer_confirmed", buyer_confirmed),
        ("balanced_runner_candidate", balanced),
        ("balanced_runner_with_oi", balanced & oi_growth),
    ]

    rows: list[dict[str, object]] = []
    for rule, mask in rules:
        subset = trades.loc[mask.fillna(False)].copy()
        closed = subset.loc[subset.get("status", pd.Series(index=subset.index, dtype=object)).eq("closed")].copy()
        net = _numeric_series(closed, "net_return").dropna()
        top = _top20_metrics(net)
        closed_count = int(len(closed))
        win_rate = float((net > 0).mean()) if not net.empty else float("nan")
        avg_net = float(net.mean()) if not net.empty else float("nan")
        median_net = float(net.median()) if not net.empty else float("nan")
        sum_net = float(net.sum()) if not net.empty else float("nan")
        runner_share = float(_bool_series(closed, "runner_10pct_next_hour").mean()) if closed_count else float("nan")
        clean_share = float(_bool_series(closed, "clean_runner_without_low_break").mean()) if closed_count else float("nan")
        score = _balance_score(
            closed_trades=closed_count,
            win_rate=win_rate,
            avg_net_return=avg_net,
            median_net_return=median_net,
            sum_net_return=sum_net,
            top20pct_positive_share=top["top20pct_positive_share"],
        )
        rows.append(
            {
                "scope": scope,
                "rule": rule,
                "signals": int(len(subset)),
                "closed_trades": closed_count,
                "symbols": int(closed["symbol"].nunique()) if "symbol" in closed.columns and closed_count else 0,
                "win_rate": win_rate,
                "avg_net_return": avg_net,
                "median_net_return": median_net,
                "sum_net_return": sum_net,
                "avg_mfe_pct": _mean_column(closed, "mfe_pct"),
                "avg_mae_pct": _mean_column(closed, "mae_pct"),
                "runner_10pct_label_share": runner_share,
                "clean_runner_label_share": clean_share,
                **top,
                "balance_score_0_100": score,
                "status": _trade_rule_status(closed_count, avg_net, median_net, sum_net, top["top20pct_positive_share"], score),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["balance_score_0_100", "closed_trades", "sum_net_return"],
        ascending=[False, False, False],
    )


def _score_entry_window_rules(
    trades: pd.DataFrame,
    *,
    scope: str,
    apply_same_symbol_filter: bool,
) -> pd.DataFrame:
    columns = [
        "scope",
        "rule",
        "signals",
        "closed_trades",
        "symbols",
        "win_rate",
        "avg_net_return",
        "median_net_return",
        "sum_net_return",
        "avg_mfe_pct",
        "avg_mae_pct",
        "runner_10pct_label_share",
        "clean_runner_label_share",
        "top20pct_sum_net_return",
        "top20pct_positive_share",
        "top20pct_trade_count",
        "balance_score_0_100",
        "status",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    all_rows = pd.Series(True, index=trades.index)
    no_decay_positive = (~_bool_series(trades, "ltf_quote_decay_under50")) & _numeric_series(trades, "ltf_confirm_return_pct").ge(0.0)
    sustain = _bool_series(trades, "window_volume_sustain_ok")
    above_prior = _bool_series(trades, "window_volume_sustain_above_prior_median_ok")
    above_prior_150 = _bool_series(trades, "window_volume_sustain_above_prior_150pct_ok")
    oi_nonnegative = _bool_series(trades, "window_oi_nonnegative_from_pregrowth_end")
    low_risk = _numeric_series(trades, "initial_risk_pct").le(0.05)
    strong_flow = _numeric_series(trades, "ltf_quote_pace_ratio").ge(5.0) & _numeric_series(trades, "ltf_trade_pace_ratio").ge(5.0)
    decay = _bool_series(trades, "ltf_quote_decay_under50")
    decay_negative = decay & _numeric_series(trades, "ltf_confirm_return_pct").lt(0.0)
    rules: list[tuple[str, pd.Series]] = [
        ("all_entry_windows", all_rows),
        ("no_decay_positive_price", no_decay_positive),
        ("volume_sustain", sustain),
        ("volume_sustain_above_prior_median", sustain & above_prior),
        ("volume_sustain_above_prior_150pct", sustain & above_prior_150),
        ("volume_sustain_oi_nonnegative", sustain & oi_nonnegative),
        ("strong_sustain_low_risk", sustain & strong_flow & low_risk),
        ("fader_decay_under50", decay),
        ("fader_decay_under50_negative_price", decay_negative),
    ]

    rows: list[dict[str, object]] = []
    for rule, mask in rules:
        subset_before_filter = trades.loc[mask.fillna(False)].copy()
        subset = _apply_same_symbol_overlap_filter(subset_before_filter) if apply_same_symbol_filter else subset_before_filter
        closed = subset.loc[subset.get("status", pd.Series(index=subset.index, dtype=object)).eq("closed")].copy()
        net = _numeric_series(closed, "net_return").dropna()
        top = _top20_metrics(net)
        closed_count = int(len(closed))
        win_rate = float((net > 0).mean()) if not net.empty else float("nan")
        avg_net = float(net.mean()) if not net.empty else float("nan")
        median_net = float(net.median()) if not net.empty else float("nan")
        sum_net = float(net.sum()) if not net.empty else float("nan")
        runner_share = float(_bool_series(closed, "runner_10pct_next_hour").mean()) if closed_count else float("nan")
        clean_share = float(_bool_series(closed, "clean_runner_without_low_break").mean()) if closed_count else float("nan")
        score = _balance_score(
            closed_trades=closed_count,
            win_rate=win_rate,
            avg_net_return=avg_net,
            median_net_return=median_net,
            sum_net_return=sum_net,
            top20pct_positive_share=top["top20pct_positive_share"],
        )
        rows.append(
            {
                "scope": scope,
                "rule": rule,
                "signals": int(len(subset_before_filter)),
                "closed_trades": closed_count,
                "symbols": int(closed["symbol"].nunique()) if "symbol" in closed.columns and closed_count else 0,
                "win_rate": win_rate,
                "avg_net_return": avg_net,
                "median_net_return": median_net,
                "sum_net_return": sum_net,
                "avg_mfe_pct": _mean_column(closed, "mfe_pct"),
                "avg_mae_pct": _mean_column(closed, "mae_pct"),
                "runner_10pct_label_share": runner_share,
                "clean_runner_label_share": clean_share,
                **top,
                "balance_score_0_100": score,
                "status": _trade_rule_status(closed_count, avg_net, median_net, sum_net, top["top20pct_positive_share"], score),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["balance_score_0_100", "closed_trades", "sum_net_return"],
        ascending=[False, False, False],
    )


def _research_shortlist(
    candidate_rule_scores: pd.DataFrame,
    live_trade_rule_scores: pd.DataFrame,
    *,
    entry_window_rule_scores_live_filtered: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if entry_window_rule_scores_live_filtered is not None and not entry_window_rule_scores_live_filtered.empty:
        viable_windows = entry_window_rule_scores_live_filtered.loc[
            entry_window_rule_scores_live_filtered["closed_trades"].fillna(0).astype(int) > 0
        ].copy()
        viable_windows = viable_windows.sort_values(
            ["balance_score_0_100", "closed_trades", "sum_net_return"],
            ascending=[False, False, False],
        ).head(8)
        for _, row in viable_windows.iterrows():
            rows.append(
                {
                    "source": "entry_window_trade_rule",
                    "rule": row["rule"],
                    "status": row["status"],
                    "closed_trades": row["closed_trades"],
                    "win_rate": row["win_rate"],
                    "avg_net_return": row["avg_net_return"],
                    "median_net_return": row["median_net_return"],
                    "sum_net_return": row["sum_net_return"],
                    "top20pct_positive_share": row["top20pct_positive_share"],
                    "balance_score_0_100": row["balance_score_0_100"],
                    "runner_10pct_share": row["runner_10pct_label_share"],
                    "clean_runner_share": row["clean_runner_label_share"],
                    "uses_future_label_as_entry_filter": False,
                    "next_action": "inspect fixed-window trades and validate on the other TF set before live logic",
                }
            )
    if not live_trade_rule_scores.empty:
        viable = live_trade_rule_scores.loc[live_trade_rule_scores["closed_trades"].fillna(0).astype(int) > 0].copy()
        viable = viable.sort_values(["balance_score_0_100", "closed_trades", "sum_net_return"], ascending=[False, False, False]).head(8)
        for _, row in viable.iterrows():
            rows.append(
                {
                    "source": "live_filtered_trade_rule",
                    "rule": row["rule"],
                    "status": row["status"],
                    "closed_trades": row["closed_trades"],
                    "win_rate": row["win_rate"],
                    "avg_net_return": row["avg_net_return"],
                    "median_net_return": row["median_net_return"],
                    "sum_net_return": row["sum_net_return"],
                    "top20pct_positive_share": row["top20pct_positive_share"],
                    "balance_score_0_100": row["balance_score_0_100"],
                    "runner_10pct_share": row["runner_10pct_label_share"],
                    "clean_runner_share": row["clean_runner_label_share"],
                    "uses_future_label_as_entry_filter": False,
                    "next_action": "inspect trades and rerun on another period before promoting to live logic",
                }
            )
    if not candidate_rule_scores.empty:
        candidates = candidate_rule_scores.loc[candidate_rule_scores["events"].fillna(0).astype(int) > 0].copy()
        candidates = candidates.sort_values(["clean_runner_share", "runner_10pct_share", "events"], ascending=[False, False, False]).head(5)
        for _, row in candidates.iterrows():
            rows.append(
                {
                    "source": "candidate_label_rule",
                    "rule": row["rule"],
                    "status": row["status"],
                    "closed_trades": float("nan"),
                    "win_rate": float("nan"),
                    "avg_net_return": float("nan"),
                    "median_net_return": float("nan"),
                    "sum_net_return": float("nan"),
                    "top20pct_positive_share": float("nan"),
                    "balance_score_0_100": float("nan"),
                    "runner_10pct_share": row["runner_10pct_share"],
                    "clean_runner_share": row["clean_runner_share"],
                    "uses_future_label_as_entry_filter": False,
                    "next_action": "use only as a nature label; do not use future runner labels as entry filters",
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "rank",
                "source",
                "rule",
                "status",
                "closed_trades",
                "win_rate",
                "avg_net_return",
                "median_net_return",
                "sum_net_return",
                "top20pct_positive_share",
                "balance_score_0_100",
                "runner_10pct_share",
                "clean_runner_share",
                "uses_future_label_as_entry_filter",
                "next_action",
            ]
        )
    frame.insert(0, "rank", np.arange(1, len(frame) + 1))
    return frame


def _setup_nature(
    *,
    anomaly_gate: bool,
    dormancy_ok: bool,
    smooth_price_ok: bool,
    oi_ok: bool,
    quote_ratio: float,
    trade_ratio: float,
) -> str:
    if not anomaly_gate:
        return "not_htf_anomaly"
    if dormancy_ok and smooth_price_ok and oi_ok:
        return "dormant_smooth_price_oi_wakeup"
    if dormancy_ok and smooth_price_ok:
        return "dormant_smooth_price_oi_missing_or_down"
    if quote_ratio >= 20.0 and trade_ratio >= 10.0 and not smooth_price_ok:
        return "violent_flow_spike_no_smooth_pregrowth"
    return "generic_htf_flow_wakeup"


def _load_frame(
    storage: ParquetStorage,
    symbol: str,
    timeframe: str,
    *,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    try:
        result = storage.load_window_result(
            symbol,
            Timeframe(str(timeframe)),
            start_timestamp_ms=int(start_ms),
            end_timestamp_ms=int(end_ms),
        )
    except Exception:
        return pd.DataFrame()
    if not result.ok or result.frame.empty:
        return pd.DataFrame()
    frame = result.frame.copy()
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).copy()
    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _load_oi_frame(
    storage: ParquetStorage,
    symbol: str,
    *,
    config: HtfLtfRunnerDiscoveryConfig,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    frame = _load_frame(storage, symbol, "5m", start_ms=start_ms - 24 * 60 * 60 * 1000, end_ms=end_ms)
    if frame.empty or "open_interest" not in frame.columns:
        return pd.DataFrame()
    oi = frame.loc[frame["open_interest"].notna(), ["timestamp", "open_interest"]].copy()
    if oi.empty:
        return pd.DataFrame()
    oi["available_timestamp_ms"] = oi["timestamp"].astype("int64") + _timeframe_ms("5m")
    return oi.reset_index(drop=True)


def _oi_asof(oi: pd.DataFrame, decision_ms: int) -> dict[str, object]:
    if oi.empty or "available_timestamp_ms" not in oi.columns:
        return {"status": "missing", "timestamp_ms": float("nan"), "available_timestamp_ms": float("nan"), "open_interest": float("nan")}
    available = pd.to_numeric(oi["available_timestamp_ms"], errors="coerce")
    rows = oi.loc[available <= int(decision_ms)]
    if rows.empty:
        return {"status": "missing", "timestamp_ms": float("nan"), "available_timestamp_ms": float("nan"), "open_interest": float("nan")}
    row = rows.iloc[-1]
    return {
        "status": "ok",
        "timestamp_ms": int(row["timestamp"]),
        "available_timestamp_ms": int(row["available_timestamp_ms"]),
        "open_interest": float(row["open_interest"]),
    }


def _data_quality_row(
    *,
    symbol: str,
    htf: pd.DataFrame,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "htf_rows": int(len(htf)),
        "ltf_rows": int(len(ltf)),
        "oi_rows": int(len(oi)),
        "htf_quote_volume_source": "quote_volume" if "quote_volume" in htf.columns else "missing",
        "htf_trade_count_source": "number_of_trades" if "number_of_trades" in htf.columns else "missing",
        "ltf_quote_volume_source": "quote_volume" if "quote_volume" in ltf.columns else "missing",
        "ltf_trade_count_source": "number_of_trades" if "number_of_trades" in ltf.columns else "missing",
        "taker_buy_quote_source": "taker_buy_quote_volume" if "taker_buy_quote_volume" in ltf.columns else "missing",
        "oi_source": "cached_5m_open_interest" if not oi.empty else "missing",
    }


def _resolve_symbols(cache_dir: Path, timeframe: str, symbols: Iterable[str] | None) -> list[str]:
    if symbols:
        return sorted({_canonical_futures_symbol(str(symbol)) for symbol in symbols if str(symbol).strip()})
    result: list[str] = []
    for path in sorted(cache_dir.iterdir()) if cache_dir.exists() else []:
        if not path.is_dir():
            continue
        if (path / timeframe / "data.parquet").exists():
            result.append(ParquetStorage.decode_symbol_from_path(path.name))
    return result


def _canonical_futures_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if not normalized:
        return ""
    if ":" in normalized:
        return normalized
    if "/" in normalized:
        return f"{normalized}:USDT" if normalized.endswith("/USDT") else normalized
    if normalized.endswith("USDT") and len(normalized) > 4:
        return f"{normalized[:-4]}/USDT:USDT"
    return normalized


def _resolve_end_timestamp_ms(
    config: HtfLtfRunnerDiscoveryConfig,
    storage: ParquetStorage,
    symbols: tuple[str, ...],
) -> int:
    if config.end_timestamp_ms is not None:
        return int(config.end_timestamp_ms)
    max_ts = 0
    timeframe = Timeframe(str(config.htf_timeframe))
    for symbol in symbols:
        try:
            last_ts = storage.get_last_timestamp(symbol, timeframe)
        except Exception:
            last_ts = None
        if last_ts is not None:
            max_ts = max(max_ts, int(last_ts))
    if max_ts <= 0:
        raise ValueError("no cached HTF data found")
    return max_ts


def _numeric_column(frame: pd.DataFrame, column: str, *, fallback: pd.Series | None = None) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    if fallback is not None:
        return pd.to_numeric(fallback, errors="coerce")
    return pd.Series(np.nan, index=frame.index)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce").fillna(0).ne(0)
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "t", "yes", "y", "ok"})


def _actual_oi_growth_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    status = frame["pregrowth_oi_status"].astype(str).str.lower().eq("ok") if "pregrowth_oi_status" in frame.columns else pd.Series(False, index=frame.index)
    change = _numeric_series(frame, "pregrowth_oi_change_pct").ge(0.0)
    return status & change


def _median_column(frame: pd.DataFrame, column: str) -> float:
    values = _numeric_series(frame, column).dropna()
    return float(values.median()) if not values.empty else float("nan")


def _mean_column(frame: pd.DataFrame, column: str) -> float:
    values = _numeric_series(frame, column).dropna()
    return float(values.mean()) if not values.empty else float("nan")


def _candidate_rule_status(events: int, clean_runner_share: float) -> str:
    if events <= 0:
        return "no_events"
    if events < 20:
        return "low_sample_research_only"
    if np.isfinite(clean_runner_share) and clean_runner_share >= 0.20:
        return "interesting_label_rule_research_only"
    return "watch_research_only"


def _top20_metrics(net_returns: pd.Series) -> dict[str, object]:
    net = pd.to_numeric(net_returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().sort_values(ascending=False)
    if net.empty:
        return {
            "top20pct_sum_net_return": float("nan"),
            "top20pct_positive_share": float("nan"),
            "top20pct_trade_count": 0,
        }
    top_n = max(1, int(math.ceil(len(net) * 0.20)))
    top = net.head(top_n)
    positive_total = float(net.loc[net > 0].sum())
    top_positive = float(top.loc[top > 0].sum())
    return {
        "top20pct_sum_net_return": float(top.sum()),
        "top20pct_positive_share": _safe_divide(top_positive, positive_total) if positive_total > 0 else float("nan"),
        "top20pct_trade_count": int(top_n),
    }


def _clip_score(value: float, low: float, high: float) -> float:
    if not np.isfinite(value) or high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _balance_score(
    *,
    closed_trades: int,
    win_rate: float,
    avg_net_return: float,
    median_net_return: float,
    sum_net_return: float,
    top20pct_positive_share: float,
) -> float:
    if closed_trades <= 0:
        return 0.0
    sample_score = min(1.0, closed_trades / 100.0) * 18.0
    win_score = _clip_score(win_rate, 0.45, 0.62) * 22.0
    avg_score = _clip_score(avg_net_return, 0.0, 0.006) * 20.0
    median_score = _clip_score(median_net_return, -0.001, 0.004) * 20.0
    total_score = 10.0 if np.isfinite(sum_net_return) and sum_net_return > 0.0 else 0.0
    dependency_score = _clip_score(0.75 - top20pct_positive_share, 0.0, 0.45) * 10.0
    return round(sample_score + win_score + avg_score + median_score + total_score + dependency_score, 2)


def _trade_rule_status(
    closed_trades: int,
    avg_net_return: float,
    median_net_return: float,
    sum_net_return: float,
    top20pct_positive_share: float,
    balance_score: float,
) -> str:
    if closed_trades <= 0:
        return "no_closed_trades"
    if closed_trades < 30:
        return "low_sample"
    if not np.isfinite(sum_net_return) or sum_net_return <= 0.0 or not np.isfinite(avg_net_return) or avg_net_return <= 0.0:
        return "fail_negative_expectancy"
    if np.isfinite(top20pct_positive_share) and top20pct_positive_share > 0.65:
        return "watch_top20_dependency"
    if np.isfinite(median_net_return) and median_net_return <= 0.0:
        return "watch_negative_median"
    if balance_score >= 60.0:
        return "interesting_research_only"
    return "watch_research_only"


def _positive_median(values: pd.Series) -> float:
    series = pd.to_numeric(values, errors="coerce")
    series = series.loc[series > 0].dropna()
    return float(series.median()) if not series.empty else float("nan")


def _safe_divide(numerator: float, denominator: float) -> float:
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0.0:
        return float("nan")
    return numerator / denominator


def _strict_ltf_window(
    frame: pd.DataFrame,
    *,
    start_ms: int,
    end_exclusive_ms: int,
    expected_step_ms: int,
) -> _StrictLtfWindow:
    step_ms = int(expected_step_ms)
    if step_ms <= 0:
        raise ValueError("expected_step_ms must be > 0")
    start = int(start_ms)
    end = int(end_exclusive_ms)
    if end <= start:
        raise ValueError("end_exclusive_ms must be > start_ms")
    expected_candles = max(1, int(math.ceil((end - start) / step_ms)))
    empty = frame.iloc[0:0].copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if frame.empty or "timestamp" not in frame.columns:
        return _StrictLtfWindow(
            frame=empty,
            status="no_ltf_candles",
            start_ms=start,
            end_exclusive_ms=end,
            expected_step_ms=step_ms,
            expected_candles=expected_candles,
            observed_candles=0,
            first_gap_start_ms=start,
            first_gap_end_ms=None,
            max_gap_ms=0,
        )
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
    selected = frame.loc[timestamps.ge(start) & timestamps.lt(end)].copy()
    selected["timestamp"] = pd.to_numeric(selected["timestamp"], errors="coerce")
    selected = selected.dropna(subset=["timestamp"]).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    if selected.empty:
        return _StrictLtfWindow(
            frame=selected,
            status="no_ltf_candles",
            start_ms=start,
            end_exclusive_ms=end,
            expected_step_ms=step_ms,
            expected_candles=expected_candles,
            observed_candles=0,
            first_gap_start_ms=start,
            first_gap_end_ms=None,
            max_gap_ms=0,
        )

    expected_ts = start
    prefix_positions: list[int] = []
    previous_ts: int | None = None
    max_gap_ms = 0
    first_gap_start: int | None = None
    first_gap_end: int | None = None
    for position, (_, row) in enumerate(selected.iterrows()):
        candle_ts = int(row["timestamp"])
        if candle_ts != expected_ts:
            first_gap_start = expected_ts
            first_gap_end = candle_ts
            max_gap_ms = abs(candle_ts - expected_ts) if previous_ts is None else max(max_gap_ms, candle_ts - previous_ts)
            break
        prefix_positions.append(position)
        previous_ts = candle_ts
        expected_ts += step_ms
        if expected_ts >= end:
            break

    if prefix_positions:
        prefix = selected.iloc[prefix_positions].copy().reset_index(drop=True)
    else:
        prefix = selected.iloc[0:0].copy().reset_index(drop=True)
    observed = int(len(prefix))
    if observed >= expected_candles:
        status = "ok"
    elif first_gap_start is not None:
        status = "ltf_gap"
    elif observed == 0:
        status = "first_ltf_candle_missing"
    else:
        status = "incomplete_ltf_window"
        first_gap_start = expected_ts
        first_gap_end = None
    return _StrictLtfWindow(
        frame=prefix,
        status=status,
        start_ms=start,
        end_exclusive_ms=end,
        expected_step_ms=step_ms,
        expected_candles=expected_candles,
        observed_candles=observed,
        first_gap_start_ms=first_gap_start,
        first_gap_end_ms=first_gap_end,
        max_gap_ms=int(max_gap_ms),
    )


def _strict_ltf_window_audit(window: _StrictLtfWindow, *, prefix: str) -> dict[str, object]:
    frame = window.frame
    first_ts = int(frame.iloc[0]["timestamp"]) if not frame.empty else float("nan")
    last_ts = int(frame.iloc[-1]["timestamp"]) if not frame.empty else float("nan")
    return {
        f"{prefix}_path_status": window.status,
        f"{prefix}_expected_step_ms": int(window.expected_step_ms),
        f"{prefix}_start_ms": int(window.start_ms),
        f"{prefix}_end_exclusive_ms": int(window.end_exclusive_ms),
        f"{prefix}_expected_candles": int(window.expected_candles),
        f"{prefix}_observed_continuous_candles": int(window.observed_candles),
        f"{prefix}_first_timestamp_ms": first_ts,
        f"{prefix}_last_timestamp_ms": last_ts,
        f"{prefix}_first_gap_start_ms": window.first_gap_start_ms if window.first_gap_start_ms is not None else float("nan"),
        f"{prefix}_first_gap_end_ms": window.first_gap_end_ms if window.first_gap_end_ms is not None else float("nan"),
        f"{prefix}_max_gap_ms": int(window.max_gap_ms),
    }


def _entry_window_unavailable_reason(window: _StrictLtfWindow, *, required_candles: int) -> str:
    if window.observed_candles >= int(required_candles):
        return ""
    if window.status == "ltf_gap":
        return "entry_ltf_gap_before_next_open"
    if window.status == "first_ltf_candle_missing":
        return "entry_ltf_first_candle_missing"
    if window.status == "no_ltf_candles":
        return "no_entry_ltf_candles"
    return "insufficient_ltf_for_next_open"


def _post_entry_window_skip_reason(window: _StrictLtfWindow) -> str:
    if window.status == "ltf_gap":
        return "post_entry_ltf_gap_before_exit"
    if window.status == "first_ltf_candle_missing":
        return "post_entry_first_ltf_candle_missing"
    if window.status == "no_ltf_candles":
        return "no_post_entry_ltf_candles"
    if window.status == "incomplete_ltf_window":
        return "post_entry_ltf_window_incomplete"
    return "post_entry_ltf_path_invalid"


def _future_label_missing_status(window: _StrictLtfWindow) -> str:
    if window.status == "ltf_gap":
        return "missing_ltf_future_gap"
    if window.status == "first_ltf_candle_missing":
        return "missing_ltf_future_first_candle"
    if window.status == "no_ltf_candles":
        return "missing_ltf_future_window"
    if window.status == "incomplete_ltf_window":
        return "missing_ltf_future_window_incomplete"
    return "missing_ltf_future_window"


def _timeframe_ms(value: str) -> int:
    return Timeframe(str(value)).to_milliseconds()


def _infer_step_ms(frame: pd.DataFrame) -> int:
    if len(frame) < 2:
        return 1
    diffs = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().diff().dropna()
    if diffs.empty:
        return 1
    return max(1, int(diffs.median()))


def _timestamp_to_utc(timestamp_ms: object) -> str:
    try:
        parsed = int(float(timestamp_ms))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(parsed):
        return ""
    return pd.to_datetime(parsed, unit="ms", utc=True).isoformat()


def _format_duration(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "n/a"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _effective_symbol_workers(value: object, *, total_items: int) -> int:
    if total_items <= 1:
        return 1
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = 1
    if requested <= 1:
        return 1
    cpu_count = os.cpu_count() or 1
    return max(1, min(int(requested), int(total_items), max(1, int(cpu_count)), 8))


def _entry_window_counts(config: HtfLtfRunnerDiscoveryConfig) -> tuple[int, ...]:
    start = int(config.ltf_min_confirm_candles)
    end = int(config.ltf_max_confirm_candles)
    if start <= 0 or end < start:
        return ()
    counts = {start, end}
    value = start
    while value + start <= end:
        value += start
        counts.add(value)
    return tuple(sorted(counts))


def _apply_same_symbol_overlap_filter(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return trades.copy()
    closed = trades.copy()
    closed["_order"] = np.arange(len(closed))
    closed["entry_timestamp_ms"] = pd.to_numeric(closed["entry_timestamp_ms"], errors="coerce")
    closed["exit_timestamp_ms"] = pd.to_numeric(closed["exit_timestamp_ms"], errors="coerce")
    closed.sort_values(["entry_timestamp_ms", "decision_timestamp_ms", "symbol", "_order"], inplace=True)
    open_positions: list[tuple[str, int]] = []
    rows: list[pd.Series] = []
    for _, row in closed.iterrows():
        if row.get("status") != "closed":
            rows.append(row)
            continue
        entry_ts = int(row["entry_timestamp_ms"])
        exit_ts = int(row["exit_timestamp_ms"])
        symbol = str(row["symbol"])
        open_positions = [(s, e) for s, e in open_positions if e >= entry_ts]
        skip_reason = ""
        if any(s == symbol for s, _ in open_positions):
            skip_reason = "same_symbol_overlap_position_at_entry"
        if skip_reason:
            skipped = row.copy()
            skipped["status"] = "skipped"
            skipped["skip_reason"] = skip_reason
            skipped["execution_guard"] = False
            skipped["would_have_net_return"] = row.get("net_return", float("nan"))
            rows.append(skipped)
        else:
            kept = row.copy()
            kept["parallel_other_symbol_positions_at_entry"] = sum(1 for s, _ in open_positions if s != symbol)
            rows.append(kept)
            open_positions.append((symbol, exit_ts))
    result = pd.DataFrame(rows).sort_values("_order").drop(columns=["_order"], errors="ignore")
    return result.reset_index(drop=True)


def _summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}, {"metric": "skipped_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    rows.extend(
        [
            {"metric": "closed_trades", "value": int(len(closed))},
            {"metric": "skipped_trades", "value": int(len(skipped))},
        ]
    )
    if closed.empty:
        return pd.DataFrame(rows)
    net = pd.to_numeric(closed["net_return"], errors="coerce")
    rows.extend(
        [
            {"metric": "symbols", "value": int(closed["symbol"].nunique())},
            {"metric": "win_rate", "value": float((net > 0).mean())},
            {"metric": "avg_net_return", "value": float(net.mean())},
            {"metric": "median_net_return", "value": float(net.median())},
            {"metric": "sum_net_return", "value": float(net.sum())},
            {"metric": "avg_gross_r", "value": float(pd.to_numeric(closed["gross_r"], errors="coerce").mean())},
            {"metric": "median_gross_r", "value": float(pd.to_numeric(closed["gross_r"], errors="coerce").median())},
            {"metric": "avg_mfe_pct", "value": float(pd.to_numeric(closed["mfe_pct"], errors="coerce").mean())},
            {"metric": "avg_mae_pct", "value": float(pd.to_numeric(closed["mae_pct"], errors="coerce").mean())},
            {"metric": "runner_10pct_label_share", "value": float(closed["runner_10pct_next_hour"].astype(bool).mean()) if "runner_10pct_next_hour" in closed.columns else 0.0},
            {"metric": "clean_runner_label_share", "value": float(closed["clean_runner_without_low_break"].astype(bool).mean()) if "clean_runner_without_low_break" in closed.columns else 0.0},
        ]
    )
    for reason, count in closed["exit_reason"].astype(str).value_counts().items():
        rows.append({"metric": f"exit_reason:{reason}", "value": int(count)})
    return pd.DataFrame(rows)


def _metric_map(summary: pd.DataFrame) -> dict[str, object]:
    if summary.empty:
        return {}
    return {str(row["metric"]): row["value"] for _, row in summary.iterrows()}


def _summarize_by_column(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    if trades.empty or column not in trades.columns or "status" not in trades.columns:
        return pd.DataFrame(columns=[column, "closed_trades", "win_rate", "avg_net_return", "sum_net_return"])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=[column, "closed_trades", "win_rate", "avg_net_return", "sum_net_return"])
    closed["net_return"] = pd.to_numeric(closed["net_return"], errors="coerce")
    grouped = closed.assign(win=closed["net_return"] > 0).groupby(column, dropna=False).agg(
        closed_trades=("net_return", "size"),
        win_rate=("win", "mean"),
        avg_net_return=("net_return", "mean"),
        median_net_return=("net_return", "median"),
        sum_net_return=("net_return", "sum"),
        runner_10pct_label_share=("runner_10pct_next_hour", "mean"),
        clean_runner_label_share=("clean_runner_without_low_break", "mean"),
    )
    return grouped.reset_index().sort_values(["sum_net_return", "closed_trades"], ascending=[False, False])


def _daily_summary(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "entry_day_utc",
        "closed_trades",
        "symbols",
        "win_rate",
        "avg_net_return",
        "median_net_return",
        "sum_net_return",
        "avg_mfe_pct",
        "avg_mae_pct",
        "runner_10pct_label_share",
        "clean_runner_label_share",
        "top_trade_net_return",
        "bottom_trade_net_return",
    ]
    if trades.empty or "status" not in trades.columns or "entry_timestamp_utc" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["entry_day_utc"] = pd.to_datetime(closed["entry_timestamp_utc"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    closed = closed.loc[closed["entry_day_utc"].notna()].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["net_return"] = pd.to_numeric(closed["net_return"], errors="coerce")
    closed["mfe_pct"] = pd.to_numeric(closed.get("mfe_pct", pd.Series(index=closed.index, dtype=float)), errors="coerce")
    closed["mae_pct"] = pd.to_numeric(closed.get("mae_pct", pd.Series(index=closed.index, dtype=float)), errors="coerce")
    closed["_win"] = closed["net_return"] > 0
    if "runner_10pct_next_hour" not in closed.columns:
        closed["runner_10pct_next_hour"] = False
    if "clean_runner_without_low_break" not in closed.columns:
        closed["clean_runner_without_low_break"] = False
    grouped = closed.groupby("entry_day_utc", dropna=False).agg(
        closed_trades=("net_return", "size"),
        symbols=("symbol", "nunique"),
        win_rate=("_win", "mean"),
        avg_net_return=("net_return", "mean"),
        median_net_return=("net_return", "median"),
        sum_net_return=("net_return", "sum"),
        avg_mfe_pct=("mfe_pct", "mean"),
        avg_mae_pct=("mae_pct", "mean"),
        runner_10pct_label_share=("runner_10pct_next_hour", "mean"),
        clean_runner_label_share=("clean_runner_without_low_break", "mean"),
        top_trade_net_return=("net_return", "max"),
        bottom_trade_net_return=("net_return", "min"),
    )
    return grouped.reset_index().sort_values("entry_day_utc").loc[:, columns]


def _label_distribution(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["label", "count"])
    rows = []
    for column in ("runner_10pct_next_hour", "clean_runner_without_low_break", "anomaly_low_broken_before_runner", "setup_nature"):
        if column not in candidates.columns:
            continue
        for value, count in candidates[column].value_counts(dropna=False).items():
            rows.append({"label": column, "value": value, "count": int(count), "share": float(count / len(candidates))})
    return pd.DataFrame(rows)


def _build_funnel(
    candidates: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    scanned_htf_rows: int,
    entry_windows: pd.DataFrame,
    entry_window_trades: pd.DataFrame,
) -> pd.DataFrame:
    rows = [
        {"stage": "htf_rows_after_scan", "count": int(scanned_htf_rows)},
        {"stage": "htf_rows_rejected_before_artifact", "count": int(max(0, scanned_htf_rows - len(candidates)))},
        {"stage": "htf_anomaly_gate_ok", "count": int(candidates["htf_anomaly_gate"].astype(bool).sum()) if "htf_anomaly_gate" in candidates.columns else 0},
        {"stage": "runner_10pct_next_hour_labels", "count": int(candidates["runner_10pct_next_hour"].astype(bool).sum()) if "runner_10pct_next_hour" in candidates.columns else 0},
        {"stage": "clean_runner_without_low_break_labels", "count": int(candidates["clean_runner_without_low_break"].astype(bool).sum()) if "clean_runner_without_low_break" in candidates.columns else 0},
        {"stage": "entry_window_rows", "count": int(len(entry_windows))},
        {"stage": "entry_window_execution_ok", "count": int(entry_windows["window_execution_ok"].astype(bool).sum()) if "window_execution_ok" in entry_windows.columns else 0},
        {"stage": "entry_window_closed_trades", "count": int(entry_window_trades["status"].eq("closed").sum()) if "status" in entry_window_trades.columns else 0},
        {"stage": "ltf_signals_selected", "count": int(len(signals))},
        {"stage": "closed_trades", "count": int(trades["status"].eq("closed").sum()) if "status" in trades.columns else 0},
        {"stage": "skipped_trades", "count": int(trades["status"].eq("skipped").sum()) if "status" in trades.columns else 0},
    ]
    return pd.DataFrame(rows)


def _skip_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "skip_reason" not in trades.columns:
        return pd.DataFrame(columns=["skip_reason", "count"])
    skipped = trades.loc[trades["status"].eq("skipped")]
    if skipped.empty:
        return pd.DataFrame(columns=["skip_reason", "count"])
    return skipped["skip_reason"].value_counts().rename_axis("skip_reason").reset_index(name="count")


def _top_dependency(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=["scope", "closed_trades", "sum_net_return", "top20pct_positive_share"])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=["scope", "closed_trades", "sum_net_return", "top20pct_positive_share"])
    net = pd.to_numeric(closed["net_return"], errors="coerce").dropna().sort_values(ascending=False)
    positive = net.loc[net > 0]
    top_n = max(1, int(math.ceil(len(net) * 0.20)))
    positive_total = float(positive.sum())
    return pd.DataFrame(
        [
            {
                "scope": "all_closed",
                "closed_trades": int(len(net)),
                "sum_net_return": float(net.sum()),
                "top20pct_sum_net_return": float(net.head(top_n).sum()),
                "top20pct_positive_share": float(net.head(top_n).loc[net.head(top_n) > 0].sum() / positive_total) if positive_total > 0 else float("inf"),
                "top20pct_trade_count": int(top_n),
            }
        ]
    )


def _honesty_report(config: HtfLtfRunnerDiscoveryConfig) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "check": "future_label_separation",
                "status": "ok",
                "detail": "runner_10pct_next_hour and low-break labels are written after the event scan, are not used to select entry, and require a strict continuous LTF path until runner hit or full horizon.",
            },
            {
                "check": "entry_availability",
                "status": "ok",
                "detail": "entry is the next LTF open after the closed confirmation candle; confirmation and next-open entry require a continuous LTF path with no gap hops.",
            },
            {
                "check": "exit_model",
                "status": "ok",
                "detail": "no TP is simulated; exits are structural stop, structural trailing stop, or max-hold wall-clock time exit. If the post-entry LTF path has a gap before exit, the trade is skipped rather than carried across missing time.",
            },
            {
                "check": "ltf_continuity",
                "status": "ok",
                "detail": "LTF confirmation, future labels and post-entry replay use configured timeframe steps; missing candles create explicit missing/gap statuses instead of sparse-row simulation.",
            },
            {
                "check": "oi_availability",
                "status": "ok",
                "detail": "OI is used only from cached 5m rows whose timestamp plus 5m availability is <= decision time.",
            },
            {
                "check": "costs",
                "status": "ok",
                "detail": f"entry_slippage={config.entry_slippage_pct}; exit_slippage={config.exit_slippage_pct}; round_trip_fee={2 * config.fee_rate}.",
            },
            {
                "check": "data_access",
                "status": "ok",
                "detail": "The tool reads Parquet cache through ParquetStorage only; it does not download exchange candles or seconds data during the backtest and does not synthesize quote-volume from close*volume for flow logic.",
            },
        ]
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run HTF/LTF runner discovery backtest.")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", default=str(Path(DEFAULT_RESULTS_DIR) / "htf_ltf_runner_discovery"))
    parser.add_argument("--htf-timeframe", default="1m")
    parser.add_argument("--ltf-timeframe", default="5s")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--baseline-candles", type=int, default=60)
    parser.add_argument("--dormancy-candles", type=int, default=30)
    parser.add_argument("--pregrowth-candles", type=int, default=5)
    parser.add_argument("--runner-target-return-pct", type=float, default=0.10)
    parser.add_argument("--runner-horizon-minutes", type=int, default=60)
    parser.add_argument("--min-htf-quote-ratio", type=float, default=5.0)
    parser.add_argument("--min-htf-trade-ratio", type=float, default=5.0)
    parser.add_argument("--min-htf-return-pct", type=float, default=0.010)
    parser.add_argument("--min-dormancy-to-anomaly-quote-ratio", type=float, default=6.0)
    parser.add_argument("--min-dormancy-to-anomaly-trade-ratio", type=float, default=5.0)
    parser.add_argument("--max-dormancy-range-pct-median", type=float, default=0.004)
    parser.add_argument("--min-pregrowth-return-pct", type=float, default=0.002)
    parser.add_argument("--max-pregrowth-single-candle-return-pct", type=float, default=0.020)
    parser.add_argument("--min-pregrowth-positive-step-share", type=float, default=0.55)
    parser.add_argument("--min-pregrowth-oi-change-pct", type=float, default=0.0)
    parser.add_argument("--require-pregrowth-oi", action="store_true")
    parser.add_argument("--ltf-min-confirm-candles", type=int, default=6)
    parser.add_argument("--ltf-max-confirm-candles", type=int, default=24)
    parser.add_argument("--min-ltf-confirm-return-pct", type=float, default=0.004)
    parser.add_argument("--min-ltf-quote-pace-ratio", type=float, default=3.0)
    parser.add_argument("--min-ltf-trade-pace-ratio", type=float, default=3.0)
    parser.add_argument("--min-ltf-taker-buy-share", type=float, default=None)
    parser.add_argument("--min-ltf-second-half-return-pct", type=float, default=0.0)
    parser.add_argument("--min-ltf-quote-acceleration", type=float, default=1.0)
    parser.add_argument("--min-ltf-trade-acceleration", type=float, default=1.0)
    parser.add_argument("--max-entry-drift-pct", type=float, default=0.004)
    parser.add_argument("--max-initial-risk-pct", type=float, default=0.05)
    parser.add_argument("--structural-stop-buffer-pct", type=float, default=0.0005)
    parser.add_argument("--trail-lookback-candles", type=int, default=6)
    parser.add_argument("--trail-buffer-pct", type=float, default=0.0005)
    parser.add_argument("--max-hold-candles", type=int, default=720)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--entry-slippage-pct", type=float, default=0.0005)
    parser.add_argument("--exit-slippage-pct", type=float, default=0.0005)
    parser.add_argument("--backtest-symbol-workers", type=int, default=1)
    args = parser.parse_args(argv)
    config = HtfLtfRunnerDiscoveryConfig(
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        htf_timeframe=str(args.htf_timeframe),
        ltf_timeframe=str(args.ltf_timeframe),
        days=int(args.days),
        end_timestamp_ms=args.end_timestamp_ms,
        baseline_candles=int(args.baseline_candles),
        dormancy_candles=int(args.dormancy_candles),
        pregrowth_candles=int(args.pregrowth_candles),
        min_htf_quote_ratio=float(args.min_htf_quote_ratio),
        min_htf_trade_ratio=float(args.min_htf_trade_ratio),
        min_htf_return_pct=float(args.min_htf_return_pct),
        min_dormancy_to_anomaly_quote_ratio=float(args.min_dormancy_to_anomaly_quote_ratio),
        min_dormancy_to_anomaly_trade_ratio=float(args.min_dormancy_to_anomaly_trade_ratio),
        max_dormancy_range_pct_median=float(args.max_dormancy_range_pct_median),
        min_pregrowth_return_pct=float(args.min_pregrowth_return_pct),
        max_pregrowth_single_candle_return_pct=float(args.max_pregrowth_single_candle_return_pct),
        min_pregrowth_positive_step_share=float(args.min_pregrowth_positive_step_share),
        min_pregrowth_oi_change_pct=float(args.min_pregrowth_oi_change_pct),
        require_pregrowth_oi=bool(args.require_pregrowth_oi),
        runner_target_return_pct=float(args.runner_target_return_pct),
        runner_horizon_minutes=int(args.runner_horizon_minutes),
        ltf_min_confirm_candles=int(args.ltf_min_confirm_candles),
        ltf_max_confirm_candles=int(args.ltf_max_confirm_candles),
        min_ltf_confirm_return_pct=float(args.min_ltf_confirm_return_pct),
        min_ltf_quote_pace_ratio=float(args.min_ltf_quote_pace_ratio),
        min_ltf_trade_pace_ratio=float(args.min_ltf_trade_pace_ratio),
        min_ltf_taker_buy_share=args.min_ltf_taker_buy_share,
        min_ltf_second_half_return_pct=float(args.min_ltf_second_half_return_pct),
        min_ltf_quote_acceleration=float(args.min_ltf_quote_acceleration),
        min_ltf_trade_acceleration=float(args.min_ltf_trade_acceleration),
        max_entry_drift_pct=float(args.max_entry_drift_pct),
        max_initial_risk_pct=float(args.max_initial_risk_pct),
        structural_stop_buffer_pct=float(args.structural_stop_buffer_pct),
        trail_lookback_candles=int(args.trail_lookback_candles),
        trail_buffer_pct=float(args.trail_buffer_pct),
        max_hold_candles=int(args.max_hold_candles),
        fee_rate=float(args.fee_rate),
        entry_slippage_pct=float(args.entry_slippage_pct),
        exit_slippage_pct=float(args.exit_slippage_pct),
        symbol_workers=int(args.backtest_symbol_workers),
    )
    run_htf_ltf_runner_discovery(config, symbols=args.symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
