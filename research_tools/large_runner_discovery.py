"""Cache-only large-runner discovery for pump-awakening research.

This module is intentionally separate from the current HTF/LTF runner
discovery. It is a cheaper research sweep over 5m + 1m cached candles:

* 5m candles find broad first-awakening setups and hourly runner labels;
* 1m candles describe intra-seed tape and simulate entry/exit paths;
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


MINUTE_MS = 60_000
FIVE_MINUTE_MS = 5 * MINUTE_MS
HOUR_MS = 60 * MINUTE_MS

LARGE_RUNNER_DISCOVERY_ID = "large_runner_discovery_v1"


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
        first_setups = _first_setup_per_cluster(raw, config=config)
        top_hours = _hourly_growth_rows(symbol=symbol, frame_5m=prepared_5m, start_ms=start_ms, end_ms=end_ms)
        if not first_setups:
            quality[0]["1m_load_scope"] = "not_loaded_no_broad_setups"
            return {"raw": raw, "setups": [], "matches": [], "trades": [], "quality": quality, "top_hours": top_hours}
        setup_rows: list[dict[str, object]] = []
        enrichable_setups: list[dict[str, object]] = []
        for setup in first_setups:
            prefilter = _large_runner_5m_prefilter(setup, frame_5m=prepared_5m)
            if bool(prefilter["large_runner_5m_prefilter_passed"]):
                enrichable_setups.append({**setup, **prefilter})
            else:
                setup_rows.append({**setup, **prefilter, "enrichment_status": "skipped_1m_prefilter_no_large_runner_arm_possible"})
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
                match = _build_arm_match(enriched, arm=arm, frame_1m=frame_1m, config=config)
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
        ("large_runner_by_day.csv", _summary_by_day(trade_frame)),
        ("large_runner_mfe_mae.csv", _mfe_mae_frame(trade_frame)),
        ("large_runner_skip_reasons.csv", _skip_reasons(trade_frame)),
        ("large_runner_top_dependency.csv", _top_dependency(trade_frame)),
        ("large_runner_portfolio_top_dependency.csv", _top_dependency(portfolio_frame)),
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
                        "future_label_model": "evaluation_only_not_used_for_rule_matching",
                        "candidate_model": "first_broad_5m_awakening_per_symbol_per_60m_cluster",
                        "top_growth_audit_model": "evaluation_only_first15_full_hour_and_pre60_windows",
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


def _first_setup_per_cluster(raw: list[dict[str, object]], *, config: LargeRunnerDiscoveryConfig) -> list[dict[str, object]]:
    del config
    first: dict[tuple[str, int], dict[str, object]] = {}
    for row in sorted(raw, key=lambda item: (str(item.get("symbol", "")), int(item.get("timestamp_ms", 0)))):
        key = (str(row.get("symbol", "")), int(row.get("cluster_start_ms", 0)))
        first.setdefault(key, row)
    return list(first.values())


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
    labels = _future_labels(frame_1m, seed_open=seed_open, config=config)
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
    result.update(evaluate_large_runner_nature(result).as_features())
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
        "initial_risk_pct": float(risk_pct),
        "win": bool(net_return > 0.0),
        "future_label_available_at_entry": False,
    }


def _future_labels(frame_1m: pd.DataFrame, *, seed_open: int, config: LargeRunnerDiscoveryConfig) -> dict[str, object]:
    horizon_end = int(seed_open) + int(config.horizon_minutes) * MINUTE_MS
    path = _window(frame_1m, int(seed_open), horizon_end)
    if path.empty:
        return {
            "future60_high_return_pct": float("nan"),
            "future60_close_return_pct": float("nan"),
            "future60_min_path_pct": float("nan"),
            "runner_high10_next60": False,
            "runner_high20_next60": False,
            "runner_high30_next60": False,
            "runner_close10_next60": False,
            "runner_close20_next60": False,
        }
    first_open = float(path.iloc[0]["open"])
    future_high = float(path["high"].max())
    future_low = float(path["low"].min())
    future_close = float(path.iloc[-1]["close"])
    high_ret = _safe_divide(future_high - first_open, first_open)
    close_ret = _safe_divide(future_close - first_open, first_open)
    min_ret = _safe_divide(future_low - first_open, first_open)
    return {
        "future60_high_return_pct": high_ret,
        "future60_close_return_pct": close_ret,
        "future60_min_path_pct": min_ret,
        "runner_high10_next60": bool(high_ret >= 0.10),
        "runner_high20_next60": bool(high_ret >= 0.20),
        "runner_high30_next60": bool(high_ret >= 0.30),
        "runner_close10_next60": bool(close_ret >= 0.10),
        "runner_close20_next60": bool(close_ret >= 0.20),
    }


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
        {"stage": "first_setup_per_symbol_60m", "rows": int(len(setups))},
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
