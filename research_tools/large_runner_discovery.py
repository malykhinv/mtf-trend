"""Cache-only large-runner discovery for pump-awakening research.

This module is intentionally separate from the current HTF/LTF runner
discovery. It is a cheaper research sweep over 5m + 1m cached candles:

* 5m candles find broad first-awakening setups from left to right;
* 1m candles describe intra-seed tape and simulate entry/exit paths;
* runner/fader labels are anchored to each anomaly, not to calendar hours;
* future labels are written only as evaluation fields, never as rule inputs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import math
import os
import time
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from constants import DEFAULT_CACHE_DIR, DEFAULT_RESULTS_DIR
from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe
from research_tools.large_runner_nature_rules import (
    TRADE_ELIGIBLE_E5_ARMS,
    evaluate_large_runner_nature,
    v4_quality_mask,
)
from research_tools.hourly_levels import (
    HourlyLevelMetric,
    HourlyLevelScanConfig,
    find_hourly_overhead_levels,
)


MINUTE_MS = 60_000
FIVE_MINUTE_MS = 5 * MINUTE_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

LARGE_RUNNER_DISCOVERY_ID = "large_runner_discovery_v1"
LEVEL_ATTACK_DISCOVERY_ID = "h1_level_attack_v1"


@dataclass(frozen=True, slots=True)
class LargeRunnerDiscoveryConfig:
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "large_runner_discovery"
    days: int = 30
    end_timestamp_ms: int | None = None
    symbol_workers: int = 4
    baseline_5m_candles: int = 288
    broad_min_return_pct: float = 0.005
    broad_min_quote_ratio: float = 3.0
    broad_min_trade_ratio: float = 2.0
    setup_cluster_minutes: int = 60
    horizon_minutes: int = 60
    max_initial_risk_pct: float = 0.10
    structural_stop_buffer_pct: float = 0.0005
    entry_slippage_pct: float = 0.0005
    exit_slippage_pct: float = 0.0005
    fee_rate: float = 0.0004
    trail_lookback_1m: int = 5

    def __post_init__(self) -> None:
        if int(self.days) <= 0:
            raise ValueError("days must be > 0")
        if int(self.baseline_5m_candles) <= 0:
            raise ValueError("baseline_5m_candles must be > 0")
        if int(self.setup_cluster_minutes) <= 0:
            raise ValueError("setup_cluster_minutes must be > 0")
        if int(self.horizon_minutes) <= 0:
            raise ValueError("horizon_minutes must be > 0")
        if float(self.max_initial_risk_pct) <= 0.0:
            raise ValueError("max_initial_risk_pct must be > 0")
        if int(self.trail_lookback_1m) <= 0:
            raise ValueError("trail_lookback_1m must be > 0")


@dataclass(frozen=True, slots=True)
class LargeRunnerArm:
    arm_id: str
    decision_offset_minutes: int
    priority: int


@dataclass(frozen=True, slots=True)
class ExitPolicySpec:
    policy_id: str
    tp_r: float = 0.0
    tp_close_fraction: float = 0.0
    be_after_r: float = 0.0
    early_kill_minutes: int = 0
    early_kill_min_mfe_pct: float = 0.0
    early_kill_require_close_above_entry: bool = False


LARGE_RUNNER_ARMS: tuple[LargeRunnerArm, ...] = (
    LargeRunnerArm("E5_ignition_strict", 5, 10),
    LargeRunnerArm("E5_mass_ignition", 5, 20),
    LargeRunnerArm("preheat_ignition", 5, 30),
    LargeRunnerArm("E10_confirmed_runner", 10, 40),
    LargeRunnerArm("E15_exceptional_runner", 15, 50),
)

LARGE_RUNNER_EXIT_POLICIES: tuple[ExitPolicySpec, ...] = (
    ExitPolicySpec("trail_all_1m_structure"),
    ExitPolicySpec("tp075r_close25_trail", tp_r=0.75, tp_close_fraction=0.25),
    ExitPolicySpec("tp15r_close25_trail", tp_r=1.50, tp_close_fraction=0.25),
    ExitPolicySpec(
        "trail_all_kill10_no_progress",
        early_kill_minutes=10,
        early_kill_min_mfe_pct=0.03,
        early_kill_require_close_above_entry=True,
    ),
    ExitPolicySpec(
        "tp075r_close25_be1r_kill10",
        tp_r=0.75,
        tp_close_fraction=0.25,
        be_after_r=1.0,
        early_kill_minutes=10,
        early_kill_min_mfe_pct=0.03,
        early_kill_require_close_above_entry=True,
    ),
)


class _ProgressLine:
    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(0, int(total))
        self.started_at = time.monotonic()
        self.last_emit_at = 0.0

    def update(self, *, index: int, item: str) -> None:
        now = time.monotonic()
        if now - self.last_emit_at < 0.5 and index < self.total:
            return
        self.last_emit_at = now
        pct = (index / self.total * 100.0) if self.total else 100.0
        elapsed = max(0.001, now - self.started_at)
        eta = (elapsed / max(index, 1)) * max(self.total - index, 0) if self.total else 0.0
        safe_item = _safe_console_text(item)
        print(
            f"{self.label}: scanning {index}/{self.total} ({pct:5.1f}%) "
            f"symbol={safe_item} eta={_format_duration(eta)}",
            flush=True,
        )

    def finish(self) -> None:
        print("", flush=True)


def run_large_runner_discovery(
    config: LargeRunnerDiscoveryConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str = "large runner discovery",
) -> Path:
    started_at = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    selected_symbols = tuple(_resolve_symbols(config.cache_dir, symbols))
    storage = ParquetStorage(config.cache_dir)
    end_ms = _resolve_end_timestamp_ms(config, storage, selected_symbols)
    start_ms = int(end_ms) - int(config.days) * 24 * HOUR_MS
    warmup_ms = max(48 * HOUR_MS, int(config.baseline_5m_candles) * FIVE_MINUTE_MS)
    load_start_ms = int(start_ms) - warmup_ms
    load_end_ms = int(end_ms) + int(config.horizon_minutes + 20) * MINUTE_MS

    progress = _ProgressLine(progress_label, len(selected_symbols))

    def _process(symbol: str) -> dict[str, object]:
        local_storage = ParquetStorage(config.cache_dir)
        frame_5m = _load_frame(local_storage, symbol, "5m", start_ms=load_start_ms, end_ms=load_end_ms)
        quality = [_data_quality_row(symbol=symbol, frame_5m=frame_5m, frame_1m=pd.DataFrame())]
        if frame_5m.empty:
            return {"raw": [], "setups": [], "matches": [], "trades": [], "quality": quality, "top_hours": []}
        prepared_5m = _prepare_ohlcv(frame_5m)
        if not _has_real_flow(prepared_5m):
            quality[0]["data_rejection"] = "missing_real_5m_quote_or_trade_count"
            return {"raw": [], "setups": [], "matches": [], "trades": [], "quality": quality, "top_hours": []}
        raw = _collect_raw_candidates(symbol=symbol, frame=prepared_5m, start_ms=start_ms, end_ms=end_ms, config=config)
        first_setups = _cluster_setups_with_prefilter(raw, frame_5m=prepared_5m, config=config)
        top_hours = _hourly_growth_rows(symbol=symbol, frame_5m=prepared_5m, start_ms=start_ms, end_ms=end_ms)
        level_frame_1h = _aggregate_5m_to_1h(prepared_5m)
        level_cache: dict[int, tuple[list[HourlyLevelMetric], str, str]] = {}
        level_scan_config = _level_attack_scan_config(config)
        if first_setups:
            first_setups = [
                _annotate_setup_level_attack(
                    setup,
                    frame_5m=prepared_5m,
                    level_frame_1h=level_frame_1h,
                    level_cache=level_cache,
                    level_scan_config=level_scan_config,
                )
                for setup in first_setups
            ]
        if not first_setups:
            quality[0]["1m_load_scope"] = "not_loaded_no_broad_setups"
            return {"raw": raw, "setups": [], "matches": [], "trades": [], "quality": quality, "top_hours": top_hours}
        setup_rows: list[dict[str, object]] = []
        enrichable_setups: list[dict[str, object]] = []
        for setup in first_setups:
            if bool(setup["large_runner_5m_prefilter_passed"]):
                enrichable_setups.append(setup)
            else:
                setup_rows.append({**setup, "enrichment_status": "skipped_1m_prefilter_no_large_runner_arm_possible"})
        if not enrichable_setups:
            quality[0]["1m_load_scope"] = "not_loaded_no_prefiltered_large_runner_setups"
            return {"raw": raw, "setups": setup_rows, "matches": [], "trades": [], "quality": quality, "top_hours": top_hours}
        min_seed = min(int(row["seed_open_ms"]) for row in enrichable_setups)
        max_seed = max(int(row["seed_open_ms"]) for row in enrichable_setups)
        minute_start = min_seed
        minute_end = max_seed + int(config.horizon_minutes + 20) * MINUTE_MS
        frame_1m = _load_frame(local_storage, symbol, "1m", start_ms=minute_start, end_ms=minute_end)
        quality[0].update(
            {
                "1m_rows": int(len(frame_1m)),
                "1m_quote_volume_source": "quote_volume" if "quote_volume" in frame_1m.columns else "missing",
                "1m_trade_count_source": "number_of_trades" if "number_of_trades" in frame_1m.columns else "missing",
                "taker_buy_quote_source": "taker_buy_quote_volume" if "taker_buy_quote_volume" in frame_1m.columns else "missing",
                "1m_load_scope": "targeted_setup_minmax_window",
                "1m_load_start_ms": int(minute_start),
                "1m_load_end_ms": int(minute_end),
                "1m_enrichable_setups": int(len(enrichable_setups)),
                "1m_prefilter_skipped_setups": int(len(first_setups) - len(enrichable_setups)),
            }
        )
        match_rows: list[dict[str, object]] = []
        trade_rows: list[dict[str, object]] = []
        for setup in enrichable_setups:
            enriched = _enrich_setup(setup, frame_5m=prepared_5m, frame_1m=frame_1m, config=config)
            enriched["enrichment_status"] = "1m_enriched_after_5m_prefilter"
            setup_rows.append(enriched)
            for arm_id in match_large_runner_arms(enriched):
                arm = next(arm for arm in LARGE_RUNNER_ARMS if arm.arm_id == arm_id)
                match = _build_arm_match(
                    enriched,
                    arm=arm,
                    frame_1m=frame_1m,
                    frame_5m=prepared_5m,
                    level_frame_1h=level_frame_1h,
                    level_cache=level_cache,
                    level_scan_config=level_scan_config,
                    config=config,
                )
                match_rows.append(match)
                for exit_policy in LARGE_RUNNER_EXIT_POLICIES:
                    trade_rows.append(_simulate_trade(match, frame_1m=frame_1m, exit_policy=exit_policy, config=config))
        return {
            "raw": raw,
            "setups": setup_rows,
            "matches": match_rows,
            "trades": trade_rows,
            "quality": quality,
            "top_hours": top_hours,
        }

    results: dict[str, dict[str, object]] = {}
    workers = _effective_workers(config.symbol_workers, total_items=len(selected_symbols))
    if workers <= 1:
        for index, symbol in enumerate(selected_symbols, start=1):
            progress.update(index=index, item=symbol)
            results[symbol] = _process(symbol)
    else:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="large-runner-discovery")
        futures = {executor.submit(_process, symbol): symbol for symbol in selected_symbols}
        try:
            for index, future in enumerate(as_completed(futures), start=1):
                symbol = futures[future]
                progress.update(index=index, item=symbol)
                results[symbol] = future.result()
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            progress.finish()
            raise
        else:
            executor.shutdown(wait=True)
    progress.finish()

    raw_rows: list[dict[str, object]] = []
    setup_rows: list[dict[str, object]] = []
    match_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    quality_rows: list[dict[str, object]] = []
    top_hour_rows: list[dict[str, object]] = []
    for symbol in selected_symbols:
        result = results.get(symbol, {})
        raw_rows.extend(result.get("raw", []))  # type: ignore[arg-type]
        setup_rows.extend(result.get("setups", []))  # type: ignore[arg-type]
        match_rows.extend(result.get("matches", []))  # type: ignore[arg-type]
        trade_rows.extend(result.get("trades", []))  # type: ignore[arg-type]
        quality_rows.extend(result.get("quality", []))  # type: ignore[arg-type]
        top_hour_rows.extend(result.get("top_hours", []))  # type: ignore[arg-type]

    raw_frame = pd.DataFrame(raw_rows)
    setup_frame = pd.DataFrame(setup_rows)
    match_frame = pd.DataFrame(match_rows)
    trade_frame = pd.DataFrame(trade_rows)
    quality_frame = pd.DataFrame(quality_rows)
    top_growth_frame = _top_growth_frame(pd.DataFrame(top_hour_rows))
    top_coverage_frame = _top_growth_coverage(top_growth_frame, match_frame)
    level_attack_candidates_frame = _level_attack_candidates(setup_frame)
    level_attack_reject_reasons_frame = _level_attack_reject_reasons(setup_frame)
    level_attack_top_growth_coverage_frame = _level_attack_top_growth_coverage(top_growth_frame, setup_frame, match_frame)
    portfolio_frame, portfolio_events = _apply_research_portfolio(trade_frame, config=config)
    nature_summary_frame = _nature_summary(trade_frame, portfolio_frame, config=config)
    nature_by_week_frame = _nature_by_period(portfolio_frame, period="week", config=config)
    nature_by_symbol_frame = _nature_by_symbol(portfolio_frame, config=config)
    nature_top_dependency_frame = _nature_top_dependency(portfolio_frame)
    nature_sensitivity_frame = _nature_sensitivity(portfolio_frame, config=config)
    missed_top_growth_frame = _prefilter_missed_top_growth(
        top_growth_frame,
        raw_frame,
        setup_frame,
        match_frame,
        trade_frame,
        portfolio_frame,
    )
    top_growth_timing_frame = _top_growth_timing_audit(
        top_growth_frame,
        raw_frame,
        setup_frame,
        match_frame,
        trade_frame,
        portfolio_frame,
    )

    artifacts = [
        ("large_runner_candidates_raw.csv", raw_frame),
        ("large_runner_setups.csv", setup_frame),
        ("large_runner_rule_matches.csv", match_frame),
        ("large_runner_trade_grid.csv", trade_frame),
        ("large_runner_portfolio_trades.csv", portfolio_frame),
        ("large_runner_portfolio_events.csv", portfolio_events),
        ("large_runner_exit_policy_comparison.csv", _exit_policy_comparison(trade_frame)),
        ("large_runner_by_arm.csv", _summary_by(trade_frame, ["arm_id"])),
        ("large_runner_by_arm_exit_policy.csv", _summary_by(trade_frame, ["arm_id", "exit_policy_id"])),
        ("large_runner_by_setup_selection.csv", _summary_by(trade_frame, ["setup_selection_model"])),
        ("large_runner_portfolio_by_setup_selection.csv", _summary_by(portfolio_frame, ["setup_selection_model"])),
        ("large_runner_by_day.csv", _summary_by_day(trade_frame)),
        ("large_runner_mfe_mae.csv", _mfe_mae_frame(trade_frame)),
        ("large_runner_skip_reasons.csv", _skip_reasons(trade_frame)),
        ("large_runner_top_dependency.csv", _top_dependency(trade_frame)),
        ("large_runner_portfolio_top_dependency.csv", _top_dependency(portfolio_frame)),
        ("large_runner_stability_report.csv", _stability_report(portfolio_frame, config=config)),
        ("large_runner_level_attack_candidates.csv", level_attack_candidates_frame),
        ("large_runner_level_attack_reject_reasons.csv", level_attack_reject_reasons_frame),
        ("large_runner_level_attack_top_growth_coverage.csv", level_attack_top_growth_coverage_frame),
        ("large_runner_nature_summary.csv", nature_summary_frame),
        ("large_runner_nature_by_week.csv", nature_by_week_frame),
        ("large_runner_nature_by_symbol.csv", nature_by_symbol_frame),
        ("large_runner_nature_top_dependency.csv", nature_top_dependency_frame),
        ("large_runner_nature_sensitivity.csv", nature_sensitivity_frame),
        ("large_runner_prefilter_missed_top_growth.csv", missed_top_growth_frame),
        ("large_runner_top_growth_timing_audit.csv", top_growth_timing_frame),
        ("large_runner_top_growth.csv", top_growth_frame),
        ("large_runner_top_growth_coverage.csv", top_coverage_frame),
        ("large_runner_feature_deciles.csv", _feature_deciles(setup_frame)),
        ("large_runner_funnel.csv", _funnel(raw_frame, setup_frame, match_frame, trade_frame)),
        ("large_runner_data_quality.csv", quality_frame),
        (
            "run_config.csv",
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
                        "research_model": LARGE_RUNNER_DISCOVERY_ID,
                        "data_access_model": "cache_only_5m_1m_no_exchange_fetch",
                        "entry_model": "decision_close_then_next_1m_open_plus_adverse_slippage",
                        "future_label_model": "anomaly_seed_close_target_before_seed_low_break_evaluation_only",
                        "candidate_model": "first_broad_plus_first_prefilter_pass_promotion_per_symbol_per_60m_cluster",
                        "top_growth_audit_model": "evaluation_only_first15_full_hour_and_pre60_windows",
                        "level_attack_model": LEVEL_ATTACK_DISCOVERY_ID,
                        "level_attack_candidate_source": "cluster_selected_setups_not_only_arm_matches",
                        "level_attack_top_growth_coverage_model": "setup_level_attack_lookback_60m_to_first15m",
                        "post_entry_validation_model": "entry_plus_1m_2m_3m_evaluation_only",
                        "runtime_seconds": round(time.monotonic() - started_at, 3),
                    }
                ]
            ),
        ),
    ]
    for name, frame in artifacts:
        path = config.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(path, frame)

    summary = _metric_map(_summary_by(trade_frame, []))
    print(
        f"{progress_label}: done raw={len(raw_frame)} setups={len(setup_frame)} "
        f"matches={len(match_frame)} trades={int(summary.get('closed_trades', 0))} "
        f"avg_net={float(summary.get('avg_net_return', 0.0)):.4%} "
        f"win_rate={float(summary.get('win_rate', 0.0)):.2%}",
        flush=True,
    )
    return config.output_dir


def match_large_runner_arms(features: Mapping[str, object]) -> tuple[str, ...]:
    """Return arm ids using only entry-time fields from *features*."""
    early_return = _float(features.get("early_return_pct"))
    quote_ratio = _float(features.get("early_quote_ratio_24h_scaled"))
    trade_ratio = _float(features.get("early_trade_ratio_24h_scaled"))
    prior_quote = _float(features.get("current_vs_prior_max_quote_24h"))
    pre60_range = _float(features.get("pre60_range_pct"))
    pre60_min_path = _float(features.get("pre60_min_path_pct"))
    oi_ok = _bool(features.get("no_short_covering_oi"))
    sustain_mid = _bool(features.get("m1_sustain_mid"))
    sustain_strict = _bool(features.get("m1_sustain_strict"))
    confirm10_valid = _bool(features.get("confirm10_valid"))
    confirm15_valid = _bool(features.get("confirm15_valid"))
    close10 = _float(features.get("close_ret_10m"))
    close15 = _float(features.get("close_ret_15m"))
    wick10 = _float(features.get("wick_ret_10m"))
    wick15 = _float(features.get("wick_ret_15m"))

    arms: list[str] = []
    if early_return >= 0.050 and sustain_strict and prior_quote >= 0.50 and oi_ok:
        arms.append("E5_ignition_strict")
    if early_return >= 0.055 and quote_ratio >= 6.0 and trade_ratio >= 4.0 and oi_ok:
        arms.append("E5_mass_ignition")
    if (
        early_return >= 0.045
        and sustain_mid
        and pre60_range >= 0.020
        and pre60_min_path >= -0.060
        and oi_ok
    ):
        arms.append("preheat_ignition")
    if (
        confirm10_valid
        and early_return >= 0.030
        and close10 >= 0.070
        and wick10 <= 0.060
        and oi_ok
    ):
        arms.append("E10_confirmed_runner")
    if (
        confirm15_valid
        and early_return >= 0.020
        and close15 >= 0.100
        and wick15 <= 0.080
        and oi_ok
    ):
        arms.append("E15_exceptional_runner")
    return tuple(arms)


def _collect_raw_candidates(
    *,
    symbol: str,
    frame: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    config: LargeRunnerDiscoveryConfig,
) -> list[dict[str, object]]:
    if frame.empty:
        return []
    prepared = frame.copy()
    quote = prepared["quote_volume"]
    trades = prepared["number_of_trades"]
    positive_quote = quote.where(quote > 0)
    positive_trades = trades.where(trades > 0)
    baseline = int(config.baseline_5m_candles)
    prepared["baseline_quote_volume_median"] = positive_quote.shift(1).rolling(baseline, min_periods=12).median()
    prepared["baseline_number_of_trades_median"] = positive_trades.shift(1).rolling(baseline, min_periods=12).median()
    prepared["early_quote_ratio_24h_scaled"] = prepared["quote_volume"] / prepared["baseline_quote_volume_median"]
    prepared["early_trade_ratio_24h_scaled"] = prepared["number_of_trades"] / prepared["baseline_number_of_trades_median"]
    prepared["current_vs_prior_max_quote_24h"] = quote / positive_quote.shift(1).rolling(288, min_periods=12).max()
    prepared["current_vs_prior_max_trade_24h"] = trades / positive_trades.shift(1).rolling(288, min_periods=12).max()
    prepared["current_vs_prior_max_quote_48h"] = quote / positive_quote.shift(1).rolling(576, min_periods=12).max()
    prepared["current_vs_prior_max_trade_48h"] = trades / positive_trades.shift(1).rolling(576, min_periods=12).max()
    prepared["early_return_pct"] = _safe_divide_series(prepared["close"] - prepared["open"], prepared["open"])
    prepared["early_high_return_pct"] = _safe_divide_series(prepared["high"] - prepared["open"], prepared["open"])
    taker = prepared["taker_buy_quote_volume"] if "taker_buy_quote_volume" in prepared.columns else pd.Series(np.nan, index=prepared.index)
    prepared["early_taker_buy_quote_share"] = _safe_divide_series(taker, prepared["quote_volume"])

    scan = prepared.loc[(prepared["timestamp"] >= int(start_ms)) & (prepared["timestamp"] < int(end_ms))].copy()
    gate = (
        scan["early_return_pct"].ge(float(config.broad_min_return_pct))
        & scan["early_quote_ratio_24h_scaled"].ge(float(config.broad_min_quote_ratio))
        & scan["early_trade_ratio_24h_scaled"].ge(float(config.broad_min_trade_ratio))
    )
    rows: list[dict[str, object]] = []
    for idx, row in scan.loc[gate].iterrows():
        timestamp = int(row["timestamp"])
        pre = _pre60_features(prepared, history_end=int(idx))
        oi = _oi_features(prepared, index=int(idx))
        rows.append(
            {
                "symbol": symbol,
                "status": "ok",
                "candidate_model": "broad_5m_awakening",
                "timestamp_ms": timestamp,
                "timestamp_utc": _timestamp_to_utc(timestamp),
                "seed_open_ms": timestamp,
                "seed_close_ms": timestamp + FIVE_MINUTE_MS,
                "seed_close_utc": _timestamp_to_utc(timestamp + FIVE_MINUTE_MS),
                "cluster_start_ms": _cluster_start(timestamp, config=config),
                "cluster_start_utc": _timestamp_to_utc(_cluster_start(timestamp, config=config)),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "early_return_pct": float(row["early_return_pct"]),
                "early_high_return_pct": float(row["early_high_return_pct"]),
                "early_quote_sum": float(row["quote_volume"]),
                "early_trades_sum": float(row["number_of_trades"]),
                "early_quote_ratio_24h_scaled": float(row["early_quote_ratio_24h_scaled"]),
                "early_trade_ratio_24h_scaled": float(row["early_trade_ratio_24h_scaled"]),
                "early_taker_buy_quote_share": float(row["early_taker_buy_quote_share"]),
                "baseline_quote_volume_median": float(row["baseline_quote_volume_median"]),
                "baseline_number_of_trades_median": float(row["baseline_number_of_trades_median"]),
                "current_vs_prior_max_quote_24h": float(row["current_vs_prior_max_quote_24h"]),
                "current_vs_prior_max_trade_24h": float(row["current_vs_prior_max_trade_24h"]),
                "current_vs_prior_max_quote_48h": float(row["current_vs_prior_max_quote_48h"]),
                "current_vs_prior_max_trade_48h": float(row["current_vs_prior_max_trade_48h"]),
                **pre,
                **oi,
                "future_label_available_at_entry": False,
                "rule_feature_boundary": "known_at_seed_5m_close",
            }
        )
    return rows


def _cluster_setups_with_prefilter(
    raw: list[dict[str, object]],
    *,
    frame_5m: pd.DataFrame,
    config: LargeRunnerDiscoveryConfig,
) -> list[dict[str, object]]:
    """Select cluster setups without letting the first weak print hide later wake-up.

    The old research shortcut kept only the first broad 5m candidate in a
    60-minute cluster. That is cheaper, but it is not live-like: live would keep
    evaluating later closed candles after the first weak broad wake-up failed.
    This selector keeps that first row for audit continuity and adds at most one
    later row only when it already passes the same decision-time 5m prefilter.
    """
    del config
    groups: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in sorted(raw, key=lambda item: (str(item.get("symbol", "")), int(item.get("timestamp_ms", 0)))):
        key = (str(row.get("symbol", "")), int(row.get("cluster_start_ms", 0)))
        groups.setdefault(key, []).append(row)

    selected: list[dict[str, object]] = []
    for (_symbol, _cluster_start), rows in groups.items():
        first = rows[0]
        first_prefilter = _large_runner_5m_prefilter(first, frame_5m=frame_5m)
        first_row = {
            **first,
            **first_prefilter,
            "setup_selection_model": "first_broad_5m_awakening",
            "setup_cluster_raw_rank": 1,
            "cluster_initial_seed_open_ms": int(first.get("seed_open_ms", 0)),
            "cluster_initial_prefilter_passed": bool(first_prefilter["large_runner_5m_prefilter_passed"]),
            "cluster_promoted_after_initial_prefilter_fail": False,
        }
        selected.append(first_row)
        if bool(first_prefilter["large_runner_5m_prefilter_passed"]):
            continue
        for rank, candidate in enumerate(rows[1:], start=2):
            candidate_prefilter = _large_runner_5m_prefilter(candidate, frame_5m=frame_5m)
            if not bool(candidate_prefilter["large_runner_5m_prefilter_passed"]):
                continue
            selected.append(
                {
                    **candidate,
                    **candidate_prefilter,
                    "setup_selection_model": "first_prefilter_pass_after_initial_broad_fail",
                    "setup_cluster_raw_rank": int(rank),
                    "cluster_initial_seed_open_ms": int(first.get("seed_open_ms", 0)),
                    "cluster_initial_prefilter_passed": False,
                    "cluster_promoted_after_initial_prefilter_fail": True,
                }
            )
            break
    return selected


def _large_runner_5m_prefilter(setup: Mapping[str, object], *, frame_5m: pd.DataFrame) -> dict[str, object]:
    """Cheap known-by-decision-time gate for deciding whether 1m is needed."""
    seed_open = int(setup["seed_open_ms"])
    early_return = _float(setup.get("early_return_pct"))
    quote_ratio = _float(setup.get("early_quote_ratio_24h_scaled"))
    trade_ratio = _float(setup.get("early_trade_ratio_24h_scaled"))
    prior_quote = _float(setup.get("current_vs_prior_max_quote_24h"))
    pre60_range = _float(setup.get("pre60_range_pct"))
    pre60_min_path = _float(setup.get("pre60_min_path_pct"))
    oi_ok = bool(_nan_or_ge(setup.get("oi_change_early_pct"), -0.005) and _nan_or_ge(setup.get("oi_change_pre60_pct"), -0.020))
    close10, high10, wick10, valid10 = _closed_5m_projection(frame_5m, seed_open=seed_open, minutes=10)
    close15, high15, wick15, valid15 = _closed_5m_projection(frame_5m, seed_open=seed_open, minutes=15)
    possible_e5 = bool(
        oi_ok
        and (
            (early_return >= 0.050 and prior_quote >= 0.50)
            or (early_return >= 0.055 and quote_ratio >= 6.0 and trade_ratio >= 4.0)
            or (early_return >= 0.045 and pre60_range >= 0.020 and pre60_min_path >= -0.060)
        )
    )
    possible_e10 = bool(valid10 and oi_ok and early_return >= 0.030 and close10 >= 0.070 and wick10 <= 0.060)
    possible_e15 = bool(valid15 and oi_ok and early_return >= 0.020 and close15 >= 0.100 and wick15 <= 0.080)
    passed = bool(possible_e5 or possible_e10 or possible_e15)
    possible = []
    if possible_e5:
        possible.append("E5")
    if possible_e10:
        possible.append("E10")
    if possible_e15:
        possible.append("E15")
    return {
        "large_runner_5m_prefilter_passed": passed,
        "large_runner_5m_prefilter_possible_offsets": "|".join(possible),
        "large_runner_5m_prefilter_model": "entry_time_only_closed_5m_upper_gate_for_1m_enrichment",
        "prefilter_close_ret_10m": close10,
        "prefilter_high_ret_10m": high10,
        "prefilter_wick_ret_10m": wick10,
        "prefilter_valid_10m": valid10,
        "prefilter_close_ret_15m": close15,
        "prefilter_high_ret_15m": high15,
        "prefilter_wick_ret_15m": wick15,
        "prefilter_valid_15m": valid15,
    }


def _closed_5m_projection(frame_5m: pd.DataFrame, *, seed_open: int, minutes: int) -> tuple[float, float, float, bool]:
    end_ms = int(seed_open) + int(minutes) * MINUTE_MS
    window = _window(frame_5m, int(seed_open), end_ms)
    expected = int(minutes // 5)
    if len(window) < expected or window.empty:
        return float("nan"), float("nan"), float("nan"), False
    first_open = float(window.iloc[0]["open"])
    close_ret = _safe_divide(float(window.iloc[-1]["close"]) - first_open, first_open)
    high_ret = _safe_divide(float(window["high"].max()) - first_open, first_open)
    return close_ret, high_ret, high_ret - close_ret, True


def _enrich_setup(
    setup: Mapping[str, object],
    *,
    frame_5m: pd.DataFrame,
    frame_1m: pd.DataFrame,
    config: LargeRunnerDiscoveryConfig,
) -> dict[str, object]:
    seed_open = int(setup["seed_open_ms"])
    seed_close = int(setup["seed_close_ms"])
    seed = _minute_window_features(
        frame_1m,
        start_ms=seed_open,
        end_ms=seed_close,
        expected_candles=5,
        baseline_quote_5m=_float(setup.get("baseline_quote_volume_median")),
        baseline_trades_5m=_float(setup.get("baseline_number_of_trades_median")),
        prefix="m1",
    )
    confirm10 = _confirm_features(frame_1m, seed_open=seed_open, minutes=10, prefix="confirm10")
    confirm15 = _confirm_features(frame_1m, seed_open=seed_open, minutes=15, prefix="confirm15")
    labels = _future_labels(
        frame_1m,
        seed_open=seed_open,
        seed_close=seed_close,
        anomaly_low=_float(setup.get("low")),
        anomaly_close=_float(setup.get("close")),
        config=config,
    )
    row = {**dict(setup), **seed, **confirm10, **confirm15, **labels}
    row["m1_sustain_mid"] = bool(
        row.get("m1_valid_count", 0) >= 5
        and _float(row.get("m1_elevated_both_count_3x")) >= 3
        and _float(row.get("m1_quote_top1_share")) <= 0.65
        and _float(row.get("m1_last2_quote_share")) >= 0.25
    )
    row["m1_sustain_strict"] = bool(
        row.get("m1_valid_count", 0) >= 5
        and _float(row.get("m1_elevated_both_count_3x")) >= 3
        and _float(row.get("m1_quote_top1_share")) <= 0.60
        and _float(row.get("m1_last2_quote_share")) >= 0.30
        and _float(row.get("m1_last2_trade_share")) >= 0.30
    )
    row["no_short_covering_oi"] = bool(
        _nan_or_ge(row.get("oi_change_early_pct"), -0.005)
        and _nan_or_ge(row.get("oi_change_pre60_pct"), -0.020)
    )
    row.update(evaluate_large_runner_nature(row).as_features())
    row["future_label_available_at_entry"] = False
    row["future_label_assignment_model"] = "after_setup_construction_evaluation_only"
    del frame_5m
    return row


def _build_arm_match(
    setup: Mapping[str, object],
    *,
    arm: LargeRunnerArm,
    frame_1m: pd.DataFrame,
    frame_5m: pd.DataFrame,
    level_frame_1h: pd.DataFrame,
    level_cache: dict[int, tuple[list[HourlyLevelMetric], str, str]],
    level_scan_config: HourlyLevelScanConfig,
    config: LargeRunnerDiscoveryConfig,
) -> dict[str, object]:
    decision_ms = int(setup["seed_open_ms"]) + int(arm.decision_offset_minutes) * MINUTE_MS
    entry_ms = decision_ms
    entry_row = _row_at_timestamp(frame_1m, entry_ms)
    entry_skip_reason = ""
    raw_entry_price = float("nan")
    entry_price = float("nan")
    initial_stop = float("nan")
    initial_risk_pct = float("nan")
    if entry_row is None:
        entry_skip_reason = "missing_next_1m_open_at_decision"
    else:
        raw_entry_price = float(entry_row["open"])
        entry_price = raw_entry_price * (1.0 + float(config.entry_slippage_pct))
        stop_window = _window(frame_1m, int(setup["seed_open_ms"]), entry_ms)
        if stop_window.empty:
            stop_low = _float(setup.get("low"))
        else:
            stop_low = float(stop_window["low"].min())
        initial_stop = stop_low * (1.0 - float(config.structural_stop_buffer_pct))
        initial_risk_pct = _safe_divide(entry_price - initial_stop, entry_price)
        if not np.isfinite(initial_risk_pct) or initial_risk_pct <= 0.0:
            entry_skip_reason = "invalid_initial_risk"
        elif initial_risk_pct > float(config.max_initial_risk_pct):
            entry_skip_reason = "initial_risk_above_max"
    result = {
        **dict(setup),
        "arm_id": arm.arm_id,
        "arm_priority": int(arm.priority),
        "decision_offset_minutes": int(arm.decision_offset_minutes),
        "decision_timestamp_ms": decision_ms,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ms),
        "entry_timestamp_ms": entry_ms,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ms),
        "entry_price_model": "next_1m_open_plus_adverse_slippage",
        "raw_entry_price": raw_entry_price,
        "entry_price": entry_price,
        "initial_stop": initial_stop,
        "initial_risk_pct": initial_risk_pct,
        "entry_execution_skip_reason": entry_skip_reason,
        "large_runner_policy_id": LARGE_RUNNER_DISCOVERY_ID,
        "future_label_available_at_entry": False,
    }
    result.update(_session_features(decision_ms))
    result.update(
        _level_attack_features(
            result,
            frame_5m=frame_5m,
            level_frame_1h=level_frame_1h,
            level_cache=level_cache,
            level_scan_config=level_scan_config,
        )
    )
    result.update(evaluate_large_runner_nature(result).as_features())
    # Evaluation-only: added after arm/nature-independent entry construction.
    result.update(_post_entry_validation_features(result, frame_1m=frame_1m))
    return result


def _simulate_trade(
    match: Mapping[str, object],
    *,
    frame_1m: pd.DataFrame,
    exit_policy: ExitPolicySpec,
    config: LargeRunnerDiscoveryConfig,
) -> dict[str, object]:
    if str(match.get("entry_execution_skip_reason", "")):
        return {**dict(match), "exit_policy_id": exit_policy.policy_id, "status": "skipped", "skip_reason": match.get("entry_execution_skip_reason", "")}
    entry_ms = int(match["entry_timestamp_ms"])
    seed_open = int(match["seed_open_ms"])
    horizon_end = seed_open + int(config.horizon_minutes) * MINUTE_MS
    path = _window(frame_1m, entry_ms, horizon_end + MINUTE_MS)
    if path.empty:
        return {**dict(match), "exit_policy_id": exit_policy.policy_id, "status": "skipped", "skip_reason": "missing_post_entry_1m_path"}
    entry_price = float(match["entry_price"])
    stop = float(match["initial_stop"])
    risk = entry_price - stop
    if not np.isfinite(risk) or risk <= 0.0:
        return {**dict(match), "exit_policy_id": exit_policy.policy_id, "status": "skipped", "skip_reason": "invalid_initial_risk"}
    risk_pct = _safe_divide(risk, entry_price)
    remaining = 1.0
    gross_return = 0.0
    tp_hit = False
    be_armed = False
    mfe = 0.0
    mae = 0.0
    exit_ts = int(path.iloc[-1]["timestamp"])
    exit_price = float(path.iloc[-1]["close"]) * (1.0 - float(config.exit_slippage_pct))
    exit_reason = "horizon_close"
    pending_kill = ""
    highs_since_entry: list[float] = []
    lows_since_entry: list[float] = []

    for idx, row in path.reset_index(drop=True).iterrows():
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        ts = int(row["timestamp"])
        if pending_kill:
            exit_ts = ts
            exit_price = open_price * (1.0 - float(config.exit_slippage_pct))
            exit_reason = pending_kill
            break

        highs_since_entry.append(high)
        lows_since_entry.append(low)
        mfe = max(mfe, _safe_divide(high - entry_price, entry_price))
        mae = min(mae, _safe_divide(low - entry_price, entry_price))

        tp_price = entry_price + risk * float(exit_policy.tp_r) if exit_policy.tp_r > 0.0 else float("nan")
        stop_hit = low <= stop
        tp_hit_now = bool(exit_policy.tp_close_fraction > 0.0 and np.isfinite(tp_price) and high >= tp_price and remaining > 1e-12 and not tp_hit)
        if stop_hit:
            exit_ts = ts
            exit_price = stop * (1.0 - float(config.exit_slippage_pct))
            gross_return += remaining * _safe_divide(exit_price - entry_price, entry_price)
            remaining = 0.0
            exit_reason = "initial_or_trailing_stop"
            break
        if tp_hit_now:
            fraction = min(float(exit_policy.tp_close_fraction), remaining)
            tp_exit = tp_price * (1.0 - float(config.exit_slippage_pct))
            gross_return += fraction * _safe_divide(tp_exit - entry_price, entry_price)
            remaining -= fraction
            tp_hit = True
        if exit_policy.be_after_r > 0.0 and high >= entry_price + risk * float(exit_policy.be_after_r):
            be_armed = True
            stop = max(stop, entry_price)

        trailing_low = min(lows_since_entry[-int(config.trail_lookback_1m) :])
        stop = max(stop, trailing_low * (1.0 - float(config.structural_stop_buffer_pct)))
        if be_armed:
            stop = max(stop, entry_price)

        if exit_policy.early_kill_minutes > 0 and ts >= entry_ms + int(exit_policy.early_kill_minutes) * MINUTE_MS:
            max_high = max(highs_since_entry)
            kill_by_mfe = _safe_divide(max_high - entry_price, entry_price) < float(exit_policy.early_kill_min_mfe_pct)
            kill_by_close = bool(exit_policy.early_kill_require_close_above_entry and close <= entry_price)
            if kill_by_mfe or kill_by_close:
                pending_kill = "early_kill_no_progress_next_open"

        if idx == len(path) - 1:
            exit_ts = ts
            exit_price = close * (1.0 - float(config.exit_slippage_pct))
            exit_reason = "horizon_close"
            break

    if remaining > 0.0:
        gross_return += remaining * _safe_divide(exit_price - entry_price, entry_price)
    net_return = gross_return - float(config.fee_rate) * 2.0
    return {
        **dict(match),
        "exit_policy_id": exit_policy.policy_id,
        "exit_tp_r": float(exit_policy.tp_r),
        "exit_tp_close_fraction": float(exit_policy.tp_close_fraction),
        "exit_be_after_r": float(exit_policy.be_after_r),
        "exit_early_kill_minutes": int(exit_policy.early_kill_minutes),
        "status": "closed",
        "skip_reason": "",
        "exit_timestamp_ms": int(exit_ts),
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "tp_hit": bool(tp_hit),
        "mfe_pct": float(mfe),
        "mae_pct": float(mae),
        "gross_return": float(gross_return),
        "net_return": float(net_return),
        "gross_r": float(_safe_divide(gross_return, risk_pct)),
        "net_r": float(_safe_divide(net_return, risk_pct)),
        "initial_risk_pct": float(risk_pct),
        "win": bool(net_return > 0.0),
        "future_label_available_at_entry": False,
    }


def _post_entry_validation_features(
    match: Mapping[str, object],
    *,
    frame_1m: pd.DataFrame,
) -> dict[str, object]:
    """Evaluation-only 1m/2m/3m anti-fader labels."""
    horizons = (1, 2, 3)
    result: dict[str, object] = {
        "post_entry_validation_model": "entry_plus_1m_2m_3m_evaluation_only",
        "post_entry_validation_available_at_entry": False,
    }
    for minutes in horizons:
        result.update(
            {
                f"entry_plus_{minutes}m_close_R": float("nan"),
                f"entry_plus_{minutes}m_high_R": float("nan"),
                f"entry_plus_{minutes}m_low_R": float("nan"),
                f"new_HH_within_{minutes}m": False,
                f"quote_retention_{minutes}m": float("nan"),
                f"trade_retention_{minutes}m": float("nan"),
                f"taker_buy_retention_{minutes}m": float("nan"),
                f"oi_change_{minutes}m": float("nan"),
            }
        )
    result.update(
        {
            "hit_0p25R_before_neg_0p35R_within_3m": False,
            "hit_0p50R_before_neg_0p50R_within_3m": False,
            "mfe_before_mae": False,
            "post_entry_validation_status": "missing_or_invalid_entry",
        }
    )
    if frame_1m.empty:
        result["post_entry_validation_status"] = "missing_1m_frame"
        return result

    entry_ms = int(_float(match.get("entry_timestamp_ms")))
    entry_price = _float(match.get("entry_price"))
    initial_stop = _float(match.get("initial_stop"))
    risk = entry_price - initial_stop
    if not np.isfinite(entry_price) or not np.isfinite(risk) or risk <= 0.0:
        return result

    seed_open = int(_float(match.get("seed_open_ms")))
    pre_entry = _window(frame_1m, seed_open, entry_ms)
    previous_high = _float(match.get("high")) if pre_entry.empty else float(pd.to_numeric(pre_entry["high"], errors="coerce").max())
    seed_quote = _float(match.get("m1_quote_sum"))
    seed_trades = _float(match.get("m1_trades_sum"))
    seed_taker_share = _float(match.get("m1_taker_buy_quote_share"))
    seed_taker = seed_quote * seed_taker_share if np.isfinite(seed_quote) and np.isfinite(seed_taker_share) else float("nan")
    entry_row = _row_at_timestamp(frame_1m, entry_ms)
    entry_oi = _float(entry_row.get("open_interest")) if entry_row is not None and "open_interest" in entry_row.index else float("nan")
    full_path = _window(frame_1m, entry_ms, entry_ms + 3 * MINUTE_MS)
    if full_path.empty:
        result["post_entry_validation_status"] = "missing_post_entry_1m_path"
        return result

    for minutes in horizons:
        path = _window(frame_1m, entry_ms, entry_ms + int(minutes) * MINUTE_MS)
        if path.empty:
            continue
        high = float(pd.to_numeric(path["high"], errors="coerce").max())
        low = float(pd.to_numeric(path["low"], errors="coerce").min())
        close = float(path.iloc[-1]["close"])
        result[f"entry_plus_{minutes}m_close_R"] = float(_safe_divide(close - entry_price, risk))
        result[f"entry_plus_{minutes}m_high_R"] = float(_safe_divide(high - entry_price, risk))
        result[f"entry_plus_{minutes}m_low_R"] = float(_safe_divide(low - entry_price, risk))
        result[f"new_HH_within_{minutes}m"] = bool(np.isfinite(previous_high) and high > previous_high)
        quote_sum = float(_series(path, "quote_volume").sum())
        trade_sum = float(_series(path, "number_of_trades").sum())
        taker_sum = float(_series(path, "taker_buy_quote_volume").sum())
        result[f"quote_retention_{minutes}m"] = float(_safe_divide(quote_sum, seed_quote))
        result[f"trade_retention_{minutes}m"] = float(_safe_divide(trade_sum, seed_trades))
        result[f"taker_buy_retention_{minutes}m"] = float(_safe_divide(taker_sum, seed_taker))
        if "open_interest" in path.columns and np.isfinite(entry_oi):
            end_oi = _float(path.iloc[-1].get("open_interest"))
            result[f"oi_change_{minutes}m"] = float(_safe_divide(end_oi - entry_oi, entry_oi))

    result["hit_0p25R_before_neg_0p35R_within_3m"] = _hit_positive_before_negative_r(
        full_path,
        entry_price=entry_price,
        risk=risk,
        positive_r=0.25,
        negative_r=-0.35,
    )
    result["hit_0p50R_before_neg_0p50R_within_3m"] = _hit_positive_before_negative_r(
        full_path,
        entry_price=entry_price,
        risk=risk,
        positive_r=0.50,
        negative_r=-0.50,
    )
    result["mfe_before_mae"] = _mfe_before_mae(full_path, entry_price=entry_price)
    result["post_entry_validation_status"] = "ok" if len(full_path) >= 3 else "partial_1m_path"
    return result


def _hit_positive_before_negative_r(
    path: pd.DataFrame,
    *,
    entry_price: float,
    risk: float,
    positive_r: float,
    negative_r: float,
) -> bool:
    if path.empty or not np.isfinite(entry_price) or not np.isfinite(risk) or risk <= 0.0:
        return False
    positive_price = float(entry_price) + float(risk) * float(positive_r)
    negative_price = float(entry_price) + float(risk) * float(negative_r)
    for _, row in path.sort_values("timestamp").iterrows():
        # Same-candle ambiguity is conservative: adverse move wins the tie.
        if _float(row.get("low")) <= negative_price:
            return False
        if _float(row.get("high")) >= positive_price:
            return True
    return False


def _mfe_before_mae(path: pd.DataFrame, *, entry_price: float) -> bool:
    if path.empty or not np.isfinite(entry_price):
        return False
    for _, row in path.sort_values("timestamp").iterrows():
        high = _float(row.get("high"))
        low = _float(row.get("low"))
        high_move = high - float(entry_price) if np.isfinite(high) else 0.0
        low_move = float(entry_price) - low if np.isfinite(low) else 0.0
        if low_move > 0.0 and low_move >= high_move:
            return False
        if high_move > 0.0 and high_move > low_move:
            return True
    return False


def _level_attack_scan_config(config: LargeRunnerDiscoveryConfig) -> HourlyLevelScanConfig:
    return HourlyLevelScanConfig(
        cache_dir=config.cache_dir,
        output_dir=config.output_dir / "_level_attack_internal",
        source_timeframe=Timeframe.M5,
        days=min(max(int(config.days), 30), 180),
        lookback_bars=720,
        chart_bars=0,
        min_touches=2,
        min_touch_spacing_hours=6,
        min_target_room_pct=0.0,
        max_overhead_distance_pct=0.60,
        reject_downtrend_symbols=False,
        reject_downtrend_levels=False,
        reject_pierced_levels=False,
        save_empty_charts=False,
        fast_source_trim=True,
    )


def _aggregate_5m_to_1h(frame_5m: pd.DataFrame) -> pd.DataFrame:
    if frame_5m.empty or "timestamp" not in frame_5m.columns:
        return pd.DataFrame()
    prepared = frame_5m.copy()
    ts = pd.to_datetime(pd.to_numeric(prepared["timestamp"], errors="coerce"), unit="ms", utc=True)
    prepared.index = ts
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume", "open_interest"):
        if column in prepared.columns:
            aggregation[column] = "sum" if column != "open_interest" else "last"
    hourly = prepared.resample("1h", label="left", closed="left").agg(aggregation)
    hourly = hourly.dropna(subset=["open", "high", "low", "close"]).copy()
    if hourly.empty:
        return pd.DataFrame()
    hourly["timestamp"] = (hourly.index.view("int64") // 1_000_000).astype("int64")
    return hourly.reset_index(drop=True)


def _level_attack_features(
    match: Mapping[str, object],
    *,
    frame_5m: pd.DataFrame,
    level_frame_1h: pd.DataFrame,
    level_cache: dict[int, tuple[list[HourlyLevelMetric], str, str]],
    level_scan_config: HourlyLevelScanConfig,
) -> dict[str, object]:
    base = _empty_level_attack_features("missing_or_invalid")
    if level_frame_1h.empty:
        base["h1_level_attack_status"] = "missing_1h_level_frame"
        return base
    symbol = str(match.get("symbol", ""))
    seed_open = int(_float(match.get("seed_open_ms")))
    seed_hour = seed_open // HOUR_MS * HOUR_MS
    if seed_hour not in level_cache:
        history = level_frame_1h.loc[pd.to_numeric(level_frame_1h["timestamp"], errors="coerce") < seed_hour].tail(720).copy()
        if history.empty:
            level_cache[seed_hour] = ([], "unknown", "empty_closed_h1_history")
        else:
            level_cache[seed_hour] = find_hourly_overhead_levels(
                history,
                symbol=symbol,
                config=level_scan_config,
                chart_path="",
            )
    levels, trend, reason = level_cache[seed_hour]
    if not levels:
        base["h1_level_attack_status"] = f"no_level:{reason}"
        base["h1_level_trend_state"] = trend
        return base

    seed_open_price = _float(match.get("open"))
    seed_high = _float(match.get("high"))
    seed_close = _float(match.get("close"))
    entry_price = _float(match.get("entry_price"))
    if not np.isfinite(entry_price) or entry_price <= 0.0:
        entry_price = seed_close
    initial_risk_pct = _float(match.get("initial_risk_pct"))
    risk_abs = entry_price * initial_risk_pct if np.isfinite(entry_price) and np.isfinite(initial_risk_pct) else float("nan")
    if not np.isfinite(risk_abs) or risk_abs <= 0.0:
        seed_low = _float(match.get("low"))
        risk_abs = max(entry_price - seed_low, entry_price * 0.02) if np.isfinite(seed_low) and seed_low > 0.0 else float("nan")
    reference_price = seed_open_price if np.isfinite(seed_open_price) and seed_open_price > 0 else seed_close
    above_levels = [level for level in levels if level.level_price > reference_price]
    if not above_levels:
        base["h1_level_attack_status"] = "no_level_above_seed_open"
        base["h1_level_trend_state"] = trend
        return base
    nearest = min(above_levels, key=lambda level: level.level_price - reference_price)
    level_price = float(nearest.level_price)
    tolerance = float(level_scan_config.touch_tolerance_pct)
    history_1h = level_frame_1h.loc[pd.to_numeric(level_frame_1h["timestamp"], errors="coerce") < seed_hour].copy()
    after_touch = history_1h.loc[pd.to_numeric(history_1h["timestamp"], errors="coerce") >= int(nearest.last_touch_timestamp_ms)].copy()
    pullback_low = float(pd.to_numeric(after_touch["low"], errors="coerce").min()) if not after_touch.empty else float("nan")
    pullback_size = _safe_divide(level_price - pullback_low, level_price)
    progress = _safe_divide(seed_close - pullback_low, level_price - pullback_low)

    crossed_high = bool(np.isfinite(seed_high) and seed_high >= level_price)
    crossed_close = bool(np.isfinite(seed_close) and seed_close >= level_price)
    entry_mode = _level_entry_mode(
        entry_price=entry_price,
        level_price=level_price,
        seed_high_crossed=crossed_high,
        seed_close_crossed=crossed_close,
        tolerance_pct=tolerance,
    )
    prior_spike_count, quote_vs_prior, trades_vs_prior = _level_attack_flow_vs_prior(
        frame_5m,
        seed_open_ms=seed_open,
        level_price=level_price,
        tolerance_pct=tolerance,
        current_quote=_float(match.get("early_quote_sum")),
        current_trades=_float(match.get("early_trades_sum")),
    )
    level_dist_r = _safe_divide(level_price - entry_price, risk_abs)
    flow_beats_prior = bool(
        (np.isfinite(quote_vs_prior) and quote_vs_prior >= 1.0)
        or (np.isfinite(trades_vs_prior) and trades_vs_prior >= 1.0)
    )
    result = {
        **base,
        "h1_level_attack_status": "ok",
        "h1_level_trend_state": trend,
        "h1_nearest_level_price": level_price,
        "h1_nearest_level_context": nearest.context,
        "h1_nearest_level_strength_score": float(nearest.strength_score),
        "h1_nearest_level_valid_touch_count": int(nearest.valid_touch_count),
        "h1_nearest_level_last_touch_timestamp_ms": int(nearest.last_touch_timestamp_ms),
        "h1_nearest_level_last_touch_utc": nearest.last_touch_timestamp_utc,
        "h1_nearest_level_distance_pct_from_seed_close": _safe_divide(level_price - seed_close, seed_close),
        "h1_nearest_level_distance_R_from_entry": level_dist_r,
        "h1_pullback_low_after_last_touch": pullback_low,
        "h1_pullback_from_level_pct": pullback_size,
        "h1_progress_to_level_from_pullback": progress,
        "h1_is_in_attack_zone_70": bool(np.isfinite(progress) and progress >= 0.70 and seed_close <= level_price * (1.0 + tolerance)),
        "h1_level_crossed_by_seed_high": crossed_high,
        "h1_level_crossed_by_seed_close": crossed_close,
        "h1_seed_close_above_level": crossed_close,
        "h1_entry_mode_vs_nearest_level": entry_mode,
        "h1_entry_before_level_break": bool(entry_mode == "advance_before_level"),
        "h1_entry_in_level_crossing": bool(entry_mode in {"seed_high_crossing", "seed_close_crossing", "entry_in_level_band"}),
        "h1_attack_prior_spike_count": int(prior_spike_count),
        "h1_attack_quote_vs_prior_spike_max": quote_vs_prior,
        "h1_attack_trades_vs_prior_spike_max": trades_vs_prior,
        "h1_attack_flow_beats_prior_spikes": flow_beats_prior,
    }
    result["h1_level_attack_candidate"] = _is_level_attack_candidate(result)
    result["h1_level_attack_reject_reason"] = _level_attack_reject_reason(result)
    for r_value in (1, 2, 3):
        result[f"h1_levels_above_within_{r_value}R"] = _levels_above_within_r(levels, entry_price=entry_price, risk_abs=risk_abs, r_value=float(r_value))
    result["h1_remaining_overhead_level_count_3R"] = result["h1_levels_above_within_3R"]
    result["h1_level_cascade_score_3R"] = _level_cascade_score(levels, entry_price=entry_price, risk_abs=risk_abs, max_r=3.0)
    return result



def _annotate_setup_level_attack(
    setup: Mapping[str, object],
    *,
    frame_5m: pd.DataFrame,
    level_frame_1h: pd.DataFrame,
    level_cache: dict[int, tuple[list[HourlyLevelMetric], str, str]],
    level_scan_config: HourlyLevelScanConfig,
) -> dict[str, object]:
    """Attach cheap H1 level-attack context to a 5m setup before arm matching.

    This is the recall layer: it runs on cluster-selected setups, including
    setups that fail the old large-runner 5m prefilter. It uses only closed H1
    candles before the seed hour and 5m seed fields known at seed close.
    """
    row = dict(setup)
    seed_close_ms = int(_float(row.get("seed_close_ms")))
    if math.isfinite(seed_close_ms):
        row.update(_session_features(seed_close_ms))
    row.update(
        _level_attack_features(
            row,
            frame_5m=frame_5m,
            level_frame_1h=level_frame_1h,
            level_cache=level_cache,
            level_scan_config=level_scan_config,
        )
    )
    return row


def _is_level_attack_candidate(features: Mapping[str, object]) -> bool:
    if str(features.get("h1_level_attack_status", "")) != "ok":
        return False
    progress = _float(features.get("h1_progress_to_level_from_pullback"))
    distance_pct = _float(features.get("h1_nearest_level_distance_pct_from_seed_close"))
    crossed_high = _bool(features.get("h1_level_crossed_by_seed_high"))
    crossed_close = _bool(features.get("h1_level_crossed_by_seed_close"))
    in_band_or_before = str(features.get("h1_entry_mode_vs_nearest_level", "")) in {
        "advance_before_level",
        "entry_in_level_band",
        "seed_high_crossing",
        "seed_close_crossing",
    }
    near_level = bool(np.isfinite(distance_pct) and -0.003 <= distance_pct <= 0.08)
    progressed = bool(np.isfinite(progress) and progress >= 0.55)
    prior_spikes = _float(features.get("h1_attack_prior_spike_count"))
    flow_ok = _bool(features.get("h1_attack_flow_beats_prior_spikes")) or (np.isfinite(prior_spikes) and prior_spikes == 0)
    return bool((progressed or near_level or crossed_high or crossed_close) and in_band_or_before and flow_ok)


def _level_attack_reject_reason(features: Mapping[str, object]) -> str:
    status = str(features.get("h1_level_attack_status", ""))
    if status != "ok":
        return status or "not_computed"
    if _is_level_attack_candidate(features):
        return "candidate"
    entry_mode = str(features.get("h1_entry_mode_vs_nearest_level", ""))
    if entry_mode == "after_level_break":
        return "already_after_level_break"
    progress = _float(features.get("h1_progress_to_level_from_pullback"))
    distance_pct = _float(features.get("h1_nearest_level_distance_pct_from_seed_close"))
    if np.isfinite(progress) and progress < 0.55:
        return "progress_to_level_too_low"
    if np.isfinite(distance_pct) and distance_pct > 0.08:
        return "too_far_from_level"
    prior_spikes = _float(features.get("h1_attack_prior_spike_count"))
    if np.isfinite(prior_spikes) and prior_spikes > 0 and not _bool(features.get("h1_attack_flow_beats_prior_spikes")):
        return "flow_weaker_than_prior_level_attacks"
    return "not_candidate"

def _empty_level_attack_features(status: str) -> dict[str, object]:
    result: dict[str, object] = {
        "h1_level_attack_model": LEVEL_ATTACK_DISCOVERY_ID,
        "h1_level_attack_available_at_entry": False,
        "h1_level_attack_status": status,
        "h1_level_trend_state": "unknown",
        "h1_nearest_level_price": float("nan"),
        "h1_nearest_level_context": "",
        "h1_nearest_level_strength_score": float("nan"),
        "h1_nearest_level_valid_touch_count": 0,
        "h1_nearest_level_last_touch_timestamp_ms": float("nan"),
        "h1_nearest_level_last_touch_utc": "",
        "h1_nearest_level_distance_pct_from_seed_close": float("nan"),
        "h1_nearest_level_distance_R_from_entry": float("nan"),
        "h1_pullback_low_after_last_touch": float("nan"),
        "h1_pullback_from_level_pct": float("nan"),
        "h1_progress_to_level_from_pullback": float("nan"),
        "h1_is_in_attack_zone_70": False,
        "h1_level_crossed_by_seed_high": False,
        "h1_level_crossed_by_seed_close": False,
        "h1_seed_close_above_level": False,
        "h1_entry_mode_vs_nearest_level": "no_level",
        "h1_entry_before_level_break": False,
        "h1_entry_in_level_crossing": False,
        "h1_attack_prior_spike_count": 0,
        "h1_attack_quote_vs_prior_spike_max": float("nan"),
        "h1_attack_trades_vs_prior_spike_max": float("nan"),
        "h1_attack_flow_beats_prior_spikes": False,
        "h1_level_attack_candidate": False,
        "h1_level_attack_reject_reason": status,
        "h1_remaining_overhead_level_count_3R": 0,
        "h1_level_cascade_score_3R": float("nan"),
    }
    for r_value in (1, 2, 3):
        result[f"h1_levels_above_within_{r_value}R"] = 0
    return result


def _level_entry_mode(
    *,
    entry_price: float,
    level_price: float,
    seed_high_crossed: bool,
    seed_close_crossed: bool,
    tolerance_pct: float,
) -> str:
    if not np.isfinite(entry_price) or not np.isfinite(level_price) or level_price <= 0.0:
        return "unknown"
    band = level_price * float(tolerance_pct)
    if entry_price >= level_price + band:
        return "after_level_break"
    if abs(entry_price - level_price) <= band:
        return "entry_in_level_band"
    if seed_close_crossed:
        return "seed_close_crossing"
    if seed_high_crossed:
        return "seed_high_crossing"
    return "advance_before_level"


def _level_attack_flow_vs_prior(
    frame_5m: pd.DataFrame,
    *,
    seed_open_ms: int,
    level_price: float,
    tolerance_pct: float,
    current_quote: float,
    current_trades: float,
) -> tuple[int, float, float]:
    if frame_5m.empty or level_price <= 0.0:
        return 0, float("nan"), float("nan")
    timestamps = pd.to_numeric(frame_5m["timestamp"], errors="coerce")
    history = frame_5m.loc[timestamps < int(seed_open_ms)].copy()
    if history.empty:
        return 0, float("nan"), float("nan")
    band_low = level_price * (1.0 - max(float(tolerance_pct), 0.0))
    band_high = level_price * (1.0 + max(float(tolerance_pct), 0.0))
    highs = pd.to_numeric(history["high"], errors="coerce")
    closes = pd.to_numeric(history["close"], errors="coerce")
    attempts = history.loc[highs.between(band_low, band_high, inclusive="both") & closes.lt(band_high)].copy()
    if attempts.empty:
        return 0, float("nan"), float("nan")
    quote = pd.to_numeric(attempts.get("quote_volume", pd.Series(index=attempts.index)), errors="coerce")
    trades = pd.to_numeric(attempts.get("number_of_trades", pd.Series(index=attempts.index)), errors="coerce")
    return int(len(attempts)), _safe_divide(current_quote, float(quote.max())), _safe_divide(current_trades, float(trades.max()))


def _levels_above_within_r(levels: list[HourlyLevelMetric], *, entry_price: float, risk_abs: float, r_value: float) -> int:
    if not np.isfinite(entry_price) or not np.isfinite(risk_abs) or risk_abs <= 0.0:
        return 0
    count = 0
    for level in levels:
        distance_r = _safe_divide(float(level.level_price) - entry_price, risk_abs)
        if np.isfinite(distance_r) and 0.0 < distance_r <= float(r_value):
            count += 1
    return count


def _level_cascade_score(levels: list[HourlyLevelMetric], *, entry_price: float, risk_abs: float, max_r: float) -> float:
    if not np.isfinite(entry_price) or not np.isfinite(risk_abs) or risk_abs <= 0.0:
        return float("nan")
    score = 0.0
    for level in levels:
        distance_r = _safe_divide(float(level.level_price) - entry_price, risk_abs)
        if np.isfinite(distance_r) and 0.0 < distance_r <= float(max_r):
            score += float(level.strength_score) / (1.0 + distance_r)
    return float(score)



def _future_labels(
    frame_1m: pd.DataFrame,
    *,
    seed_open: int,
    seed_close: int,
    anomaly_low: float,
    anomaly_close: float,
    config: LargeRunnerDiscoveryConfig,
) -> dict[str, object]:
    horizon_end = int(seed_close) + int(config.horizon_minutes) * MINUTE_MS
    path = _window(frame_1m, int(seed_close), horizon_end)
    expected_count = int(config.horizon_minutes)
    base = {
        "future_label_model": "anomaly_seed_close_target_before_seed_low_break",
        "future_label_anchor": "seed_close",
        "future_label_available_at_entry": False,
        "future60_expected_1m_candles": expected_count,
        "future60_valid_count": int(len(path)),
        "anomaly_low": float(anomaly_low),
        "anomaly_close": float(anomaly_close),
    }
    if path.empty or not np.isfinite(float(anomaly_low)) or not np.isfinite(float(anomaly_close)) or float(anomaly_close) <= 0.0:
        return {
            **base,
            "future_label_status": "missing_or_invalid_anomaly_path",
            "future60_high_return_pct": float("nan"),
            "future60_close_return_pct": float("nan"),
            "future60_min_path_pct": float("nan"),
            "future60_raw_high_return_from_seed_open_pct": float("nan"),
            "future60_high_before_low_return_pct": float("nan"),
            "future60_low_break_before_high10": False,
            "future60_low_break_timestamp_ms": float("nan"),
            "future60_low_break_offset_min": float("nan"),
            "runner_high10_next60": False,
            "runner_high20_next60": False,
            "runner_high30_next60": False,
            "runner_close10_next60": False,
            "runner_close20_next60": False,
            "fader_high10_next60": False,
        }

    old_path = _window(frame_1m, int(seed_open), int(seed_open) + int(config.horizon_minutes) * MINUTE_MS)
    future_high = float(path["high"].max())
    future_low = float(path["low"].min())
    future_close = float(path.iloc[-1]["close"])
    high_ret = _safe_divide(future_high - float(anomaly_close), float(anomaly_close))
    close_ret = _safe_divide(future_close - float(anomaly_close), float(anomaly_close))
    min_ret = _safe_divide(future_low - float(anomaly_close), float(anomaly_close))
    raw_seed_open_high_ret = float("nan")
    if not old_path.empty:
        first_open = float(old_path.iloc[0]["open"])
        raw_seed_open_high_ret = _safe_divide(float(old_path["high"].max()) - first_open, first_open)

    target10 = float(anomaly_close) * 1.10
    target20 = float(anomaly_close) * 1.20
    target30 = float(anomaly_close) * 1.30
    low_break = _first_threshold_event(path, threshold=float(anomaly_low), column="low", direction="le")
    hit10 = _first_threshold_event(path, threshold=target10, column="high", direction="ge")
    hit20 = _first_threshold_event(path, threshold=target20, column="high", direction="ge")
    hit30 = _first_threshold_event(path, threshold=target30, column="high", direction="ge")

    runner10 = _target_before_low_break(hit10, low_break)
    runner20 = _target_before_low_break(hit20, low_break)
    runner30 = _target_before_low_break(hit30, low_break)
    high_before_low = _high_before_event(path, event_ts=low_break.get("timestamp_ms"))
    high_before_low_ret = _safe_divide(high_before_low - float(anomaly_close), float(anomaly_close))
    low_break_before_high10 = bool(low_break["hit"] and (not hit10["hit"] or int(low_break["timestamp_ms"]) <= int(hit10["timestamp_ms"])))
    labels_complete = int(len(path)) >= expected_count
    return {
        **base,
        "future_label_status": "ok" if labels_complete else "partial_1m_path",
        "future60_high_return_pct": high_ret,
        "future60_close_return_pct": close_ret,
        "future60_min_path_pct": min_ret,
        "future60_raw_high_return_from_seed_open_pct": raw_seed_open_high_ret,
        "future60_high_before_low_return_pct": high_before_low_ret,
        "future60_low_break_before_high10": low_break_before_high10,
        "future60_low_break_timestamp_ms": low_break.get("timestamp_ms", float("nan")),
        "future60_low_break_utc": _timestamp_to_utc(low_break.get("timestamp_ms")),
        "future60_low_break_offset_min": _event_offset_min(low_break, int(seed_close)),
        "runner_high10_hit_timestamp_ms": hit10.get("timestamp_ms", float("nan")),
        "runner_high10_hit_utc": _timestamp_to_utc(hit10.get("timestamp_ms")),
        "runner_high10_hit_offset_min": _event_offset_min(hit10, int(seed_close)),
        "runner_high20_hit_timestamp_ms": hit20.get("timestamp_ms", float("nan")),
        "runner_high20_hit_utc": _timestamp_to_utc(hit20.get("timestamp_ms")),
        "runner_high20_hit_offset_min": _event_offset_min(hit20, int(seed_close)),
        "runner_high30_hit_timestamp_ms": hit30.get("timestamp_ms", float("nan")),
        "runner_high30_hit_utc": _timestamp_to_utc(hit30.get("timestamp_ms")),
        "runner_high30_hit_offset_min": _event_offset_min(hit30, int(seed_close)),
        "runner_high10_next60": bool(runner10 and labels_complete),
        "runner_high20_next60": bool(runner20 and labels_complete),
        "runner_high30_next60": bool(runner30 and labels_complete),
        "runner_close10_next60": bool(close_ret >= 0.10 and labels_complete and not low_break["hit"]),
        "runner_close20_next60": bool(close_ret >= 0.20 and labels_complete and not low_break["hit"]),
        "fader_high10_next60": bool(labels_complete and not runner10),
    }


def _first_threshold_event(path: pd.DataFrame, *, threshold: float, column: str, direction: str) -> dict[str, object]:
    if path.empty or column not in path.columns or not np.isfinite(float(threshold)):
        return {"hit": False, "timestamp_ms": float("nan")}
    values = pd.to_numeric(path[column], errors="coerce")
    if direction == "ge":
        mask = values.ge(float(threshold))
    elif direction == "le":
        mask = values.le(float(threshold))
    else:
        raise ValueError(f"unsupported threshold direction: {direction}")
    rows = path.loc[mask.fillna(False)]
    if rows.empty:
        return {"hit": False, "timestamp_ms": float("nan")}
    return {"hit": True, "timestamp_ms": int(rows.iloc[0]["timestamp"])}


def _target_before_low_break(target_event: Mapping[str, object], low_break_event: Mapping[str, object]) -> bool:
    if not bool(target_event.get("hit")):
        return False
    if not bool(low_break_event.get("hit")):
        return True
    # Same 1m candle is ambiguous with OHLCV, so low-break wins the tie.
    return int(target_event["timestamp_ms"]) < int(low_break_event["timestamp_ms"])


def _high_before_event(path: pd.DataFrame, *, event_ts: object) -> float:
    if path.empty or "high" not in path.columns:
        return float("nan")
    try:
        parsed_event_ts = int(float(event_ts))
    except (TypeError, ValueError):
        parsed_event_ts = 0
    if parsed_event_ts > 0:
        window = path.loc[pd.to_numeric(path["timestamp"], errors="coerce") < parsed_event_ts]
    else:
        window = path
    if window.empty:
        return float("nan")
    return float(pd.to_numeric(window["high"], errors="coerce").max())


def _event_offset_min(event: Mapping[str, object], anchor_ms: int) -> float:
    if not bool(event.get("hit")):
        return float("nan")
    return _safe_divide(float(event["timestamp_ms"]) - float(anchor_ms), float(MINUTE_MS))


def _minute_window_features(
    frame: pd.DataFrame,
    *,
    start_ms: int,
    end_ms: int,
    expected_candles: int,
    baseline_quote_5m: float,
    baseline_trades_5m: float,
    prefix: str,
) -> dict[str, object]:
    window = _window(frame, start_ms, end_ms)
    expected = max(1, int(expected_candles))
    if window.empty:
        return {
            f"{prefix}_valid_count": 0,
            f"{prefix}_status": "missing",
        }
    quote = _series(window, "quote_volume")
    trades = _series(window, "number_of_trades")
    closes = _series(window, "close")
    opens = _series(window, "open")
    highs = _series(window, "high")
    lows = _series(window, "low")
    quote_sum = float(quote.sum())
    trades_sum = float(trades.sum())
    taker = _series(window, "taker_buy_quote_volume")
    quote_per_minute = _safe_divide(float(baseline_quote_5m), 5.0)
    trades_per_minute = _safe_divide(float(baseline_trades_5m), 5.0)
    first_open = float(opens.iloc[0])
    last_close = float(closes.iloc[-1])
    first2_quote = float(quote.head(2).sum())
    last2_quote = float(quote.tail(2).sum())
    first2_trades = float(trades.head(2).sum())
    last2_trades = float(trades.tail(2).sum())
    step_returns = (closes - opens) / opens.replace(0.0, np.nan)
    return {
        f"{prefix}_valid_count": int(len(window)),
        f"{prefix}_status": "ok" if len(window) >= expected else "incomplete",
        f"{prefix}_quote_sum": quote_sum,
        f"{prefix}_trades_sum": trades_sum,
        f"{prefix}_taker_buy_quote_share": _safe_divide(float(taker.sum()), quote_sum),
        f"{prefix}_quote_top1_share": _safe_divide(float(quote.max()), quote_sum),
        f"{prefix}_trade_top1_share": _safe_divide(float(trades.max()), trades_sum),
        f"{prefix}_green_share": float((closes > opens).mean()),
        f"{prefix}_positive_return_count": int((step_returns > 0.0).sum()),
        f"{prefix}_elevated_quote_count_3x": int((quote >= quote_per_minute * 3.0).sum()) if np.isfinite(quote_per_minute) else 0,
        f"{prefix}_elevated_trade_count_3x": int((trades >= trades_per_minute * 3.0).sum()) if np.isfinite(trades_per_minute) else 0,
        f"{prefix}_elevated_both_count_3x": int(((quote >= quote_per_minute * 3.0) & (trades >= trades_per_minute * 3.0)).sum()) if np.isfinite(quote_per_minute) and np.isfinite(trades_per_minute) else 0,
        f"{prefix}_last2_quote_share": _safe_divide(last2_quote, quote_sum),
        f"{prefix}_last2_trade_share": _safe_divide(last2_trades, trades_sum),
        f"{prefix}_quote_accel_last2_vs_first2": _safe_divide(last2_quote, first2_quote),
        f"{prefix}_trade_accel_last2_vs_first2": _safe_divide(last2_trades, first2_trades),
        f"{prefix}_return_sum": _safe_divide(last_close - first_open, first_open),
        f"{prefix}_high_return": _safe_divide(float(highs.max()) - first_open, first_open),
        f"{prefix}_min_path_return": _safe_divide(float(lows.min()) - first_open, first_open),
        f"{prefix}_late_return_last2": _safe_divide(float(closes.tail(1).iloc[0]) - float(opens.tail(min(2, len(opens))).iloc[0]), float(opens.tail(min(2, len(opens))).iloc[0])),
        f"{prefix}_first_minute_return": float(step_returns.iloc[0]) if len(step_returns) else float("nan"),
        f"{prefix}_last_minute_return": float(step_returns.iloc[-1]) if len(step_returns) else float("nan"),
    }


def _confirm_features(frame: pd.DataFrame, *, seed_open: int, minutes: int, prefix: str) -> dict[str, object]:
    end_ms = int(seed_open) + int(minutes) * MINUTE_MS
    window = _window(frame, int(seed_open), end_ms)
    expected = int(minutes)
    if len(window) < expected:
        return {
            f"{prefix}_valid": False,
            f"{prefix}_valid_count": int(len(window)),
            f"close_ret_{minutes}m": float("nan"),
            f"high_ret_{minutes}m": float("nan"),
            f"wick_ret_{minutes}m": float("nan"),
        }
    first_open = float(window.iloc[0]["open"])
    close_ret = _safe_divide(float(window.iloc[-1]["close"]) - first_open, first_open)
    high_ret = _safe_divide(float(window["high"].max()) - first_open, first_open)
    return {
        f"{prefix}_valid": True,
        f"{prefix}_valid_count": int(len(window)),
        f"close_ret_{minutes}m": close_ret,
        f"high_ret_{minutes}m": high_ret,
        f"wick_ret_{minutes}m": high_ret - close_ret,
    }


def _pre60_features(frame: pd.DataFrame, *, history_end: int) -> dict[str, object]:
    start = max(0, int(history_end) - 12)
    pre = frame.iloc[start:int(history_end)]
    if pre.empty:
        return {
            "pre60_return_pct": float("nan"),
            "pre60_min_path_pct": float("nan"),
            "pre60_range_pct": float("nan"),
            "pre60_quote_sum": float("nan"),
            "pre60_trades_sum": float("nan"),
        }
    first_open = float(pre.iloc[0]["open"])
    return {
        "pre60_return_pct": _safe_divide(float(pre.iloc[-1]["close"]) - first_open, first_open),
        "pre60_min_path_pct": _safe_divide(float(pre["low"].min()) - first_open, first_open),
        "pre60_range_pct": _safe_divide(float(pre["high"].max()) - float(pre["low"].min()), first_open),
        "pre60_quote_sum": float(pre["quote_volume"].sum()),
        "pre60_trades_sum": float(pre["number_of_trades"].sum()),
    }


def _oi_features(frame: pd.DataFrame, *, index: int) -> dict[str, object]:
    if "open_interest" not in frame.columns:
        return {
            "oi_status": "missing",
            "oi_change_early_pct": float("nan"),
            "oi_change_pre60_pct": float("nan"),
        }
    current = _float(frame.iloc[int(index)].get("open_interest"))
    prev = _float(frame.iloc[int(index) - 1].get("open_interest")) if int(index) > 0 else float("nan")
    before = _float(frame.iloc[max(0, int(index) - 12)].get("open_interest")) if int(index) > 0 else float("nan")
    return {
        "oi_status": "ok" if np.isfinite(current) else "missing",
        "oi_change_early_pct": _safe_divide(current - prev, prev),
        "oi_change_pre60_pct": _safe_divide(prev - before, before),
    }


def _hourly_growth_rows(*, symbol: str, frame_5m: pd.DataFrame, start_ms: int, end_ms: int) -> list[dict[str, object]]:
    if frame_5m.empty:
        return []
    frame = frame_5m.loc[(frame_5m["timestamp"] >= int(start_ms)) & (frame_5m["timestamp"] < int(end_ms))].copy()
    if frame.empty:
        return []
    frame["period_start_ms"] = (pd.to_numeric(frame["timestamp"], errors="coerce") // HOUR_MS * HOUR_MS).astype("int64")
    rows: list[dict[str, object]] = []
    for period_start, group in frame.groupby("period_start_ms"):
        group = group.sort_values("timestamp")
        if len(group) != 12:
            continue
        first_open = float(group.iloc[0]["open"])
        close = float(group.iloc[-1]["close"])
        high_series = pd.to_numeric(group["high"], errors="coerce")
        high_idx = high_series.idxmax()
        high_row = group.loc[high_idx] if high_idx in group.index else group.iloc[0]
        high = float(high_series.max())
        high_ts = int(high_row["timestamp"])
        first5 = _hour_prefix_stats(group, first_open=first_open, period_start_ms=int(period_start), minutes=5)
        first15 = _hour_prefix_stats(group, first_open=first_open, period_start_ms=int(period_start), minutes=15)
        first30 = _hour_prefix_stats(group, first_open=first_open, period_start_ms=int(period_start), minutes=30)
        rows.append(
            {
                "symbol": symbol,
                "period_start_ms": int(period_start),
                "period_start_utc": _timestamp_to_utc(int(period_start)),
                "period_end_ms": int(period_start) + HOUR_MS,
                "period_end_utc": _timestamp_to_utc(int(period_start) + HOUR_MS),
                "hour_open": first_open,
                "hour_high": high,
                "hour_close": close,
                "hour_high_return_pct": _safe_divide(high - first_open, first_open),
                "hour_close_return_pct": _safe_divide(close - first_open, first_open),
                "hour_high_candle_open_ms": high_ts,
                "hour_high_candle_open_utc": _timestamp_to_utc(high_ts),
                "hour_high_candle_open_offset_min": _safe_divide(float(high_ts - int(period_start)), float(MINUTE_MS)),
                "quote_volume": float(group["quote_volume"].sum()) if "quote_volume" in group.columns else float("nan"),
                "number_of_trades": float(group["number_of_trades"].sum()) if "number_of_trades" in group.columns else float("nan"),
                **first5,
                **first15,
                **first30,
            }
        )
    return rows


def _hour_prefix_stats(
    group: pd.DataFrame,
    *,
    first_open: float,
    period_start_ms: int,
    minutes: int,
) -> dict[str, object]:
    prefix = f"first{int(minutes)}"
    if group.empty:
        return {
            f"{prefix}_high_return_pct": float("nan"),
            f"{prefix}_close_return_pct": float("nan"),
            f"{prefix}_quote_volume": float("nan"),
            f"{prefix}_number_of_trades": float("nan"),
        }
    window = group.loc[pd.to_numeric(group["timestamp"], errors="coerce") < int(period_start_ms) + int(minutes) * MINUTE_MS]
    if window.empty:
        return {
            f"{prefix}_high_return_pct": float("nan"),
            f"{prefix}_close_return_pct": float("nan"),
            f"{prefix}_quote_volume": float("nan"),
            f"{prefix}_number_of_trades": float("nan"),
        }
    high = float(pd.to_numeric(window["high"], errors="coerce").max())
    close = float(window.iloc[-1]["close"])
    return {
        f"{prefix}_high_return_pct": _safe_divide(high - float(first_open), float(first_open)),
        f"{prefix}_close_return_pct": _safe_divide(close - float(first_open), float(first_open)),
        f"{prefix}_quote_volume": float(window["quote_volume"].sum()) if "quote_volume" in window.columns else float("nan"),
        f"{prefix}_number_of_trades": float(window["number_of_trades"].sum()) if "number_of_trades" in window.columns else float("nan"),
    }


def _top_growth_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    result = frame.loc[
        pd.to_numeric(frame["hour_high_return_pct"], errors="coerce").ge(0.10)
        | pd.to_numeric(frame["hour_close_return_pct"], errors="coerce").ge(0.10)
    ].copy()
    return result.sort_values(["hour_high_return_pct", "hour_close_return_pct"], ascending=[False, False]).reset_index(drop=True)


def _top_growth_coverage(top_growth: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    if top_growth.empty:
        return pd.DataFrame(columns=["symbol", "period_start_ms", "covered_by_any_arm"])
    if matches.empty:
        result = top_growth.copy()
        result["covered_by_any_arm"] = False
        result["covered_arm_ids"] = ""
        return result
    rows: list[dict[str, object]] = []
    for _, top in top_growth.iterrows():
        symbol = str(top["symbol"])
        period_start = int(top["period_start_ms"])
        period_end = period_start + 15 * MINUTE_MS
        mask = (
            matches["symbol"].astype(str).eq(symbol)
            & pd.to_numeric(matches["seed_open_ms"], errors="coerce").between(period_start, period_end, inclusive="left")
        )
        covered = matches.loc[mask]
        rows.append(
            {
                **top.to_dict(),
                "covered_by_any_arm": bool(not covered.empty),
                "covered_arm_ids": "|".join(sorted(set(covered["arm_id"].astype(str)))) if not covered.empty else "",
                "covering_matches": int(len(covered)),
            }
        )
    return pd.DataFrame(rows)


def _apply_research_portfolio(trades: pd.DataFrame, *, config: LargeRunnerDiscoveryConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty or "status" not in trades.columns:
        return trades.copy(), pd.DataFrame()
    frame = trades.loc[trades["status"].astype(str).eq("closed")].copy()
    if frame.empty:
        return frame, pd.DataFrame()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce")
    frame["exit_timestamp_ms"] = pd.to_numeric(frame["exit_timestamp_ms"], errors="coerce")
    frame = frame.sort_values(["entry_timestamp_ms", "arm_priority", "symbol", "exit_policy_id"]).reset_index(drop=True)
    active: list[tuple[str, int]] = []
    cooldown_until_by_symbol: dict[str, int] = {}
    selected: list[int] = []
    events: list[dict[str, object]] = []
    for idx, row in frame.iterrows():
        entry_ts = int(row["entry_timestamp_ms"])
        exit_ts = int(row["exit_timestamp_ms"])
        symbol = str(row["symbol"])
        active = [(s, e) for s, e in active if e > entry_ts]
        base = {
            "event_timestamp_ms": entry_ts,
            "event_timestamp_utc": _timestamp_to_utc(entry_ts),
            "symbol": symbol,
            "arm_id": row.get("arm_id", ""),
            "exit_policy_id": row.get("exit_policy_id", ""),
            "open_positions_before": int(len(active)),
        }
        if any(s == symbol for s, _ in active):
            events.append({**base, "event_type": "blocked_same_symbol_open"})
            continue
        if cooldown_until_by_symbol.get(symbol, 0) > entry_ts:
            events.append({**base, "event_type": "blocked_symbol_cooldown"})
            continue
        if len(active) >= 4:
            events.append({**base, "event_type": "blocked_position_cap"})
            continue
        selected.append(int(idx))
        active.append((symbol, exit_ts))
        cooldown_until_by_symbol[symbol] = exit_ts + FIVE_MINUTE_MS
        events.append({**base, "event_type": "selected", "open_positions_after": int(len(active))})
    portfolio = frame.loc[selected].copy() if selected else frame.iloc[0:0].copy()
    portfolio["portfolio_model"] = "priority_arms_max4_same_symbol_cooldown_5m_research"
    del config
    return portfolio.reset_index(drop=True), pd.DataFrame(events)


def _nature_summary(trade_grid: pd.DataFrame, portfolio: pd.DataFrame, *, config: LargeRunnerDiscoveryConfig) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for scope, frame in (("trade_grid", trade_grid), ("portfolio", portfolio)):
        rows.extend(
            _nature_category_summary_rows(
                frame,
                scope=scope,
                category_type="trade_rule",
                source_column="large_runner_nature_trade_rule_ids",
                config=config,
            )
        )
        rows.extend(
            _nature_category_summary_rows(
                frame,
                scope=scope,
                category_type="nature",
                source_column="large_runner_nature_ids",
                config=config,
            )
        )
        rows.extend(
            _nature_category_summary_rows(
                frame,
                scope=scope,
                category_type="booster",
                source_column="large_runner_nature_booster_ids",
                config=config,
            )
        )
    return pd.DataFrame(rows)


def _nature_by_period(portfolio: pd.DataFrame, *, period: str, config: LargeRunnerDiscoveryConfig) -> pd.DataFrame:
    if portfolio.empty or "entry_timestamp_ms" not in portfolio.columns:
        return pd.DataFrame()
    frame = _explode_multi_value(portfolio, "large_runner_nature_trade_rule_ids", "category_id")
    if frame.empty:
        return pd.DataFrame()
    timestamps = pd.to_datetime(pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce"), unit="ms", utc=True)
    if period == "week":
        iso = timestamps.dt.isocalendar()
        frame["period_id"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    else:
        frame["period_id"] = timestamps.dt.date.astype(str)
    rows: list[dict[str, object]] = []
    group_columns = ["period_id", "category_id", "exit_policy_id"]
    for key, group in frame.groupby(group_columns, dropna=False):
        period_id, category_id, exit_policy_id = key
        rows.append(
            {
                "period_type": period,
                "period_id": period_id,
                "category_type": "trade_rule",
                "category_id": category_id,
                "exit_policy_id": exit_policy_id,
                **_summary_metrics_with_days(group, pd.DataFrame(), days=config.days),
            }
        )
    return pd.DataFrame(rows)


def _nature_by_symbol(portfolio: pd.DataFrame, *, config: LargeRunnerDiscoveryConfig) -> pd.DataFrame:
    if portfolio.empty or "symbol" not in portfolio.columns:
        return pd.DataFrame()
    frame = _explode_multi_value(portfolio, "large_runner_nature_trade_rule_ids", "category_id")
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for key, group in frame.groupby(["symbol", "category_id", "exit_policy_id"], dropna=False):
        symbol, category_id, exit_policy_id = key
        rows.append(
            {
                "symbol": symbol,
                "category_type": "trade_rule",
                "category_id": category_id,
                "exit_policy_id": exit_policy_id,
                **_summary_metrics_with_days(group, pd.DataFrame(), days=config.days),
            }
        )
    return pd.DataFrame(rows)


def _nature_top_dependency(portfolio: pd.DataFrame) -> pd.DataFrame:
    if portfolio.empty:
        return pd.DataFrame()
    frame = _explode_multi_value(portfolio, "large_runner_nature_trade_rule_ids", "category_id")
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for key, group in frame.groupby(["category_id", "exit_policy_id"], dropna=False):
        category_id, exit_policy_id = key
        dep = _top_dependency(group)
        for _, row in dep.iterrows():
            rows.append(
                {
                    "category_type": "trade_rule",
                    "category_id": category_id,
                    "exit_policy_id": exit_policy_id,
                    **row.to_dict(),
                }
            )
    return pd.DataFrame(rows)


def _nature_sensitivity(portfolio: pd.DataFrame, *, config: LargeRunnerDiscoveryConfig) -> pd.DataFrame:
    if portfolio.empty or "status" not in portfolio.columns:
        return pd.DataFrame()
    frame = portfolio.loc[portfolio["status"].astype(str).eq("closed")].copy()
    if frame.empty:
        return pd.DataFrame()
    frame = frame.loc[
        pd.to_numeric(frame.get("decision_offset_minutes", pd.Series(index=frame.index)), errors="coerce").eq(5)
        & frame.get("arm_id", pd.Series(index=frame.index)).astype(str).isin(TRADE_ELIGIBLE_E5_ARMS)
    ].copy()
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    selected_policy = "tp075r_close25_be1r_kill10"
    if "exit_policy_id" in frame.columns:
        policy_frame = frame.loc[frame["exit_policy_id"].astype(str).eq(selected_policy)].copy()
    else:
        policy_frame = frame.iloc[0:0].copy()
    if policy_frame.empty:
        policy_frame = frame.copy()
    variants: list[tuple[str, str, pd.Series]] = []
    for cap in (0.04, 0.06, 0.08, 0.10, 0.12):
        variants.append(
            (
                "pre60_return_cap",
                f"{cap:.2f}",
                policy_frame.apply(lambda row, cap=cap: v4_quality_mask(row.to_dict(), pre60_return_cap=cap), axis=1),
            )
        )
    for cap in (0.35, 0.36, 0.41, 0.45, 0.55):
        variants.append(
            (
                "m1_quote_top1_cap_liquid_seed",
                f"{cap:.2f}",
                _v4_quality_variant_mask(policy_frame, m1_quote_top1_cap=cap),
            )
        )
    for cap in (0.55, 0.58, 0.62):
        variants.append(
            (
                "early_taker_veto_cap",
                f"{cap:.2f}",
                _v4_quality_variant_mask(policy_frame, early_taker_veto_cap=cap),
            )
        )
    for cap in (0.075, 0.08, 0.09):
        variants.append(
            (
                "early_return_cap_liquid_seed",
                f"{cap:.3f}",
                _v4_quality_variant_mask(policy_frame, early_return_cap=cap),
            )
        )
    for variant_name, variant_value, mask in variants:
        selected = policy_frame.loc[mask.fillna(False)].copy()
        rows.append(
            {
                "category_type": "trade_rule",
                "category_id": "v4_quality_cool",
                "exit_policy_id": selected_policy if not policy_frame.empty else "",
                "variant_name": variant_name,
                "variant_value": variant_value,
                **_summary_metrics_with_days(selected, pd.DataFrame(), days=config.days),
            }
        )
    return pd.DataFrame(rows)


def _prefilter_missed_top_growth(
    top_growth: pd.DataFrame,
    raw: pd.DataFrame,
    setups: pd.DataFrame,
    matches: pd.DataFrame,
    trades: pd.DataFrame,
    portfolio: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "symbol",
        "period_start_ms",
        "period_start_utc",
        "hour_high_return_pct",
        "hour_close_return_pct",
        "audit_window_model",
        "raw_candidates_in_window",
        "setups_in_window",
        "prefilter_passed_setups",
        "enriched_setups",
        "arm_matches",
        "nature_selected_matches",
        "closed_trade_rows",
        "portfolio_rows",
        "missed_stage",
    ]
    if top_growth.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    raw = raw.copy()
    setups = setups.copy()
    matches = matches.copy()
    trades = trades.copy()
    portfolio = portfolio.copy()
    for _, top in top_growth.iterrows():
        symbol = str(top.get("symbol", ""))
        period_start = int(top.get("period_start_ms", 0))
        period_end = period_start + 15 * MINUTE_MS
        raw_window = _rows_for_symbol_time(raw, symbol=symbol, time_column="seed_open_ms", start_ms=period_start, end_ms=period_end)
        if raw_window.empty:
            raw_window = _rows_for_symbol_time(raw, symbol=symbol, time_column="timestamp_ms", start_ms=period_start, end_ms=period_end)
        setup_window = _rows_for_symbol_time(setups, symbol=symbol, time_column="seed_open_ms", start_ms=period_start, end_ms=period_end)
        match_window = _rows_for_symbol_time(matches, symbol=symbol, time_column="seed_open_ms", start_ms=period_start, end_ms=period_end)
        trade_window = _rows_for_symbol_time(trades, symbol=symbol, time_column="seed_open_ms", start_ms=period_start, end_ms=period_end)
        portfolio_window = _rows_for_symbol_time(portfolio, symbol=symbol, time_column="seed_open_ms", start_ms=period_start, end_ms=period_end)
        prefilter_passed = _bool_series(setup_window, "large_runner_5m_prefilter_passed").sum() if not setup_window.empty else 0
        enriched = (
            _string_equals_series(setup_window, "enrichment_status", "1m_enriched_after_5m_prefilter").sum()
            if not setup_window.empty
            else 0
        )
        nature_selected = (
            _bool_series(match_window, "large_runner_nature_selected").sum() if not match_window.empty else 0
        )
        closed_trades = (
            _string_equals_series(trade_window, "status", "closed").sum() if not trade_window.empty else 0
        )
        portfolio_rows = int(len(portfolio_window))
        if portfolio_rows:
            missed_stage = "covered_by_portfolio"
        elif int(closed_trades):
            missed_stage = "simulated_trade_not_portfolio_selected"
        elif int(nature_selected):
            missed_stage = "nature_selected_but_no_closed_trade"
        elif not match_window.empty:
            missed_stage = "arm_matched_but_not_selected_nature"
        elif int(enriched):
            missed_stage = "enriched_but_no_arm_match"
        elif int(prefilter_passed):
            missed_stage = "prefilter_passed_but_not_enriched_or_missing_1m"
        elif not setup_window.empty:
            missed_stage = "5m_prefilter_failed"
        elif not raw_window.empty:
            missed_stage = "raw_candidate_not_first_cluster_setup"
        else:
            missed_stage = "broad_5m_gate_not_seen"
        rows.append(
            {
                "symbol": symbol,
                "period_start_ms": period_start,
                "period_start_utc": top.get("period_start_utc", ""),
                "hour_high_return_pct": top.get("hour_high_return_pct", float("nan")),
                "hour_close_return_pct": top.get("hour_close_return_pct", float("nan")),
                "audit_window_model": "first_15m_of_top_hour",
                "raw_candidates_in_window": int(len(raw_window)),
                "setups_in_window": int(len(setup_window)),
                "prefilter_passed_setups": int(prefilter_passed),
                "enriched_setups": int(enriched),
                "arm_matches": int(len(match_window)),
                "nature_selected_matches": int(nature_selected),
                "closed_trade_rows": int(closed_trades),
                "portfolio_rows": int(portfolio_rows),
                "missed_stage": missed_stage,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _top_growth_timing_audit(
    top_growth: pd.DataFrame,
    raw: pd.DataFrame,
    setups: pd.DataFrame,
    matches: pd.DataFrame,
    trades: pd.DataFrame,
    portfolio: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "symbol",
        "period_start_ms",
        "period_start_utc",
        "hour_high_return_pct",
        "hour_close_return_pct",
        "hour_high_candle_open_offset_min",
        "first5_high_return_pct",
        "first15_high_return_pct",
        "first30_high_return_pct",
        "audit_window_model",
        "window_start_offset_min",
        "window_end_offset_min",
        "window_start_ms",
        "window_start_utc",
        "window_end_ms",
        "window_end_utc",
        "raw_candidates_in_window",
        "raw_first_offset_min",
        "setups_in_window",
        "setup_first_offset_min",
        "prefilter_passed_setups",
        "prefilter_passed_first_offset_min",
        "enriched_setups",
        "enriched_first_offset_min",
        "arm_matches",
        "arm_match_first_offset_min",
        "nature_selected_matches",
        "nature_selected_first_offset_min",
        "closed_trade_rows",
        "closed_trade_first_offset_min",
        "portfolio_rows",
        "portfolio_first_offset_min",
        "missed_stage",
    ]
    if top_growth.empty:
        return pd.DataFrame(columns=columns)

    raw_index = _stage_time_index(raw, time_column="seed_open_ms", fallback_time_column="timestamp_ms")
    setup_index = _stage_time_index(setups, time_column="seed_open_ms")
    prefilter_index = _stage_time_index(
        setups.loc[_bool_series(setups, "large_runner_5m_prefilter_passed")] if not setups.empty else pd.DataFrame(),
        time_column="seed_open_ms",
    )
    enriched_index = _stage_time_index(
        setups.loc[_string_equals_series(setups, "enrichment_status", "1m_enriched_after_5m_prefilter")] if not setups.empty else pd.DataFrame(),
        time_column="seed_open_ms",
    )
    match_index = _stage_time_index(matches, time_column="seed_open_ms")
    nature_index = _stage_time_index(
        matches.loc[_bool_series(matches, "large_runner_nature_selected")] if not matches.empty else pd.DataFrame(),
        time_column="seed_open_ms",
    )
    closed_trade_index = _stage_time_index(
        trades.loc[_string_equals_series(trades, "status", "closed")] if not trades.empty else pd.DataFrame(),
        time_column="seed_open_ms",
    )
    portfolio_index = _stage_time_index(portfolio, time_column="seed_open_ms")
    windows = (
        ("first_15m_of_top_hour", 0, 15 * MINUTE_MS),
        ("full_top_hour", 0, HOUR_MS),
        ("pre60_to_hour_end", -HOUR_MS, HOUR_MS),
    )
    rows: list[dict[str, object]] = []
    for _, top in top_growth.iterrows():
        symbol = str(top.get("symbol", ""))
        period_start = int(top.get("period_start_ms", 0))
        for window_name, start_offset, end_offset in windows:
            window_start = period_start + int(start_offset)
            window_end = period_start + int(end_offset)
            raw_count, raw_first = _stage_count_first_offset(raw_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            setup_count, setup_first = _stage_count_first_offset(setup_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            prefilter_count, prefilter_first = _stage_count_first_offset(prefilter_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            enriched_count, enriched_first = _stage_count_first_offset(enriched_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            match_count, match_first = _stage_count_first_offset(match_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            nature_count, nature_first = _stage_count_first_offset(nature_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            closed_count, closed_first = _stage_count_first_offset(closed_trade_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            portfolio_count, portfolio_first = _stage_count_first_offset(portfolio_index, symbol=symbol, start_ms=window_start, end_ms=window_end, period_start_ms=period_start)
            missed_stage = _missed_stage_from_counts(
                raw_count=raw_count,
                setup_count=setup_count,
                prefilter_passed_count=prefilter_count,
                enriched_count=enriched_count,
                match_count=match_count,
                nature_selected_count=nature_count,
                closed_trade_count=closed_count,
                portfolio_count=portfolio_count,
            )
            rows.append(
                {
                    "symbol": symbol,
                    "period_start_ms": period_start,
                    "period_start_utc": top.get("period_start_utc", ""),
                    "hour_high_return_pct": top.get("hour_high_return_pct", float("nan")),
                    "hour_close_return_pct": top.get("hour_close_return_pct", float("nan")),
                    "hour_high_candle_open_offset_min": top.get("hour_high_candle_open_offset_min", float("nan")),
                    "first5_high_return_pct": top.get("first5_high_return_pct", float("nan")),
                    "first15_high_return_pct": top.get("first15_high_return_pct", float("nan")),
                    "first30_high_return_pct": top.get("first30_high_return_pct", float("nan")),
                    "audit_window_model": window_name,
                    "window_start_offset_min": _safe_divide(float(start_offset), float(MINUTE_MS)),
                    "window_end_offset_min": _safe_divide(float(end_offset), float(MINUTE_MS)),
                    "window_start_ms": int(window_start),
                    "window_start_utc": _timestamp_to_utc(window_start),
                    "window_end_ms": int(window_end),
                    "window_end_utc": _timestamp_to_utc(window_end),
                    "raw_candidates_in_window": raw_count,
                    "raw_first_offset_min": raw_first,
                    "setups_in_window": setup_count,
                    "setup_first_offset_min": setup_first,
                    "prefilter_passed_setups": prefilter_count,
                    "prefilter_passed_first_offset_min": prefilter_first,
                    "enriched_setups": enriched_count,
                    "enriched_first_offset_min": enriched_first,
                    "arm_matches": match_count,
                    "arm_match_first_offset_min": match_first,
                    "nature_selected_matches": nature_count,
                    "nature_selected_first_offset_min": nature_first,
                    "closed_trade_rows": closed_count,
                    "closed_trade_first_offset_min": closed_first,
                    "portfolio_rows": portfolio_count,
                    "portfolio_first_offset_min": portfolio_first,
                    "missed_stage": missed_stage,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _stage_time_index(
    frame: pd.DataFrame,
    *,
    time_column: str,
    fallback_time_column: str | None = None,
) -> dict[str, np.ndarray]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    selected_time_column = time_column if time_column in frame.columns else fallback_time_column
    if not selected_time_column or selected_time_column not in frame.columns:
        return {}
    work = pd.DataFrame(
        {
            "symbol": frame["symbol"].astype(str),
            "timestamp_ms": pd.to_numeric(frame[selected_time_column], errors="coerce"),
        }
    ).dropna()
    if work.empty:
        return {}
    result: dict[str, np.ndarray] = {}
    for symbol, group in work.groupby("symbol", sort=False):
        result[str(symbol)] = np.sort(group["timestamp_ms"].astype("int64").to_numpy())
    return result


def _stage_count_first_offset(
    index: Mapping[str, np.ndarray],
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    period_start_ms: int,
) -> tuple[int, float]:
    values = index.get(str(symbol))
    if values is None or len(values) == 0:
        return 0, float("nan")
    left = int(np.searchsorted(values, int(start_ms), side="left"))
    right = int(np.searchsorted(values, int(end_ms), side="left"))
    count = max(0, right - left)
    if count <= 0:
        return 0, float("nan")
    return count, _safe_divide(float(values[left]) - float(period_start_ms), float(MINUTE_MS))


def _missed_stage_from_counts(
    *,
    raw_count: int,
    setup_count: int,
    prefilter_passed_count: int,
    enriched_count: int,
    match_count: int,
    nature_selected_count: int,
    closed_trade_count: int,
    portfolio_count: int,
) -> str:
    if int(portfolio_count):
        return "covered_by_portfolio"
    if int(closed_trade_count):
        return "simulated_trade_not_portfolio_selected"
    if int(nature_selected_count):
        return "nature_selected_but_no_closed_trade"
    if int(match_count):
        return "arm_matched_but_not_selected_nature"
    if int(enriched_count):
        return "enriched_but_no_arm_match"
    if int(prefilter_passed_count):
        return "prefilter_passed_but_not_enriched_or_missing_1m"
    if int(setup_count):
        return "5m_prefilter_failed"
    if int(raw_count):
        return "raw_candidate_not_first_cluster_setup"
    return "broad_5m_gate_not_seen"


def _nature_category_summary_rows(
    frame: pd.DataFrame,
    *,
    scope: str,
    category_type: str,
    source_column: str,
    config: LargeRunnerDiscoveryConfig,
) -> list[dict[str, object]]:
    exploded = _explode_multi_value(frame, source_column, "category_id")
    if exploded.empty:
        return []
    rows: list[dict[str, object]] = []
    for key, group in exploded.groupby(["category_id", "exit_policy_id"], dropna=False):
        category_id, exit_policy_id = key
        closed = group.loc[group["status"].astype(str).eq("closed")].copy() if "status" in group.columns else group.copy()
        skipped = group.loc[group["status"].astype(str).eq("skipped")].copy() if "status" in group.columns else pd.DataFrame()
        rows.append(
            {
                "scope": scope,
                "category_type": category_type,
                "category_id": category_id,
                "exit_policy_id": exit_policy_id,
                **_summary_metrics_with_days(closed, skipped, days=config.days),
            }
        )
    return rows


def _summary_metrics_with_days(closed: pd.DataFrame, skipped: pd.DataFrame, *, days: int) -> dict[str, object]:
    metrics = _summary_metrics(closed, skipped)
    closed_count = int(metrics.get("closed_trades", 0) or 0)
    metrics["trades_per_day"] = _safe_divide(float(closed_count), float(days))
    if not closed.empty and "entry_timestamp_ms" in closed.columns:
        day = pd.to_datetime(pd.to_numeric(closed["entry_timestamp_ms"], errors="coerce"), unit="ms", utc=True).dt.date
        net = pd.to_numeric(closed.get("net_return", pd.Series(index=closed.index)), errors="coerce")
        day_sum = net.groupby(day).sum()
        metrics["active_days"] = int(day_sum.size)
        metrics["positive_active_days"] = int(day_sum.gt(0.0).sum())
        metrics["positive_active_day_rate"] = float(day_sum.gt(0.0).mean()) if day_sum.size else float("nan")
        metrics["worst_day_net_return"] = float(day_sum.min()) if day_sum.size else float("nan")
        metrics["median_day_net_return"] = float(day_sum.median()) if day_sum.size else float("nan")
    else:
        metrics["active_days"] = 0
        metrics["positive_active_days"] = 0
        metrics["positive_active_day_rate"] = float("nan")
        metrics["worst_day_net_return"] = float("nan")
        metrics["median_day_net_return"] = float("nan")
    return metrics


def _explode_multi_value(frame: pd.DataFrame, source_column: str, target_column: str) -> pd.DataFrame:
    if frame.empty or source_column not in frame.columns:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        raw_value = row.get(source_column, "")
        values = [value for value in str(raw_value).split("|") if value and value.lower() != "nan"]
        for value in values:
            item = row.to_dict()
            item[target_column] = value
            rows.append(item)
    return pd.DataFrame(rows)


def _v4_quality_variant_mask(
    frame: pd.DataFrame,
    *,
    m1_quote_top1_cap: float = 0.41,
    early_taker_veto_cap: float = 0.62,
    early_return_cap: float = 0.075,
) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    early_return = pd.to_numeric(frame.get("early_return_pct", pd.Series(index=frame.index)), errors="coerce")
    pre60_range = pd.to_numeric(frame.get("pre60_range_pct", pd.Series(index=frame.index)), errors="coerce")
    pre60_trades = pd.to_numeric(frame.get("pre60_trades_sum", pd.Series(index=frame.index)), errors="coerce")
    pre60_return = pd.to_numeric(frame.get("pre60_return_pct", pd.Series(index=frame.index)), errors="coerce")
    m1_min_path = pd.to_numeric(frame.get("m1_min_path_return", pd.Series(index=frame.index)), errors="coerce")
    m1_quote_top1 = pd.to_numeric(frame.get("m1_quote_top1_share", pd.Series(index=frame.index)), errors="coerce")
    m1_trade_top1 = pd.to_numeric(frame.get("m1_trade_top1_share", pd.Series(index=frame.index)), errors="coerce")
    early_taker = pd.to_numeric(frame.get("early_taker_buy_quote_share", pd.Series(index=frame.index)), errors="coerce")
    oi_early = pd.to_numeric(frame.get("oi_change_early_pct", pd.Series(index=frame.index)), errors="coerce")
    stress = pre60_range.ge(0.030) & pre60_trades.ge(12_000) & m1_min_path.le(-0.004)
    liquid = (
        early_return.le(float(early_return_cap))
        & pre60_trades.ge(40_000)
        & m1_quote_top1.le(float(m1_quote_top1_cap))
        & (oi_early.ge(0.0) | oi_early.isna())
    )
    veto = (
        pre60_return.gt(0.06)
        | early_return.gt(0.09)
        | m1_quote_top1.gt(0.55)
        | m1_trade_top1.gt(0.55)
        | early_taker.gt(float(early_taker_veto_cap))
    )
    return (stress | liquid) & ~veto


def _rows_for_symbol_time(
    frame: pd.DataFrame,
    *,
    symbol: str,
    time_column: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    if frame.empty or "symbol" not in frame.columns or time_column not in frame.columns:
        return pd.DataFrame()
    timestamps = pd.to_numeric(frame[time_column], errors="coerce")
    return frame.loc[
        frame["symbol"].astype(str).eq(symbol)
        & timestamps.between(int(start_ms), int(end_ms), inclusive="left")
    ].copy()


def _exit_policy_comparison(trades: pd.DataFrame) -> pd.DataFrame:
    return _summary_by(trades, ["exit_policy_id"])


def _summary_by(trades: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if trades.empty:
        base = {column: "" for column in columns}
        return pd.DataFrame([{**base, "closed_trades": 0, "skipped_trades": 0}])
    frame = trades.copy()
    if columns:
        groups = frame.groupby(columns, dropna=False)
    else:
        groups = [((), frame)]
    rows: list[dict[str, object]] = []
    for key, group in groups:
        if not isinstance(key, tuple):
            key = (key,)
        row = {column: value for column, value in zip(columns, key)}
        closed = group.loc[group["status"].astype(str).eq("closed")].copy()
        skipped = group.loc[group["status"].astype(str).eq("skipped")].copy()
        row.update(_summary_metrics(closed, skipped))
        rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    if "sum_net_return" not in result.columns:
        result["sum_net_return"] = float("nan")
    if "closed_trades" not in result.columns:
        result["closed_trades"] = 0
    return result.sort_values(["sum_net_return", "closed_trades"], ascending=[False, False], na_position="last")


def _summary_metrics(closed: pd.DataFrame, skipped: pd.DataFrame) -> dict[str, object]:
    if closed.empty:
        return {
            "closed_trades": 0,
            "skipped_trades": int(len(skipped)),
        }
    net = pd.to_numeric(closed["net_return"], errors="coerce")
    return {
        "closed_trades": int(len(closed)),
        "skipped_trades": int(len(skipped)),
        "symbols": int(closed["symbol"].nunique()),
        "win_rate": float(net.gt(0.0).mean()),
        "avg_net_return": float(net.mean()),
        "median_net_return": float(net.median()),
        "sum_net_return": float(net.sum()),
        "avg_mfe_pct": float(pd.to_numeric(closed["mfe_pct"], errors="coerce").mean()),
        "avg_mae_pct": float(pd.to_numeric(closed["mae_pct"], errors="coerce").mean()),
        "runner_high10_share": float(_bool_series(closed, "runner_high10_next60").mean()),
        "runner_high20_share": float(_bool_series(closed, "runner_high20_next60").mean()),
        "runner_close10_share": float(_bool_series(closed, "runner_close10_next60").mean()),
    }


def _summary_by_day(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "entry_timestamp_ms" not in trades.columns:
        return pd.DataFrame()
    frame = trades.copy()
    frame["entry_day_utc"] = pd.to_datetime(pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce"), unit="ms", utc=True).dt.date.astype(str)
    return _summary_by(frame, ["entry_day_utc", "arm_id", "exit_policy_id"])


def _mfe_mae_frame(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    columns = [
        "symbol",
        "arm_id",
        "exit_policy_id",
        "entry_timestamp_utc",
        "status",
        "net_return",
        "mfe_pct",
        "mae_pct",
        "initial_risk_pct",
        "runner_high10_next60",
        "runner_high20_next60",
        "runner_close10_next60",
        "exit_reason",
    ]
    return trades[[column for column in columns if column in trades.columns]].copy()


def _skip_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=["status", "skip_reason", "count"])
    frame = trades.copy()
    frame["skip_reason"] = frame.get("skip_reason", "").fillna("").astype(str)
    return frame.groupby(["status", "skip_reason"], dropna=False).size().reset_index(name="count").sort_values("count", ascending=False)


def _top_dependency(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "net_return" not in trades.columns:
        return pd.DataFrame()
    closed = trades.loc[trades["status"].astype(str).eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    closed["net_return"] = pd.to_numeric(closed["net_return"], errors="coerce")
    total = float(closed["net_return"].sum())
    rows: list[dict[str, object]] = []
    for top_n in (1, 3, 5, 10, 20):
        top = closed.sort_values("net_return", ascending=False).head(top_n)
        top_sum = float(top["net_return"].sum())
        rows.append(
            {
                "top_n": top_n,
                "top_sum_net_return": top_sum,
                "all_sum_net_return": total,
                "share_of_total": _safe_divide(top_sum, total),
                "top_symbols": "|".join(top["symbol"].astype(str).head(10)),
            }
        )
    return pd.DataFrame(rows)


def _level_attack_candidates(setups: pd.DataFrame) -> pd.DataFrame:
    if setups.empty:
        return pd.DataFrame()
    if "h1_level_attack_candidate" not in setups.columns:
        return pd.DataFrame()
    frame = setups.copy()
    mask = _bool_series(frame, "h1_level_attack_candidate")
    columns = [
        "symbol",
        "seed_open_ms",
        "seed_close_utc",
        "setup_selection_model",
        "setup_cluster_raw_rank",
        "large_runner_5m_prefilter_passed",
        "h1_level_attack_status",
        "h1_level_attack_reject_reason",
        "h1_entry_mode_vs_nearest_level",
        "h1_level_attack_candidate",
        "h1_is_in_attack_zone_70",
        "h1_progress_to_level_from_pullback",
        "h1_nearest_level_price",
        "h1_nearest_level_distance_R_from_entry",
        "h1_nearest_level_distance_pct_from_seed_close",
        "h1_nearest_level_strength_score",
        "h1_nearest_level_valid_touch_count",
        "h1_pullback_from_level_pct",
        "h1_level_crossed_by_seed_high",
        "h1_level_crossed_by_seed_close",
        "h1_attack_prior_spike_count",
        "h1_attack_quote_vs_prior_spike_max",
        "h1_attack_trades_vs_prior_spike_max",
        "h1_attack_flow_beats_prior_spikes",
        "h1_remaining_overhead_level_count_3R",
        "h1_level_cascade_score_3R",
        "early_return_pct",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "session_primary",
        "hour_utc",
    ]
    return frame.loc[mask, [column for column in columns if column in frame.columns]].copy()


def _level_attack_reject_reasons(setups: pd.DataFrame) -> pd.DataFrame:
    if setups.empty or "h1_level_attack_reject_reason" not in setups.columns:
        return pd.DataFrame(columns=["h1_level_attack_reject_reason", "setups"])
    frame = setups.copy()
    frame["h1_level_attack_reject_reason"] = frame["h1_level_attack_reject_reason"].fillna("missing").astype(str)
    grouped = frame.groupby("h1_level_attack_reject_reason", dropna=False).agg(
        setups=("h1_level_attack_reject_reason", "size"),
        symbols=("symbol", "nunique"),
    ).reset_index()
    if "large_runner_5m_prefilter_passed" in frame.columns:
        passed = frame.loc[_bool_series(frame, "large_runner_5m_prefilter_passed")]
        passed_counts = passed.groupby("h1_level_attack_reject_reason", dropna=False).size().rename("prefilter_passed_setups")
        grouped = grouped.merge(passed_counts, how="left", on="h1_level_attack_reject_reason")
    else:
        grouped["prefilter_passed_setups"] = 0
    grouped["prefilter_passed_setups"] = grouped["prefilter_passed_setups"].fillna(0).astype(int)
    return grouped.sort_values("setups", ascending=False).reset_index(drop=True)


def _level_attack_top_growth_coverage(top_growth: pd.DataFrame, setups: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "symbol",
        "period_start_ms",
        "period_start_utc",
        "hour_high_return_pct",
        "first15_high_return_pct",
        "lookback_start_ms",
        "lookback_end_ms",
        "level_attack_setups",
        "level_attack_first_offset_min",
        "level_attack_prefilter_passed_setups",
        "level_attack_arm_matches",
        "level_attack_advance_before_level",
        "level_attack_entry_in_crossing",
        "level_attack_seed_high_crossed",
        "level_attack_seed_close_crossed",
        "level_attack_attack_zone70",
        "level_attack_flow_beats_prior",
        "best_progress_to_level",
        "best_quote_vs_prior_spike",
        "best_trades_vs_prior_spike",
        "covered_by_level_attack_before_top",
        "coverage_stage",
    ]
    if top_growth.empty:
        return pd.DataFrame(columns=columns)
    setup_frame = setups.copy()
    match_frame = matches.copy()
    if not setup_frame.empty and "h1_level_attack_candidate" in setup_frame.columns:
        setup_frame = setup_frame.loc[_bool_series(setup_frame, "h1_level_attack_candidate")].copy()
    else:
        setup_frame = pd.DataFrame()
    if not match_frame.empty and "h1_level_attack_candidate" in match_frame.columns:
        match_frame = match_frame.loc[_bool_series(match_frame, "h1_level_attack_candidate")].copy()
    else:
        match_frame = pd.DataFrame()

    rows: list[dict[str, object]] = []
    for _, top in top_growth.iterrows():
        symbol = str(top.get("symbol", ""))
        period_start = int(top.get("period_start_ms", 0))
        lookback_start = period_start - 60 * MINUTE_MS
        lookback_end = period_start + 15 * MINUTE_MS
        setup_window = _rows_for_symbol_time(setup_frame, symbol=symbol, time_column="seed_open_ms", start_ms=lookback_start, end_ms=lookback_end)
        match_window = _rows_for_symbol_time(match_frame, symbol=symbol, time_column="seed_open_ms", start_ms=lookback_start, end_ms=lookback_end)
        if setup_window.empty:
            coverage_stage = "no_level_attack_setup_in_lookback"
        elif match_window.empty:
            coverage_stage = "level_attack_setup_but_no_arm_match"
        else:
            coverage_stage = "level_attack_arm_matched"
        first_offset = float("nan")
        if not setup_window.empty:
            first_seed = int(pd.to_numeric(setup_window["seed_open_ms"], errors="coerce").min())
            first_offset = _safe_divide(first_seed - period_start, MINUTE_MS)
        rows.append(
            {
                "symbol": symbol,
                "period_start_ms": period_start,
                "period_start_utc": top.get("period_start_utc", ""),
                "hour_high_return_pct": top.get("hour_high_return_pct", float("nan")),
                "first15_high_return_pct": top.get("first15_high_return_pct", float("nan")),
                "lookback_start_ms": int(lookback_start),
                "lookback_end_ms": int(lookback_end),
                "level_attack_setups": int(len(setup_window)),
                "level_attack_first_offset_min": first_offset,
                "level_attack_prefilter_passed_setups": int(_bool_series(setup_window, "large_runner_5m_prefilter_passed").sum()) if not setup_window.empty else 0,
                "level_attack_arm_matches": int(len(match_window)),
                "level_attack_advance_before_level": int(_string_equals_series(setup_window, "h1_entry_mode_vs_nearest_level", "advance_before_level").sum()) if not setup_window.empty else 0,
                "level_attack_entry_in_crossing": int(_bool_series(setup_window, "h1_entry_in_level_crossing").sum()) if not setup_window.empty else 0,
                "level_attack_seed_high_crossed": int(_bool_series(setup_window, "h1_level_crossed_by_seed_high").sum()) if not setup_window.empty else 0,
                "level_attack_seed_close_crossed": int(_bool_series(setup_window, "h1_level_crossed_by_seed_close").sum()) if not setup_window.empty else 0,
                "level_attack_attack_zone70": int(_bool_series(setup_window, "h1_is_in_attack_zone_70").sum()) if not setup_window.empty else 0,
                "level_attack_flow_beats_prior": int(_bool_series(setup_window, "h1_attack_flow_beats_prior_spikes").sum()) if not setup_window.empty else 0,
                "best_progress_to_level": float(pd.to_numeric(setup_window.get("h1_progress_to_level_from_pullback", pd.Series(dtype=float)), errors="coerce").max()) if not setup_window.empty else float("nan"),
                "best_quote_vs_prior_spike": float(pd.to_numeric(setup_window.get("h1_attack_quote_vs_prior_spike_max", pd.Series(dtype=float)), errors="coerce").max()) if not setup_window.empty else float("nan"),
                "best_trades_vs_prior_spike": float(pd.to_numeric(setup_window.get("h1_attack_trades_vs_prior_spike_max", pd.Series(dtype=float)), errors="coerce").max()) if not setup_window.empty else float("nan"),
                "covered_by_level_attack_before_top": bool(not setup_window.empty),
                "coverage_stage": coverage_stage,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _stability_report(portfolio: pd.DataFrame, *, config: LargeRunnerDiscoveryConfig) -> pd.DataFrame:
    if portfolio.empty or "status" not in portfolio.columns:
        return pd.DataFrame()
    closed = portfolio.loc[portfolio["status"].astype(str).eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    if "net_r" not in closed.columns:
        net = pd.to_numeric(closed.get("net_return", pd.Series(index=closed.index)), errors="coerce")
        risk = pd.to_numeric(closed.get("initial_risk_pct", pd.Series(index=closed.index)), errors="coerce")
        closed["net_r"] = net / risk.replace(0.0, np.nan)
    frames: list[tuple[str, pd.DataFrame]] = [("portfolio_all", closed)]
    exploded = _explode_multi_value(closed, "large_runner_nature_trade_rule_ids", "category_id")
    if not exploded.empty:
        for category_id, group in exploded.groupby("category_id", dropna=False):
            frames.append((f"trade_rule:{category_id}", group.copy()))
    rows: list[dict[str, object]] = []
    for family_id, frame in frames:
        rows.append({"family_id": family_id, "scope": "portfolio", **_stability_metrics(frame, days=config.days)})
    return pd.DataFrame(rows)


def _stability_metrics(frame: pd.DataFrame, *, days: int) -> dict[str, object]:
    if frame.empty:
        return {"closed_trades": 0}
    work = frame.copy()
    work["net_r"] = pd.to_numeric(work.get("net_r", pd.Series(index=work.index)), errors="coerce")
    work = work.dropna(subset=["net_r"]).copy()
    if work.empty:
        return {"closed_trades": 0}
    result: dict[str, object] = {
        "closed_trades": int(len(work)),
        "trades_per_day": _safe_divide(float(len(work)), float(days)),
        "symbols": int(work["symbol"].nunique()) if "symbol" in work.columns else 0,
        "win_rate": float(work["net_r"].gt(0.0).mean()),
        "avg_r": float(work["net_r"].mean()),
        "median_r": float(work["net_r"].median()),
        "sum_r": float(work["net_r"].sum()),
    }
    for pct in (10, 20, 30, 40, 50):
        result[f"remove_top_{pct}_pct_trades_R"] = _remove_top_pct_trades_sum_r(work, pct=pct)
        result[f"remove_top_{pct}_pct_symbols_R"] = _remove_top_pct_symbols_sum_r(work, pct=pct)
    if "entry_timestamp_ms" in work.columns:
        ts = pd.to_datetime(pd.to_numeric(work["entry_timestamp_ms"], errors="coerce"), unit="ms", utc=True)
        day_sum = work["net_r"].groupby(ts.dt.date).sum()
        week_sum = work["net_r"].groupby(ts.dt.strftime("%G-W%V")).sum()
        month_sum = work["net_r"].groupby(ts.dt.strftime("%Y-%m")).sum()
        result.update(
            {
                "active_days": int(day_sum.size),
                "positive_days": int(day_sum.gt(0.0).sum()),
                "positive_day_rate": float(day_sum.gt(0.0).mean()) if day_sum.size else float("nan"),
                "positive_weeks": int(week_sum.gt(0.0).sum()),
                "positive_week_rate": float(week_sum.gt(0.0).mean()) if week_sum.size else float("nan"),
                "positive_months": int(month_sum.gt(0.0).sum()),
                "positive_month_rate": float(month_sum.gt(0.0).mean()) if month_sum.size else float("nan"),
                "max_day_dd_R": float(min(0.0, day_sum.min())) if day_sum.size else float("nan"),
                "max_week_dd_R": float(min(0.0, week_sum.min())) if week_sum.size else float("nan"),
                "max_month_dd_R": float(min(0.0, month_sum.min())) if month_sum.size else float("nan"),
            }
        )
    return result


def _remove_top_pct_trades_sum_r(frame: pd.DataFrame, *, pct: int) -> float:
    if frame.empty or "net_r" not in frame.columns:
        return float("nan")
    ordered = frame.sort_values("net_r", ascending=False)
    drop_n = int(math.ceil(len(ordered) * float(pct) / 100.0))
    return float(ordered.iloc[drop_n:]["net_r"].sum()) if drop_n < len(ordered) else 0.0


def _remove_top_pct_symbols_sum_r(frame: pd.DataFrame, *, pct: int) -> float:
    if frame.empty or "symbol" not in frame.columns or "net_r" not in frame.columns:
        return float("nan")
    by_symbol = frame.groupby("symbol", dropna=False)["net_r"].sum().sort_values(ascending=False)
    drop_n = int(math.ceil(len(by_symbol) * float(pct) / 100.0))
    kept_symbols = set(by_symbol.iloc[drop_n:].index.astype(str)) if drop_n < len(by_symbol) else set()
    if not kept_symbols:
        return 0.0
    return float(frame.loc[frame["symbol"].astype(str).isin(kept_symbols), "net_r"].sum())


def _session_features(timestamp_ms: int) -> dict[str, object]:
    ts = pd.to_datetime(int(timestamp_ms), unit="ms", utc=True)
    hour = int(ts.hour)
    minute_of_day = hour * 60 + int(ts.minute)
    sessions = {
        "asia": (0, 8 * 60),
        "europe": (7 * 60, 16 * 60),
        "us": (13 * 60, 22 * 60),
    }
    active = {name: start <= minute_of_day < end for name, (start, end) in sessions.items()}
    active_names = [name for name, enabled in active.items() if enabled]
    primary = active_names[-1] if active_names else "off_session"
    if primary in sessions:
        start, end = sessions[primary]
        from_open = minute_of_day - start
        to_close = end - minute_of_day
    else:
        from_open = float("nan")
        to_close = float("nan")
    return {
        "hour_utc": hour,
        "weekday": int(ts.weekday()),
        "session_asia": bool(active["asia"]),
        "session_europe": bool(active["europe"]),
        "session_us": bool(active["us"]),
        "session_overlap": bool(sum(1 for value in active.values() if value) >= 2),
        "session_primary": primary,
        "minutes_from_session_open": from_open,
        "minutes_to_session_close": to_close,
    }



def _feature_deciles(setups: pd.DataFrame) -> pd.DataFrame:
    if setups.empty:
        return pd.DataFrame()
    labels = ["runner_high10_next60", "runner_high20_next60", "runner_close10_next60"]
    features = [
        "early_return_pct",
        "early_quote_sum",
        "early_trades_sum",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "current_vs_prior_max_quote_24h",
        "pre60_range_pct",
        "m1_last2_quote_share",
        "m1_quote_top1_share",
        "m1_elevated_both_count_3x",
        "oi_change_early_pct",
    ]
    rows: list[pd.DataFrame] = []
    for label in labels:
        if label not in setups.columns:
            continue
        for feature in features:
            if feature not in setups.columns:
                continue
            data = setups[[feature, label]].dropna().copy()
            if data[feature].nunique() < 4:
                continue
            try:
                data["bin"] = pd.qcut(data[feature], q=10, duplicates="drop")
            except ValueError:
                continue
            grouped = data.groupby("bin", observed=True).agg(
                rows=(label, "size"),
                positive=(label, "sum"),
                positive_rate=(label, "mean"),
                median_feature=(feature, "median"),
            )
            grouped = grouped.reset_index()
            grouped["feature"] = feature
            grouped["label"] = label
            rows.append(grouped)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def _funnel(raw: pd.DataFrame, setups: pd.DataFrame, matches: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"stage": "raw_broad_5m_candidates", "rows": int(len(raw))},
        {"stage": "cluster_selected_setups", "rows": int(len(setups))},
        {"stage": "large_runner_arm_matches", "rows": int(len(matches))},
        {"stage": "trade_grid_rows", "rows": int(len(trades))},
    ]
    if not trades.empty and "status" in trades.columns:
        for status, count in trades["status"].astype(str).value_counts().items():
            rows.append({"stage": f"trade_status_{status}", "rows": int(count)})
    return pd.DataFrame(rows)


def _data_quality_row(*, symbol: str, frame_5m: pd.DataFrame, frame_1m: pd.DataFrame) -> dict[str, object]:
    return {
        "symbol": symbol,
        "5m_rows": int(len(frame_5m)),
        "1m_rows": int(len(frame_1m)),
        "5m_quote_volume_source": "quote_volume" if "quote_volume" in frame_5m.columns else "missing",
        "5m_trade_count_source": "number_of_trades" if "number_of_trades" in frame_5m.columns else "missing",
        "1m_quote_volume_source": "quote_volume" if "quote_volume" in frame_1m.columns else "missing",
        "1m_trade_count_source": "number_of_trades" if "number_of_trades" in frame_1m.columns else "missing",
        "taker_buy_quote_source": "taker_buy_quote_volume" if "taker_buy_quote_volume" in frame_1m.columns else "missing",
        "oi_source": "cached_5m_open_interest" if "open_interest" in frame_5m.columns else "missing",
        "data_rejection": "",
    }


def _load_frame(storage: ParquetStorage, symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> pd.DataFrame:
    try:
        result = storage.load_window_result(symbol, Timeframe(str(timeframe)), start_timestamp_ms=int(start_ms), end_timestamp_ms=int(end_ms))
    except Exception:
        return pd.DataFrame()
    if not result.ok or result.frame.empty:
        return pd.DataFrame()
    return _prepare_ohlcv(result.frame)


def _prepare_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    prepared = frame.copy()
    for column in ("timestamp", "open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume", "open_interest"):
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"]).copy()
    return prepared.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _has_real_flow(frame: pd.DataFrame) -> bool:
    return (
        not frame.empty
        and "quote_volume" in frame.columns
        and "number_of_trades" in frame.columns
        and frame["quote_volume"].notna().any()
        and frame["number_of_trades"].notna().any()
    )


def _resolve_symbols(cache_dir: Path, symbols: Iterable[str] | None) -> list[str]:
    if symbols:
        return sorted({_canonical_symbol(symbol) for symbol in symbols if str(symbol).strip()})
    result: list[str] = []
    for path in sorted(Path(cache_dir).iterdir()) if Path(cache_dir).exists() else []:
        if path.is_dir() and (path / "5m" / "data.parquet").exists():
            result.append(ParquetStorage.decode_symbol_from_path(path.name))
    return result


def _resolve_end_timestamp_ms(config: LargeRunnerDiscoveryConfig, storage: ParquetStorage, symbols: tuple[str, ...]) -> int:
    if config.end_timestamp_ms is not None:
        return int(config.end_timestamp_ms)
    values = [storage.get_last_timestamp(symbol, Timeframe("5m")) for symbol in symbols]
    finite = [int(value) for value in values if value is not None]
    if not finite:
        raise ValueError("no cached 5m data found")
    return max(finite)


def _canonical_symbol(symbol: object) -> str:
    value = str(symbol).strip().upper()
    if ":" in value:
        return value
    if "/" in value:
        return f"{value}:USDT" if value.endswith("/USDT") else value
    if value.endswith("USDT") and len(value) > 4:
        return f"{value[:-4]}/USDT:USDT"
    return value


def _window(frame: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
    return frame.loc[(timestamps >= int(start_ms)) & (timestamps < int(end_ms))].copy()


def _row_at_timestamp(frame: pd.DataFrame, timestamp_ms: int) -> pd.Series | None:
    if frame.empty:
        return None
    rows = frame.loc[pd.to_numeric(frame["timestamp"], errors="coerce").eq(int(timestamp_ms))]
    return None if rows.empty else rows.iloc[0]


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(bool)
    return values.astype(str).str.lower().isin({"true", "1", "yes", "y"})


def _string_equals_series(frame: pd.DataFrame, column: str, value: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).eq(str(value))


def _safe_divide_series(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    with np.errstate(divide="ignore", invalid="ignore"):
        return numerator / denominator.replace(0.0, np.nan)


def _safe_divide(numerator: float, denominator: float) -> float:
    try:
        n = float(numerator)
        d = float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(n) or not np.isfinite(d) or abs(d) < 1e-12:
        return float("nan")
    return n / d


def _float(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) and np.isfinite(float(value))
    return str(value).strip().lower() in {"true", "1", "yes", "y", "ok"}


def _nan_or_ge(value: object, threshold: float) -> bool:
    parsed = _float(value)
    return not np.isfinite(parsed) or parsed >= float(threshold)


def _cluster_start(timestamp_ms: int, *, config: LargeRunnerDiscoveryConfig) -> int:
    cluster_ms = int(config.setup_cluster_minutes) * MINUTE_MS
    return int(timestamp_ms) // cluster_ms * cluster_ms


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


def _safe_console_text(value: object) -> str:
    text = str(value)
    encoding = "ascii" if os.name == "nt" else "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def _effective_workers(value: object, *, total_items: int) -> int:
    if total_items <= 1:
        return 1
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = 1
    if requested <= 1:
        return 1
    return max(1, min(requested, total_items, os.cpu_count() or 1, 4))


def _metric_map(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {}
    if {"metric", "value"}.issubset(frame.columns):
        return {str(row["metric"]): row["value"] for _, row in frame.iterrows()}
    row = frame.iloc[0].to_dict()
    return {str(key): value for key, value in row.items()}


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    if frame.empty and len(frame.columns) == 0:
        pd.DataFrame(columns=["artifact_status"]).to_csv(path, index=False, encoding="utf-8-sig")
        return
    frame.to_csv(path, index=False, encoding="utf-8-sig")
