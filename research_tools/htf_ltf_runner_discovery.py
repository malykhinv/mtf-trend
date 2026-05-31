"""HTF/LTF runner discovery for pump-awakening research.

The tool separates two jobs that are easy to mix up:

* future labels: did an HTF anomaly become a +10% runner within the next hour,
  and was the anomaly low broken before that happened;
* executable replay: would a live bot, using only closed LTF candles available
  at the decision time, enter, close 50% at TP1=0.75R, and manage the
  remainder with structural stop/trailing.

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
from research_tools.pump_decision_core import (
    match_rolling_categories,
    rolling_category_priority_rank,
)


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
    tp1_r: float = 0.75
    tp1_close_fraction: float = 0.5
    max_hold_candles: int = 720
    fee_rate: float = 0.0004
    entry_slippage_pct: float = 0.0005
    exit_slippage_pct: float = 0.0005
    symbol_workers: int = 1
    auto_targeted_ltf_backfill: bool = True
    targeted_backfill_min_htf_quote_ratio: float = 20.0
    targeted_backfill_min_htf_trade_ratio: float = 20.0
    targeted_backfill_min_htf_return_pct: float = 0.030
    targeted_backfill_min_htf_range_pct: float = 0.040
    targeted_backfill_min_dormancy_to_anomaly_quote_ratio: float = 14.0
    targeted_backfill_min_dormancy_to_anomaly_trade_ratio: float = 12.0
    targeted_backfill_max_dormancy_range_pct_median: float = 0.006
    targeted_backfill_min_abs_quote_volume: float = 50_000.0
    targeted_backfill_min_abs_number_of_trades: float = 150.0
    targeted_backfill_max_events_per_symbol: int = 0
    risk_per_trade_pct: float = 0.02
    max_total_open_risk_pct: float = 0.08
    targeted_pair_gate_use_category_necessary_bounds: bool = True

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
        for name in (
            "targeted_backfill_min_htf_quote_ratio",
            "targeted_backfill_min_htf_trade_ratio",
            "targeted_backfill_min_dormancy_to_anomaly_quote_ratio",
            "targeted_backfill_min_dormancy_to_anomaly_trade_ratio",
            "targeted_backfill_min_abs_quote_volume",
            "targeted_backfill_min_abs_number_of_trades",
        ):
            if float(getattr(self, name)) < 0.0:
                raise ValueError(f"{name} must be >= 0")
        if self.targeted_backfill_max_dormancy_range_pct_median < 0.0:
            raise ValueError("targeted_backfill_max_dormancy_range_pct_median must be >= 0")
        if self.risk_per_trade_pct <= 0.0:
            raise ValueError("risk_per_trade_pct must be > 0")
        if self.tp1_r <= 0.0:
            raise ValueError("tp1_r must be > 0")
        if not 0.0 < self.tp1_close_fraction <= 1.0:
            raise ValueError("tp1_close_fraction must be in (0, 1]")
        if self.max_total_open_risk_pct < self.risk_per_trade_pct:
            raise ValueError("max_total_open_risk_pct must be >= risk_per_trade_pct")



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
    pre_entry_seed_timestamps_by_symbol: dict[str, set[int]] = {}
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

        pre_entry_seed_timestamps_by_symbol = _targeted_ltf_seed_timestamps_by_symbol(pre_entry_plan)
        post_entry_plan, post_entry_windows_by_symbol = _build_targeted_ltf_post_entry_backfill_plan(
            storage=storage,
            symbols=selected_symbols,
            start_ms=start_ms,
            end_ms=end_ms,
            config=config,
            progress_label=f"{progress_label or 'runner discovery'} targeted post-entry plan",
            seed_timestamps_by_symbol=pre_entry_seed_timestamps_by_symbol,
        )
        post_entry_fetch, post_entry_materialize = _ensure_targeted_ltf_backfill(
            cache_dir=config.cache_dir,
            ltf_timeframe=config.ltf_timeframe,
            windows_by_symbol=post_entry_windows_by_symbol,
            progress_label=f"{progress_label or 'runner discovery'} targeted post-entry LTF",
            max_merged_span_ms=None,
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
                allowed_timestamps_ms=pre_entry_seed_timestamps_by_symbol.get(symbol) if _targeted_ltf_backfill_required(config) else None,
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
    trades_frame = _with_runner_candidate_categories(pd.DataFrame(trade_rows))
    live_filtered, portfolio_events = _apply_runner_candidate_portfolio(trades_frame, config=config)
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
    oos_runner_fader_hypothesis_trades = _strict_runner_fader_oos_hypothesis_trades(live_filtered)
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
        (config.output_dir / "htf_ltf_runner_portfolio_events.csv", portfolio_events),
        (config.output_dir / "htf_ltf_runner_oos_runner_fader_v1_trades_live_filtered.csv", oos_runner_fader_hypothesis_trades),
        (config.output_dir / "htf_ltf_runner_by_day.csv", _daily_summary(trades_frame)),
        (config.output_dir / "htf_ltf_runner_by_day_live_filtered.csv", _daily_summary(live_filtered)),
        (
            config.output_dir / "htf_ltf_runner_oos_runner_fader_v1_by_day_live_filtered.csv",
            _daily_summary(oos_runner_fader_hypothesis_trades),
        ),
        (config.output_dir / "htf_ltf_runner_profitability_summary.csv", _summarize_trades(trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_profitability_summary_live_filtered.csv",
            _summarize_trades(live_filtered),
        ),
        (
            config.output_dir / "htf_ltf_runner_oos_runner_fader_v1_summary_live_filtered.csv",
            _summarize_trades(oos_runner_fader_hypothesis_trades),
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
        (
            config.output_dir / "htf_ltf_runner_oos_runner_fader_v1_top_dependency_live_filtered.csv",
            _top_dependency(oos_runner_fader_hypothesis_trades),
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
                        "exit_model": "tp1_0p75r_close_50pct_then_structural_trailing",
                        "future_label_model": "separate_next_hour_10pct_label_not_used_for_entry",
                        "data_access_model": (
                            "rolling_htf_pair_superset_then_exact_ltf_seed_and_post_entry_replay"
                            if _targeted_ltf_backfill_required(config)
                            else "cache_only_rolling_htf_no_exchange_fetch"
                        ),
                        "seconds_download_required": bool(_targeted_ltf_backfill_required(config)),
                        "targeted_ltf_plan_rows": int(len(targeted_ltf_plan)),
                        "targeted_ltf_fetch_rows": int(len(targeted_ltf_fetch)),
                        "targeted_ltf_materialize_rows": int(len(targeted_ltf_materialize)),
                        "runtime_seconds": round(time.monotonic() - started_at, 3),
                        "scanned_htf_rows": scanned_htf_rows,
                        "candidate_artifact_scope": "rolling_htf_seed_gate_only",
                        "portfolio_model": "fixed_priority_C_A_S_with_total_risk_cap_and_symbol_cooldown",
                        "risk_per_trade_pct": float(config.risk_per_trade_pct),
                        "max_total_open_risk_pct": float(config.max_total_open_risk_pct),
                        "symbol_cooldown_ms": int(_timeframe_ms(config.htf_timeframe)),
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
        "window_model": "two_adjacent_htf_candles_only_then_exact_ltf_rolling_seed_check",
        "selection_model": "two_closed_htf_candle_pair_upper_bound_safe_superset_data_loading_only",
        "seed_gate": _targeted_ltf_seed_gate_description(config),
        "pair_gate_model": "fetch_pair_unless_exact_rolling_seed_is_mathematically_impossible",
        "pair_gate_uses_only_upper_bounds": True,
        "pair_gate_uses_category_necessary_bounds": bool(config.targeted_pair_gate_use_category_necessary_bounds),
        "pair_gate_trading_signal": False,
        "pair_gate_min_htf_quote_ratio": float(config.min_htf_quote_ratio),
        "pair_gate_min_htf_trade_ratio": float(config.min_htf_trade_ratio),
        "pair_gate_min_htf_return_pct": float(config.min_htf_return_pct),
        "pair_gate_min_abs_quote_volume": 0.0,
        "pair_gate_min_abs_number_of_trades": 0.0,
        "legacy_targeted_min_htf_quote_ratio_unused": float(config.targeted_backfill_min_htf_quote_ratio),
        "legacy_targeted_min_htf_trade_ratio_unused": float(config.targeted_backfill_min_htf_trade_ratio),
        "legacy_targeted_min_htf_return_pct_unused": float(config.targeted_backfill_min_htf_return_pct),
        "legacy_targeted_min_htf_range_pct_unused": float(config.targeted_backfill_min_htf_range_pct),
        "legacy_targeted_min_abs_quote_volume_unused": float(config.targeted_backfill_min_abs_quote_volume),
        "legacy_targeted_min_abs_number_of_trades_unused": float(config.targeted_backfill_min_abs_number_of_trades),
        "max_events_per_symbol": int(config.targeted_backfill_max_events_per_symbol),
    }
    if plan.empty:
        plan = pd.DataFrame([summary])
    else:
        plan = pd.concat([pd.DataFrame([summary]), plan], ignore_index=True, sort=False)
    return plan, windows_by_symbol



def _calendar_prior_spike_mask(prepared: pd.DataFrame, *, baseline_candles: int) -> np.ndarray:
    if prepared.empty:
        return np.zeros(0, dtype=bool)
    quote = _numeric_column(prepared, "_quote_volume")
    trades = _numeric_column(prepared, "_number_of_trades")
    positive_quote = quote.where(quote > 0)
    positive_trades = trades.where(trades > 0)
    baseline_quote = positive_quote.shift(1).rolling(int(baseline_candles), min_periods=1).median()
    baseline_trades = positive_trades.shift(1).rolling(int(baseline_candles), min_periods=1).median()
    with np.errstate(divide="ignore", invalid="ignore"):
        quote_ratio = quote / baseline_quote
        trade_ratio = trades / baseline_trades
        htf_return = (
            pd.to_numeric(prepared["close"], errors="coerce")
            - pd.to_numeric(prepared["open"], errors="coerce")
        ) / pd.to_numeric(prepared["open"], errors="coerce")
    return (
        quote_ratio.ge(3.0).fillna(False).to_numpy(dtype=bool)
        & trade_ratio.ge(3.0).fillna(False).to_numpy(dtype=bool)
        & htf_return.ge(0.0).fillna(False).to_numpy(dtype=bool)
        & np.isfinite(quote.to_numpy(dtype=float))
        & (quote.to_numpy(dtype=float) > 0.0)
    )


def _max_prior_spike_quote_before(
    prepared: pd.DataFrame,
    *,
    spike_mask: np.ndarray,
    history_end: int,
    current_timestamp_ms: int,
) -> float:
    if history_end <= 0 or len(spike_mask) == 0:
        return float("nan")
    timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce").to_numpy(dtype=float)
    quote = _numeric_column(prepared, "_quote_volume").to_numpy(dtype=float)
    lookback_start = int(current_timestamp_ms) - 24 * 60 * 60 * 1000
    index = np.arange(len(prepared))
    mask = (
        (index < int(history_end))
        & spike_mask
        & np.isfinite(timestamps)
        & (timestamps >= lookback_start)
        & np.isfinite(quote)
        & (quote > 0.0)
    )
    if not bool(np.any(mask)):
        return float("nan")
    return float(np.nanmax(quote[mask]))


def _pregrowth_max_single_return_before(prepared: pd.DataFrame, *, history_end: int, pregrowth_candles: int) -> float:
    if history_end <= 0:
        return float("nan")
    start = max(0, int(history_end) - int(pregrowth_candles))
    window = prepared.iloc[start:int(history_end)]
    if window.empty:
        return float("nan")
    opens = pd.to_numeric(window["open"], errors="coerce")
    closes = pd.to_numeric(window["close"], errors="coerce")
    returns = (closes - opens) / opens.replace(0.0, np.nan)
    return _finite_max_or_nan(returns.to_numpy(dtype=float))


def _pair_can_match_runner_category_bounds(
    *,
    tf_set: str,
    pair_quote: float,
    max_possible_trade_ratio: float,
    prepared: pd.DataFrame,
    possible_history_ends: Iterable[int],
    spike_mask: np.ndarray,
    current_timestamp_ms: int,
    config: HtfLtfRunnerDiscoveryConfig,
) -> tuple[bool, dict[str, object]]:
    """Whether a pair can still match at least one frozen C/A/S family.

    This is data-loading only. It rejects a pair only when already-closed
    calendar context and pair upper bounds prove all frozen categories impossible.
    It never uses LTF pace, future labels, PnL, exits, or post-entry candles.
    """

    history_ends = [int(value) for value in possible_history_ends]
    prior_max_values: list[float] = []
    pregrowth_values: list[float] = []
    for history_end in history_ends:
        prior_max_values.append(
            _max_prior_spike_quote_before(
                prepared,
                spike_mask=spike_mask,
                history_end=history_end,
                current_timestamp_ms=int(current_timestamp_ms),
            )
        )
        pregrowth_values.append(
            _pregrowth_max_single_return_before(
                prepared,
                history_end=history_end,
                pregrowth_candles=int(config.pregrowth_candles),
            )
        )
    current_vs_prior_values = [
        _safe_divide(float(pair_quote), float(prior_max))
        for prior_max in prior_max_values
        if np.isfinite(prior_max) and float(prior_max) > 0.0
    ]
    current_vs_prior_upper = max((value for value in current_vs_prior_values if np.isfinite(value)), default=float("nan"))
    pregrowth_max_single_upper = max((value for value in pregrowth_values if np.isfinite(value)), default=float("nan"))

    c_possible = (
        np.isfinite(current_vs_prior_upper)
        and current_vs_prior_upper > 0.45
        and np.isfinite(pregrowth_max_single_upper)
        and pregrowth_max_single_upper > 0.005
    )
    a_possible = np.isfinite(current_vs_prior_upper) and current_vs_prior_upper > 0.6
    s_possible = tf_set == "5m_30s" and np.isfinite(max_possible_trade_ratio) and float(max_possible_trade_ratio) >= 11.7
    possible = bool(c_possible or a_possible or s_possible)
    return possible, {
        "pair_category_gate_model": "safe_necessary_bounds_for_frozen_C_A_S_data_loading_only",
        "pair_category_gate_trading_signal": False,
        "pair_category_C_possible": bool(c_possible),
        "pair_category_A_possible": bool(a_possible),
        "pair_category_S_possible": bool(s_possible),
        "pair_current_vs_prior_spike_max_quote_upper_bound": float(current_vs_prior_upper),
        "pair_pregrowth_max_single_return_upper_bound": float(pregrowth_max_single_upper),
    }


def _targeted_ltf_backfill_seeds_for_symbol(
    *,
    symbol: str,
    htf: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
    htf_ms: int,
    ltf_ms: int,
) -> tuple[list[dict[str, object]], list[tuple[int, int]]]:
    """Plan LTF fetch windows as a safe superset for exact rolling HTF seeds.

    The pair gate must never be a trading filter.  It is allowed to skip a pair
    only when upper bounds prove that no rolling HTF window inside the two
    adjacent closed HTF candles could pass the official rolling seed gate.
    """

    empty_result: tuple[list[dict[str, object]], list[tuple[int, int]]] = ([], [])
    if htf.empty or "timestamp" not in htf.columns:
        return empty_result
    prepared = htf.copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    if prepared.empty or "quote_volume" not in prepared.columns or "number_of_trades" not in prepared.columns:
        return empty_result
    for column in ("open", "high", "low", "close", "quote_volume", "number_of_trades"):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "quote_volume", "number_of_trades"]).reset_index(drop=True)
    if len(prepared) < 2:
        return empty_result
    prepared["_quote_volume"] = _numeric_column(prepared, "quote_volume")
    prepared["_number_of_trades"] = _numeric_column(prepared, "number_of_trades")

    calendar_timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce").astype("int64").to_numpy()
    calendar_end_timestamps = calendar_timestamps + int(htf_ms)
    baseline_quote_median = prepared["_quote_volume"].where(prepared["_quote_volume"] > 0).rolling(config.baseline_candles, min_periods=1).median().to_numpy()
    baseline_trade_median = prepared["_number_of_trades"].where(prepared["_number_of_trades"] > 0).rolling(config.baseline_candles, min_periods=1).median().to_numpy()
    min_history = max(config.baseline_candles, config.dormancy_candles, config.pregrowth_candles)
    tf_set = f"{config.htf_timeframe}_{config.ltf_timeframe}"
    prior_spike_mask = _calendar_prior_spike_mask(prepared, baseline_candles=int(config.baseline_candles))

    rows: list[dict[str, object]] = []
    windows: list[tuple[int, int]] = []
    scored: list[tuple[float, int, dict[str, object]]] = []
    rejection_counts: dict[str, int] = {
        "non_adjacent_pair": 0,
        "invalid_price_bound": 0,
        "insufficient_pre_window_history": 0,
        "impossible_return": 0,
        "impossible_quote_ratio": 0,
        "impossible_trade_ratio": 0,
        "impossible_runner_category_family": 0,
    }
    total_pairs = max(0, len(prepared) - 1)

    for idx in range(total_pairs):
        first = prepared.iloc[idx]
        second = prepared.iloc[idx + 1]
        first_ts = int(first["timestamp"])
        second_ts = int(second["timestamp"])
        if second_ts != first_ts + int(htf_ms):
            rejection_counts["non_adjacent_pair"] += 1
            continue
        pair_quote = float(first["quote_volume"]) + float(second["quote_volume"])
        pair_trades = float(first["number_of_trades"]) + float(second["number_of_trades"])
        pair_high = max(float(first["high"]), float(second["high"]))
        pair_low = min(float(first["low"]), float(second["low"]))
        pair_open_floor = min(float(first["open"]), float(second["open"]), pair_low)
        if not np.isfinite(pair_open_floor) or pair_open_floor <= 0.0:
            rejection_counts["invalid_price_bound"] += 1
            continue
        max_possible_return = _safe_divide(pair_high - pair_open_floor, pair_open_floor)
        max_possible_range = _safe_divide(pair_high - pair_low, pair_open_floor)

        possible_history_ends = sorted(
            {
                int(np.searchsorted(calendar_end_timestamps, int(first_ts), side="right")),
                int(np.searchsorted(calendar_end_timestamps, int(first_ts) + int(htf_ms), side="right")),
            }
        )
        possible_history_ends = [history_end for history_end in possible_history_ends if history_end >= min_history]
        if not possible_history_ends:
            rejection_counts["insufficient_pre_window_history"] += 1
            continue

        possible_quote_ratios: list[float] = []
        possible_trade_ratios: list[float] = []
        for history_end in possible_history_ends:
            median_index = history_end - 1
            if median_index < 0:
                continue
            baseline_quote = float(baseline_quote_median[median_index])
            baseline_trades = float(baseline_trade_median[median_index])
            possible_quote_ratios.append(_safe_divide(pair_quote, baseline_quote))
            possible_trade_ratios.append(_safe_divide(pair_trades, baseline_trades))
        max_possible_quote_ratio = max((value for value in possible_quote_ratios if np.isfinite(value)), default=float("nan"))
        max_possible_trade_ratio = max((value for value in possible_trade_ratios if np.isfinite(value)), default=float("nan"))

        if not np.isfinite(max_possible_return) or max_possible_return < float(config.min_htf_return_pct):
            rejection_counts["impossible_return"] += 1
            continue
        if not np.isfinite(max_possible_quote_ratio) or max_possible_quote_ratio < float(config.min_htf_quote_ratio):
            rejection_counts["impossible_quote_ratio"] += 1
            continue
        if not np.isfinite(max_possible_trade_ratio) or max_possible_trade_ratio < float(config.min_htf_trade_ratio):
            rejection_counts["impossible_trade_ratio"] += 1
            continue

        category_possible = True
        category_bounds: dict[str, object] = {
            "pair_category_gate_model": "disabled",
            "pair_category_gate_trading_signal": False,
        }
        if bool(config.targeted_pair_gate_use_category_necessary_bounds):
            category_possible, category_bounds = _pair_can_match_runner_category_bounds(
                tf_set=tf_set,
                pair_quote=float(pair_quote),
                max_possible_trade_ratio=float(max_possible_trade_ratio),
                prepared=prepared,
                possible_history_ends=possible_history_ends,
                spike_mask=prior_spike_mask,
                current_timestamp_ms=int(first_ts),
                config=config,
            )
            if not category_possible:
                rejection_counts["impossible_runner_category_family"] += 1
                continue

        score = (
            float(max_possible_return) * 100.0
            + float(max_possible_range if np.isfinite(max_possible_range) else 0.0) * 25.0
            + math.log1p(max(0.0, float(max_possible_quote_ratio)))
            + math.log1p(max(0.0, float(max_possible_trade_ratio)))
        )
        scored.append(
            (
                score,
                idx,
                {
                    "pair_quote": pair_quote,
                    "pair_trades": pair_trades,
                    "pair_high": pair_high,
                    "pair_low": pair_low,
                    "pair_open_floor": pair_open_floor,
                    "max_possible_return": float(max_possible_return),
                    "max_possible_range": float(max_possible_range),
                    "max_possible_quote_ratio": float(max_possible_quote_ratio),
                    "max_possible_trade_ratio": float(max_possible_trade_ratio),
                    "possible_history_ends": "|".join(str(value) for value in possible_history_ends),
                    **category_bounds,
                },
            )
        )

    if not scored:
        summary = {
            "symbol": symbol,
            "targeted_ltf_phase": "pre_entry",
            "targeted_ltf_plan_status": "symbol_summary",
            "selection_model": "two_closed_htf_candle_pair_upper_bound_safe_superset_data_loading_only",
            "pair_gate_model": "fetch_pair_unless_exact_rolling_seed_is_mathematically_impossible",
            "htf_timeframe": config.htf_timeframe,
            "ltf_timeframe": config.ltf_timeframe,
            "total_adjacent_pair_candidates": int(total_pairs),
            "planned_pairs": 0,
            **{f"rejected_{key}": int(value) for key, value in rejection_counts.items()},
        }
        return [summary], []

    scored.sort(reverse=True)
    max_events = int(config.targeted_backfill_max_events_per_symbol)
    selected = scored[:max_events] if max_events > 0 else scored
    selected_by_idx: dict[int, dict[str, object]] = {idx: bounds for _, idx, bounds in selected}
    selected_indices = sorted(selected_by_idx)
    for idx in selected_indices:
        first = prepared.iloc[idx]
        second = prepared.iloc[idx + 1]
        bounds = selected_by_idx[idx]
        first_ts = int(first["timestamp"])
        pair_end_exclusive = first_ts + 2 * int(htf_ms)
        window_start = int(first_ts)
        window_end = int(pair_end_exclusive - 1)
        windows.append((window_start, window_end))
        rows.append(
            {
                "symbol": symbol,
                "targeted_ltf_phase": "pre_entry",
                "targeted_ltf_plan_status": "planned",
                "selection_model": "two_closed_htf_candle_pair_upper_bound_safe_superset_data_loading_only",
                "timestamp_ms": first_ts,
                "timestamp_utc": _timestamp_to_utc(first_ts),
                "pair_second_timestamp_ms": int(second["timestamp"]),
                "pair_second_timestamp_utc": _timestamp_to_utc(int(second["timestamp"])),
                "htf_close_ms": pair_end_exclusive,
                "htf_close_utc": _timestamp_to_utc(pair_end_exclusive),
                "htf_timeframe": config.htf_timeframe,
                "ltf_timeframe": config.ltf_timeframe,
                "window_start_ms": window_start,
                "window_start_utc": _timestamp_to_utc(window_start),
                "window_end_ms": window_end,
                "window_end_utc": _timestamp_to_utc(window_end),
                "window_ms": int(window_end - window_start + 1),
                "window_model": "two_adjacent_htf_candles_only_then_exact_ltf_rolling_seed_check",
                "seed_gate": _targeted_ltf_seed_gate_description(config),
                "known_at_seed_cutoff_ms": pair_end_exclusive,
                "known_at_seed_cutoff_utc": _timestamp_to_utc(pair_end_exclusive),
                "pair_quote_volume_upper_bound": float(bounds["pair_quote"]),
                "pair_number_of_trades_upper_bound": float(bounds["pair_trades"]),
                "pair_max_possible_return_pct": float(bounds["max_possible_return"]),
                "pair_max_possible_range_pct": float(bounds["max_possible_range"]),
                "pair_max_possible_htf_quote_ratio": float(bounds["max_possible_quote_ratio"]),
                "pair_max_possible_htf_trade_ratio": float(bounds["max_possible_trade_ratio"]),
                "pair_possible_history_ends": bounds["possible_history_ends"],
                "pair_gate_used_only_as_safe_superset": True,
                "pair_gate_trading_signal": False,
                "pair_gate_fetch_reason": "exact_rolling_seed_and_runner_category_family_not_mathematically_impossible",
                "pair_category_gate_model": bounds.get("pair_category_gate_model", ""),
                "pair_category_gate_trading_signal": bool(bounds.get("pair_category_gate_trading_signal", False)),
                "pair_category_C_possible": bool(bounds.get("pair_category_C_possible", False)),
                "pair_category_A_possible": bool(bounds.get("pair_category_A_possible", False)),
                "pair_category_S_possible": bool(bounds.get("pair_category_S_possible", False)),
                "pair_current_vs_prior_spike_max_quote_upper_bound": bounds.get("pair_current_vs_prior_spike_max_quote_upper_bound", float("nan")),
                "pair_pregrowth_max_single_return_upper_bound": bounds.get("pair_pregrowth_max_single_return_upper_bound", float("nan")),
            }
        )

    rows.append(
        {
            "symbol": symbol,
            "targeted_ltf_phase": "pre_entry",
            "targeted_ltf_plan_status": "symbol_summary",
            "selection_model": "two_closed_htf_candle_pair_upper_bound_safe_superset_data_loading_only",
            "pair_gate_model": "fetch_pair_unless_exact_rolling_seed_is_mathematically_impossible",
            "htf_timeframe": config.htf_timeframe,
            "ltf_timeframe": config.ltf_timeframe,
            "total_adjacent_pair_candidates": int(total_pairs),
            "planned_pairs": int(len(selected_indices)),
            **{f"rejected_{key}": int(value) for key, value in rejection_counts.items()},
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


def _targeted_ltf_seed_timestamps_by_symbol(plan: pd.DataFrame) -> dict[str, set[int]]:
    """Returns HTF seed timestamps selected before any LTF/post-entry data is inspected."""
    if plan.empty or "symbol" not in plan.columns or "timestamp_ms" not in plan.columns:
        return {}
    status = plan.get("targeted_ltf_plan_status", pd.Series(index=plan.index, dtype=object)).astype(str)
    phase = plan.get("targeted_ltf_phase", pd.Series(index=plan.index, dtype=object)).astype(str)
    selected = plan.loc[status.eq("planned") & phase.eq("pre_entry"), ["symbol", "timestamp_ms"]].copy()
    if selected.empty:
        return {}
    selected["timestamp_ms"] = pd.to_numeric(selected["timestamp_ms"], errors="coerce")
    selected = selected.dropna(subset=["symbol", "timestamp_ms"])
    seeds: dict[str, set[int]] = {}
    for row in selected.itertuples(index=False):
        symbol = str(getattr(row, "symbol", ""))
        if not symbol or symbol == "__all__":
            continue
        timestamp_ms = int(float(getattr(row, "timestamp_ms")))
        seeds.setdefault(symbol, set()).add(timestamp_ms)
    return seeds


def _build_targeted_ltf_post_entry_backfill_plan(
    *,
    storage: ParquetStorage,
    symbols: Iterable[str],
    start_ms: int,
    end_ms: int,
    config: HtfLtfRunnerDiscoveryConfig,
    progress_label: str,
    seed_timestamps_by_symbol: Mapping[str, set[int]],
) -> tuple[pd.DataFrame, dict[str, list[tuple[int, int]]]]:
    rows: list[dict[str, object]] = []
    windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    selected_symbols = tuple(symbols)
    progress = _ProgressLine(label=progress_label, total=len(selected_symbols))
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    horizon_ms = int(config.runner_horizon_minutes) * 60_000
    skipped_no_seed_symbols = 0
    skipped_broad_candidates = 0
    for index, symbol in enumerate(selected_symbols, start=1):
        progress.update(index=index, item=symbol)
        symbol_seed_timestamps = seed_timestamps_by_symbol.get(symbol, set())
        if not symbol_seed_timestamps:
            skipped_no_seed_symbols += 1
            continue
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
        candidate_rows, _ = _collect_symbol_candidates(
            symbol=symbol,
            htf=htf,
            ltf=ltf,
            oi=oi,
            config=config,
            allowed_timestamps_ms=symbol_seed_timestamps,
        )
        broad_candidate_count = int(len(candidate_rows))
        skipped_broad_candidates += 0
        symbol_windows: list[tuple[int, int]] = []
        symbol_rows: list[dict[str, object]] = []
        exact_rolling_seed_count = 0
        for candidate in candidate_rows:
            htf_close_ms = _coerce_int(candidate.get("htf_close_ms"))
            if htf_close_ms is None:
                continue
            max_hold_ms = int(config.max_hold_candles) * int(ltf_ms)
            max_confirm_ms = (int(config.ltf_max_confirm_candles) + 1) * int(ltf_ms)
            window_start = int(htf_close_ms)
            window_end = int(max(int(htf_close_ms) + horizon_ms, int(htf_close_ms) + max_confirm_ms + max_hold_ms) - 1)
            symbol_windows.append((window_start, window_end))
            exact_rolling_seed_count += 1
            symbol_rows.append(
                _post_entry_plan_row_for_candidate(
                    candidate,
                    window_start=window_start,
                    window_end=window_end,
                    config=config,
                )
            )
        if symbol_windows:
            windows_by_symbol[symbol] = symbol_windows
            rows.extend(symbol_rows)
        if candidate_rows or exact_rolling_seed_count:
            rows.append(
                {
                    "targeted_ltf_phase": "post_entry",
                    "symbol": symbol,
                    "targeted_ltf_plan_status": "symbol_summary",
                    "htf_timeframe": config.htf_timeframe,
                    "ltf_timeframe": config.ltf_timeframe,
                    "pre_entry_candidates": int(len(candidate_rows)),
                    "pre_entry_seed_timestamps": int(len(symbol_seed_timestamps)),
                    "exact_rolling_seed_candidates": int(exact_rolling_seed_count),
                    "post_entry_windows": int(len(symbol_windows)),
                    "strict_seed_gate_applied_before_candidate_build": True,
                    "selection_model": "two_htf_pair_superset_then_exact_rolling_ltf_seed_no_future",
                    "window_model": "post_entry_fetch_after_exact_rolling_ltf_seed",
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
        "symbols_with_pre_entry_seeds": int(sum(1 for seeds in seed_timestamps_by_symbol.values() if seeds)),
        "skipped_no_seed_symbols": int(skipped_no_seed_symbols),
        "skipped_broad_htf_candidates_not_in_strict_seed_gate": int(skipped_broad_candidates),
        "strict_seed_gate_applied_before_candidate_build": True,
        "symbols_with_windows": int(len(windows_by_symbol)),
        "raw_targeted_windows": int(sum(len(windows) for windows in windows_by_symbol.values())),
        "window_model": "post_entry_fetch_after_exact_rolling_ltf_seed",
        "selection_model": "two_htf_pair_superset_then_exact_rolling_ltf_seed_no_future",
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



def _post_entry_plan_row_for_candidate(
    candidate: Mapping[str, object],
    *,
    window_start: int,
    window_end: int,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    return {
        "targeted_ltf_phase": "post_entry",
        "symbol": candidate.get("symbol", ""),
        "targeted_ltf_plan_status": "planned",
        "signal_scope": "exact_rolling_seed_candidate",
        "selection_model": "two_htf_pair_superset_then_exact_rolling_ltf_seed_no_future",
        "timestamp_ms": candidate.get("timestamp_ms", float("nan")),
        "timestamp_utc": candidate.get("timestamp_utc", ""),
        "htf_close_ms": candidate.get("htf_close_ms", float("nan")),
        "htf_close_utc": candidate.get("htf_close_utc", ""),
        "rolling_htf_window_start_ms": candidate.get("rolling_htf_window_start_ms", float("nan")),
        "rolling_htf_window_end_ms": candidate.get("rolling_htf_window_end_ms", float("nan")),
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "window_start_ms": int(window_start),
        "window_start_utc": _timestamp_to_utc(window_start),
        "window_end_ms": int(window_end),
        "window_end_utc": _timestamp_to_utc(window_end),
        "window_ms": int(window_end - window_start + 1),
        "window_model": "full_confirm_label_exit_fetch_after_exact_rolling_seed",
        "htf_quote_ratio": candidate.get("htf_quote_ratio", float("nan")),
        "htf_trade_ratio": candidate.get("htf_trade_ratio", float("nan")),
        "dormancy_to_anomaly_quote_ratio": candidate.get("dormancy_to_anomaly_quote_ratio", float("nan")),
        "dormancy_to_anomaly_trade_ratio": candidate.get("dormancy_to_anomaly_trade_ratio", float("nan")),
    }

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
        "selection_model": "two_htf_pair_superset_then_exact_rolling_ltf_seed_no_future",
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
        "window_model": "post_entry_fetch_after_exact_rolling_ltf_seed",
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
        "official rolling HTF seed: htf_quote_ratio>={quote:.4g} AND "
        "htf_trade_ratio>={trade:.4g} AND htf_return_pct>={ret:.4%}; "
        "2xHTF pair gate is data-loading only and fetches unless this seed is "
        "mathematically impossible by pair upper bounds"
    ).format(
        quote=float(config.min_htf_quote_ratio),
        trade=float(config.min_htf_trade_ratio),
        ret=float(config.min_htf_return_pct),
    )

def _ensure_targeted_ltf_backfill(
    *,
    cache_dir: Path,
    ltf_timeframe: str,
    windows_by_symbol: Mapping[str, Iterable[tuple[int, int]]],
    progress_label: str,
    max_merged_span_ms: int | None = 60 * 60_000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not windows_by_symbol:
        return (
            pd.DataFrame([{"status": "no_targeted_windows", "reason": "two_htf_pair_superset_selected_zero_windows"}]),
            pd.DataFrame([{"status": "not_run", "reason": "no_targeted_windows"}]),
        )
    from research_tools.anomaly_strategy_backtest import ensure_targeted_aggtrade_direct_ltf_cache

    return ensure_targeted_aggtrade_direct_ltf_cache(
        cache_dir=cache_dir,
        windows_by_symbol=windows_by_symbol,
        target_timeframes=(str(ltf_timeframe),),
        progress_label=progress_label,
        max_merged_span_ms=max_merged_span_ms,
    )


def _rolling_htf_from_ltf(ltf: pd.DataFrame, *, htf_ms: int, ltf_ms: int) -> pd.DataFrame:
    if ltf.empty or "timestamp" not in ltf.columns:
        return pd.DataFrame()
    required = {"timestamp", "open", "high", "low", "close", "quote_volume", "number_of_trades"}
    if not required.issubset(ltf.columns):
        return pd.DataFrame()
    frame = ltf.copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    for column in ("open", "high", "low", "close", "quote_volume", "number_of_trades", "volume", "taker_buy_quote_volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    if frame.empty:
        return pd.DataFrame()
    frame["expected_timestamp"] = frame["timestamp"].shift(1) + int(ltf_ms)
    frame["continuous_from_prev"] = frame["expected_timestamp"].isna() | frame["timestamp"].eq(frame["expected_timestamp"])
    window_candles = int(round(float(htf_ms) / float(ltf_ms)))
    if window_candles <= 0 or len(frame) < window_candles:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    continuous = frame["continuous_from_prev"].to_numpy(dtype=bool)
    timestamps = frame["timestamp"].astype("int64").to_numpy()
    for end_pos in range(window_candles - 1, len(frame)):
        start_pos = end_pos - window_candles + 1
        if start_pos + 1 <= end_pos and not bool(np.all(continuous[start_pos + 1 : end_pos + 1])):
            continue
        window = frame.iloc[start_pos : end_pos + 1]
        start_ts = int(timestamps[start_pos])
        expected_end_exclusive = start_ts + int(htf_ms)
        actual_end_exclusive = int(timestamps[end_pos]) + int(ltf_ms)
        if actual_end_exclusive != expected_end_exclusive:
            continue
        row: dict[str, object] = {
            "timestamp": start_ts,
            "open": float(window.iloc[0]["open"]),
            "high": float(pd.to_numeric(window["high"], errors="coerce").max()),
            "low": float(pd.to_numeric(window["low"], errors="coerce").min()),
            "close": float(window.iloc[-1]["close"]),
            "volume": float(pd.to_numeric(window.get("volume", pd.Series(dtype=float)), errors="coerce").sum()) if "volume" in window.columns else float("nan"),
            "quote_volume": float(pd.to_numeric(window["quote_volume"], errors="coerce").sum()),
            "number_of_trades": float(pd.to_numeric(window["number_of_trades"], errors="coerce").sum()),
            "rolling_htf_window_start_ms": start_ts,
            "rolling_htf_window_end_ms": expected_end_exclusive,
            "rolling_htf_ltf_candles": int(window_candles),
        }
        if "taker_buy_quote_volume" in window.columns:
            row["taker_buy_quote_volume"] = float(pd.to_numeric(window["taker_buy_quote_volume"], errors="coerce").sum())
        rows.append(row)
    return pd.DataFrame(rows)

def _collect_symbol_candidates(
    *,
    symbol: str,
    htf: pd.DataFrame,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
    allowed_timestamps_ms: set[int] | None = None,
) -> tuple[list[dict[str, object]], int]:
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    horizon_ms = int(config.runner_horizon_minutes) * 60_000
    rows: list[dict[str, object]] = []

    calendar = htf.copy()
    calendar["timestamp"] = pd.to_numeric(calendar["timestamp"], errors="coerce")
    calendar = calendar.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
    if calendar.empty or "quote_volume" not in calendar.columns or "number_of_trades" not in calendar.columns:
        return [], 0
    for column in ("open", "high", "low", "close", "quote_volume", "number_of_trades"):
        calendar[column] = pd.to_numeric(calendar[column], errors="coerce")
    calendar = calendar.dropna(subset=["timestamp", "open", "high", "low", "close", "quote_volume", "number_of_trades"]).reset_index(drop=True)
    if calendar.empty:
        return [], 0
    calendar["_quote_volume"] = _numeric_column(calendar, "quote_volume")
    calendar["_number_of_trades"] = _numeric_column(calendar, "number_of_trades")

    rolling = _rolling_htf_from_ltf(ltf, htf_ms=htf_ms, ltf_ms=ltf_ms)
    if rolling.empty:
        return [], 0
    rolling["timestamp"] = pd.to_numeric(rolling["timestamp"], errors="coerce")
    rolling = rolling.dropna(subset=["timestamp", "open", "high", "low", "close", "quote_volume", "number_of_trades"]).sort_values("timestamp").reset_index(drop=True)
    if rolling.empty:
        return [], 0

    if allowed_timestamps_ms is not None:
        pair_starts = sorted(int(value) for value in allowed_timestamps_ms)
        if not pair_starts:
            return [], 0
        allowed_mask = pd.Series(False, index=rolling.index)
        rolling_ts = pd.to_numeric(rolling["timestamp"], errors="coerce")
        for pair_start in pair_starts:
            allowed_mask |= rolling_ts.between(int(pair_start), int(pair_start) + int(htf_ms), inclusive="both")
        rolling = rolling.loc[allowed_mask].reset_index(drop=True)
        if rolling.empty:
            return [], 0

    calendar_timestamps = pd.to_numeric(calendar["timestamp"], errors="coerce").astype("int64").to_numpy()
    calendar_end_timestamps = calendar_timestamps + int(htf_ms)
    max_ltf_timestamp = int(pd.to_numeric(ltf.get("timestamp", pd.Series(dtype=float)), errors="coerce").max()) if not ltf.empty and "timestamp" in ltf.columns else 0
    scanned_rows = 0
    min_history = max(config.baseline_candles, config.dormancy_candles, config.pregrowth_candles)

    for _, rolling_row in rolling.iterrows():
        ts = int(rolling_row["timestamp"])
        close_ts = ts + htf_ms
        if close_ts + horizon_ms > max_ltf_timestamp + ltf_ms:
            # The seed may still be valid, but this cache slice cannot honestly label/replay it yet.
            # The post-entry planner will fetch the full window after the exact seed is found.
            pass
        # Baseline/dormancy/pregrowth must be strictly before the rolling window.
        # A calendar HTF candle that overlaps the rolling window is known by seed close,
        # but it already contains anomaly data and must not contaminate pre-seed context.
        history_end = int(np.searchsorted(calendar_end_timestamps, ts, side="right"))
        if history_end < min_history:
            continue
        baseline = calendar.iloc[history_end - config.baseline_candles : history_end]
        dormancy = calendar.iloc[history_end - config.dormancy_candles : history_end]
        pregrowth = calendar.iloc[history_end - config.pregrowth_candles : history_end]
        if baseline.empty or dormancy.empty or pregrowth.empty:
            continue
        scanned_rows += 1

        anomaly_open = float(rolling_row["open"])
        anomaly_high = float(rolling_row["high"])
        anomaly_low = float(rolling_row["low"])
        anomaly_close = float(rolling_row["close"])
        anomaly_quote = float(rolling_row["quote_volume"])
        anomaly_trades = float(rolling_row["number_of_trades"])
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

        anomaly_gate = (
            quote_ratio >= config.min_htf_quote_ratio
            and trade_ratio >= config.min_htf_trade_ratio
            and htf_return >= config.min_htf_return_pct
        )
        if not anomaly_gate:
            continue

        pregrowth_features = _pregrowth_features(pregrowth)
        oi_features = _oi_pregrowth_features(
            oi,
            start_ms=int(pregrowth.iloc[0]["timestamp"]),
            decision_ms=close_ts,
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

        current_context_row = {
            "timestamp": ts,
            "open": anomaly_open,
            "high": anomaly_high,
            "low": anomaly_low,
            "close": anomaly_close,
            "quote_volume": anomaly_quote,
            "number_of_trades": anomaly_trades,
            "_quote_volume": anomaly_quote,
            "_number_of_trades": anomaly_trades,
        }
        prior_context_frame = pd.concat([calendar.iloc[:history_end], pd.DataFrame([current_context_row])], ignore_index=True, sort=False)
        positive_quote = prior_context_frame["_quote_volume"].where(prior_context_frame["_quote_volume"] > 0)
        positive_trades = prior_context_frame["_number_of_trades"].where(prior_context_frame["_number_of_trades"] > 0)
        baseline_quote_fast = positive_quote.shift(1).rolling(config.baseline_candles, min_periods=1).median()
        baseline_trades_fast = positive_trades.shift(1).rolling(config.baseline_candles, min_periods=1).median()
        htf_return_fast = (pd.to_numeric(prior_context_frame["close"], errors="coerce") - pd.to_numeric(prior_context_frame["open"], errors="coerce")) / pd.to_numeric(
            prior_context_frame["open"],
            errors="coerce",
        )
        quote_ratio_fast = (_numeric_column(prior_context_frame, "_quote_volume") / baseline_quote_fast).replace([np.inf, -np.inf], np.nan)
        trade_ratio_fast = (_numeric_column(prior_context_frame, "_number_of_trades") / baseline_trades_fast).replace([np.inf, -np.inf], np.nan)
        prior_spike_context = _prepare_prior_spike_context(
            prior_context_frame,
            timestamps=pd.to_numeric(prior_context_frame["timestamp"], errors="coerce"),
            quote_ratio=quote_ratio_fast,
            trade_ratio=trade_ratio_fast,
            htf_return=htf_return_fast,
        )
        prior_spike_features = _prior_spike_features_for_index(
            prior_spike_context,
            idx=len(prior_context_frame) - 1,
            current_timestamp_ms=ts,
        )

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
                "status": "ok",
                "setup_nature": setup_nature,
                "timestamp_ms": ts,
                "timestamp_utc": _timestamp_to_utc(ts),
                "htf_close_ms": close_ts,
                "htf_close_utc": _timestamp_to_utc(close_ts),
                "rolling_htf_window_start_ms": ts,
                "rolling_htf_window_start_utc": _timestamp_to_utc(ts),
                "rolling_htf_window_end_ms": close_ts,
                "rolling_htf_window_end_utc": _timestamp_to_utc(close_ts),
                "rolling_htf_step_ms": ltf_ms,
                "rolling_baseline_model": "calendar_htf_candles_fully_closed_before_rolling_window_start",
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
        candidate_categories = match_rolling_categories({**candidate, **signal_features})
        if not candidate_categories:
            continue
        oi_at_signal = _oi_asof(oi, decision_available_ts)
        return {
            **candidate,
            "signal_status": "selected",
            "signal_model": "first_category_qualified_ltf_signal_after_rolling_htf_seed",
            "runner_candidate_matched_categories": "|".join(candidate_categories),
            "runner_candidate_category": candidate_categories[0],
            "runner_candidate_priority_rank": float(rolling_category_priority_rank(candidate_categories[0]) or float("nan")),
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

    tp1_r = float(config.tp1_r)
    tp1_close_fraction = float(config.tp1_close_fraction)
    tp1_price = entry_price + initial_risk * tp1_r
    active_stop = initial_stop
    trail_updates = 0
    max_high = entry_price
    min_low = entry_price
    prior_trailing_lows: list[float] = []
    trailing_lookback = max(1, int(config.trail_lookback_candles))
    tp1_hit = False
    tp1_ts = float("nan")
    tp1_raw_price = float("nan")
    tp1_fill_price = float("nan")
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
            exit_reason = "initial_stop" if not tp1_hit and trail_updates == 0 else "structural_trailing_stop"
            exit_ts = candle_ts
            raw_exit_price = active_stop
            exit_price = raw_exit_price * (1.0 - config.exit_slippage_pct)
            exit_stop_before_update = active_stop
            break
        if not tp1_hit and high >= tp1_price:
            tp1_hit = True
            tp1_ts = candle_ts
            tp1_raw_price = tp1_price
            tp1_fill_price = tp1_raw_price * (1.0 - config.exit_slippage_pct)
        if tp1_hit and len(prior_trailing_lows) >= trailing_lookback and new_high_or_equal:
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

    remaining_fraction = 1.0 - tp1_close_fraction if tp1_hit else 1.0
    tp1_leg_gross_return = 0.0
    tp1_leg_gross_r = 0.0
    if tp1_hit:
        tp1_leg_gross_return = (tp1_fill_price - entry_price) / entry_price
        tp1_leg_gross_r = (tp1_fill_price - entry_price) / initial_risk
    rest_gross_return = (exit_price - entry_price) / entry_price
    rest_gross_r = (exit_price - entry_price) / initial_risk
    gross_return = tp1_close_fraction * tp1_leg_gross_return + remaining_fraction * rest_gross_return if tp1_hit else rest_gross_return
    net_return = gross_return - 2.0 * float(config.fee_rate)
    gross_r = tp1_close_fraction * tp1_leg_gross_r + remaining_fraction * rest_gross_r if tp1_hit else rest_gross_r
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
        "exit_price_model": "tp1_partial_then_structural_stop_or_time_exit_minus_adverse_slippage_strict_ltf_path",
        "exit_stop_before_update": exit_stop_before_update,
        "trail_updates": trail_updates,
        "final_trailing_stop": active_stop,
        "tp1_hit": bool(tp1_hit),
        "tp1_r": tp1_r,
        "tp1_price": tp1_price,
        "tp1_timestamp_ms": tp1_ts,
        "tp1_timestamp_utc": "" if not tp1_hit else _timestamp_to_utc(tp1_ts),
        "tp1_raw_price": tp1_raw_price,
        "tp1_fill_price": tp1_fill_price,
        "tp1_close_fraction": tp1_close_fraction,
        "tp_model": "tp1_0p75r_close_50pct_then_structural_trailing",
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
    quote_min_adjacent = _finite_min_or_nan(quote_adjacent)
    trade_min_adjacent = _finite_min_or_nan(trade_adjacent)
    quote_top = _finite_max_or_nan(quote_values)
    trade_top = _finite_max_or_nan(trade_values)
    return {
        "ltf_quote_min_adjacent_ratio": quote_min_adjacent,
        "ltf_quote_decay_under50": bool(np.isfinite(quote_min_adjacent) and quote_min_adjacent < 0.5),
        "ltf_quote_last_first_ratio": _safe_divide(float(quote_values[-1]), float(quote_values[0])) if quote_values.size else float("nan"),
        "ltf_quote_top1_share": _safe_divide(quote_top, quote_total),
        "ltf_quote_last_share": _safe_divide(float(quote_values[-1]), quote_total) if quote_values.size else float("nan"),
        "ltf_trade_min_adjacent_ratio": trade_min_adjacent,
        "ltf_trade_decay_under50": bool(np.isfinite(trade_min_adjacent) and trade_min_adjacent < 0.5),
        "ltf_trade_last_first_ratio": _safe_divide(float(trade_values[-1]), float(trade_values[0])) if trade_values.size else float("nan"),
        "ltf_trade_top1_share": _safe_divide(trade_top, trade_total),
    }


def _finite_min_or_nan(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite.min()) if finite.size else float("nan")


def _finite_max_or_nan(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite.max()) if finite.size else float("nan")


def _finite_median_or_nan(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else float("nan")


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
    median_quote = _finite_median_or_nan(prior_quote)
    max_quote = _finite_max_or_nan(prior_quote)
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
    oos_v1_masks = _runner_fader_oos_v1_masks(trades)
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
        *oos_v1_masks.items(),
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
    oos_v1_masks = _runner_fader_oos_v1_masks(trades)
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
        *oos_v1_masks.items(),
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


def _runner_fader_oos_v1_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Known-at-entry runner/fader hypotheses promoted from the 2026-05-28 7d readout.

    These masks are research artifacts only: they use HTF/LTF signal features that are
    already known at the selected entry decision, and intentionally avoid future labels,
    MFE/MAE, PnL, exit reason, or post-entry prices.
    """
    htf_trade_ratio = _numeric_series(frame, "htf_trade_ratio")
    htf_quote_ratio = _numeric_series(frame, "htf_quote_ratio")
    ltf_trade_pace_ratio = _numeric_series(frame, "ltf_trade_pace_ratio")

    htf_trade_awake = htf_trade_ratio.ge(12.0)
    ltf_not_blowoff = ltf_trade_pace_ratio.le(6.0)
    htf_quote_not_extreme = htf_quote_ratio.le(48.0)

    candidate = htf_trade_awake & ltf_not_blowoff
    strict = candidate & htf_quote_not_extreme
    ltf_overheat_fader_probe = htf_trade_awake & ltf_trade_pace_ratio.gt(6.0)
    quote_blowoff_fader_probe = candidate & htf_quote_ratio.gt(48.0)

    return {
        "oos_v1_htf_trade12_ltf_trade_pace_le6": candidate,
        "oos_v1_htf_trade12_ltf_trade_pace_le6_htf_quote_le48": strict,
        "fader_probe_htf_trade12_ltf_trade_pace_gt6": ltf_overheat_fader_probe,
        "fader_probe_htf_trade12_ltf_trade_pace_le6_htf_quote_gt48": quote_blowoff_fader_probe,
    }


def _strict_runner_fader_oos_hypothesis_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        result = trades.copy()
        result.insert(0, "oos_hypothesis_rule", "oos_v1_htf_trade12_ltf_trade_pace_le6_htf_quote_le48")
        result.insert(1, "uses_future_label_as_entry_filter", False)
        return result
    masks = _runner_fader_oos_v1_masks(trades)
    mask = masks["oos_v1_htf_trade12_ltf_trade_pace_le6_htf_quote_le48"]
    subset = trades.loc[mask.fillna(False)].copy()
    subset.insert(0, "oos_hypothesis_rule", "oos_v1_htf_trade12_ltf_trade_pace_le6_htf_quote_le48")
    subset.insert(1, "uses_future_label_as_entry_filter", False)
    return subset


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



def _with_runner_candidate_categories(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    frame = trades.copy()
    matched_values: list[str] = []
    selected_values: list[str] = []
    priority_values: list[float] = []
    for row in frame.to_dict("records"):
        existing_category = str(row.get("runner_candidate_category", "") or "")
        existing_matches = str(row.get("runner_candidate_matched_categories", "") or "")
        if existing_category:
            matches = [item for item in existing_matches.split("|") if item] or [existing_category]
        else:
            matches = match_rolling_categories(row)
        matched_values.append("|".join(matches))
        selected = matches[0] if matches else ""
        selected_values.append(selected)
        priority_values.append(float(rolling_category_priority_rank(selected) or float("nan")))
    frame["runner_candidate_matched_categories"] = matched_values
    frame["runner_candidate_category"] = selected_values
    frame["runner_candidate_priority_rank"] = priority_values
    return frame


def _apply_runner_candidate_portfolio(
    trades: pd.DataFrame,
    *,
    config: HtfLtfRunnerDiscoveryConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return trades.copy(), pd.DataFrame()
    required = {"entry_timestamp_ms", "exit_timestamp_ms", "symbol", "status", "runner_candidate_category"}
    if not required.issubset(trades.columns):
        return trades.iloc[0:0].copy(), pd.DataFrame([{"event_type": "portfolio_not_run_missing_columns", "missing_columns": ",".join(sorted(required.difference(trades.columns)))}])
    frame = trades.copy()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce")
    frame["exit_timestamp_ms"] = pd.to_numeric(frame["exit_timestamp_ms"], errors="coerce")
    frame = frame.sort_values(["entry_timestamp_ms", "runner_candidate_priority_rank", "symbol"], na_position="last").reset_index(drop=True)
    active: list[dict[str, object]] = []
    cooldown_until_by_symbol: dict[str, int] = {}
    selected_indices: list[int] = []
    events: list[dict[str, object]] = []
    risk_per_trade = float(config.risk_per_trade_pct)
    max_total_risk = float(config.max_total_open_risk_pct)
    default_cooldown_ms = int(_timeframe_ms(config.htf_timeframe))

    for idx, row in frame.iterrows():
        entry_ts_value = row.get("entry_timestamp_ms")
        if not np.isfinite(float(entry_ts_value)):
            continue
        entry_ts = int(float(entry_ts_value))
        symbol = str(row.get("symbol", ""))
        active = [position for position in active if int(position.get("exit_ts", 0)) > entry_ts]
        category = str(row.get("runner_candidate_category", ""))
        row_htf_timeframe = str(row.get("htf_timeframe", "") or "")
        try:
            cooldown_ms = int(_timeframe_ms(row_htf_timeframe)) if row_htf_timeframe else default_cooldown_ms
        except Exception:
            cooldown_ms = default_cooldown_ms
        base_event = {
            "event_timestamp_ms": entry_ts,
            "event_timestamp_utc": _timestamp_to_utc(entry_ts),
            "symbol": symbol,
            "htf_timeframe": row.get("htf_timeframe", ""),
            "ltf_timeframe": row.get("ltf_timeframe", ""),
            "entry_timestamp_ms": entry_ts,
            "entry_timestamp_utc": row.get("entry_timestamp_utc", ""),
            "exit_timestamp_ms": row.get("exit_timestamp_ms", float("nan")),
            "runner_candidate_category": category,
            "runner_candidate_matched_categories": row.get("runner_candidate_matched_categories", ""),
            "risk_per_trade_pct": risk_per_trade,
            "max_total_open_risk_pct": max_total_risk,
            "open_positions_before": int(len(active)),
            "open_risk_before_pct": float(len(active) * risk_per_trade),
            "symbol_cooldown_ms": cooldown_ms,
        }
        if not category:
            events.append({**base_event, "event_type": "rejected_no_runner_candidate_category"})
            continue
        if str(row.get("status", "")) != "closed":
            events.append({**base_event, "event_type": "rejected_trade_not_closed", "status": row.get("status", "")})
            continue
        if any(str(position.get("symbol", "")) == symbol for position in active):
            events.append({**base_event, "event_type": "blocked_same_symbol_open"})
            continue
        cooldown_until = int(cooldown_until_by_symbol.get(symbol, 0))
        if cooldown_until > entry_ts:
            events.append({**base_event, "event_type": "blocked_symbol_cooldown", "cooldown_until_ms": cooldown_until, "cooldown_until_utc": _timestamp_to_utc(cooldown_until)})
            continue
        if (len(active) + 1) * risk_per_trade > max_total_risk + 1e-12:
            events.append({**base_event, "event_type": "blocked_total_risk_cap"})
            continue
        exit_ts_value = row.get("exit_timestamp_ms")
        if not np.isfinite(float(exit_ts_value)):
            events.append({**base_event, "event_type": "rejected_missing_exit_timestamp"})
            continue
        exit_ts = int(float(exit_ts_value))
        selected_indices.append(int(idx))
        active.append({"symbol": symbol, "exit_ts": exit_ts, "risk": risk_per_trade})
        cooldown_until_by_symbol[symbol] = max(cooldown_until_by_symbol.get(symbol, 0), exit_ts + cooldown_ms)
        events.append({**base_event, "event_type": "selected", "open_positions_after": int(len(active)), "open_risk_after_pct": float(len(active) * risk_per_trade), "cooldown_until_ms": int(exit_ts + cooldown_ms), "cooldown_until_utc": _timestamp_to_utc(int(exit_ts + cooldown_ms))})
    selected = frame.loc[selected_indices].copy() if selected_indices else frame.iloc[0:0].copy()
    return selected.reset_index(drop=True), pd.DataFrame(events)


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
                "detail": "rolling HTF seed is built only from closed LTF candles; entry is the next LTF open after the first closed C/A/S confirmation candle; confirmation and next-open entry require a continuous LTF path with no gap hops.",
            },
            {
                "check": "oos_runner_fader_v1_hypothesis",
                "status": "research_only",
                "detail": "C/A/S category assignment is fixed-priority and uses only known-at-entry rolling HTF/LTF fields. It is still a research hypothesis and must be validated on a held-out run before live promotion.",
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
                "check": "portfolio_constraints",
                "status": "ok",
                "detail": f"Portfolio selection uses fixed C->A->S priority, one open trade per symbol, symbol cooldown equal to one rolling HTF window, risk_per_trade={config.risk_per_trade_pct}, and max_total_open_risk={config.max_total_open_risk_pct}.",
            },
            {
                "check": "costs",
                "status": "ok",
                "detail": f"entry_slippage={config.entry_slippage_pct}; exit_slippage={config.exit_slippage_pct}; round_trip_fee={2 * config.fee_rate}.",
            },
            {
                "check": "data_access",
                "status": "ok",
                "detail": "Targeted LTF access is two-stage: a cheap two-closed-HTF-candle safe-superset may fetch only that pair first; full confirm/label/exit LTF is fetched only after an exact rolling LTF seed exists. The strategy seed itself is rolling HTF, not calendar HTF.",
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
