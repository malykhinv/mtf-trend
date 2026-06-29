from __future__ import annotations

import csv
import math
import random
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.decision import ExpectedValueRow
from anomaly_science.contracts.market import Candle1m, FundingRate, MarketDataContractError, ONE_MINUTE_MS
from anomaly_science.contracts.simulation import (
    BARRIER_OUTCOME_TEMPORAL_CONTRACT,
    BARRIER_OUTCOME_VERSION,
    TRADE_SIMULATION_TEMPORAL_CONTRACT,
    BarrierOutcomeRow,
    TradeSimulationMetricRow,
    TradeSimulationRow,
)
from anomaly_science.data.normalized import normalize_candles_1m, normalize_funding_rates
from anomaly_science.data.source import CsvDataSourceError, MarketDataSource
from anomaly_science.decision import load_anomaly_decision_timing_csv
from anomaly_science.simulation.config import TradeSimulationConfig
from anomaly_science.strategy.registry import get_strategy
from anomaly_science.strategy.execution import BarrierTrigger, PositionSide, StructuralAnchor


class TradeSimulationInputError(ValueError):
    """Raised when simulation inputs cannot produce clean trade rows."""


class TradeSimulationArtifactError(ValueError):
    """Raised when simulation artifacts violate strict schemas."""


def build_trade_simulation_from_source(
    *,
    source: MarketDataSource,
    decision_timing_path: str | Path,
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    funding_frame = source.read_frame("funding_rate", required=False)
    return build_trade_simulation_rows(
        candles_1m=normalize_candles_1m(frame),
        funding_rates=normalize_funding_rates(funding_frame),
        decision_rows=load_anomaly_decision_timing_csv(decision_timing_path),
        config=config,
    )


def build_random_entry_time_control_from_source(
    *,
    source: MarketDataSource,
    decision_timing_path: str | Path,
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    funding_frame = source.read_frame("funding_rate", required=False)
    return build_random_entry_time_control_rows(
        candles_1m=normalize_candles_1m(frame),
        funding_rates=normalize_funding_rates(funding_frame),
        decision_rows=load_anomaly_decision_timing_csv(decision_timing_path),
        config=config,
    )



def build_matched_market_time_control_from_source(
    *,
    source: MarketDataSource,
    decision_timing_path: str | Path,
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    funding_frame = source.read_frame("funding_rate", required=False)
    return build_matched_market_time_control_rows(
        candles_1m=normalize_candles_1m(frame),
        funding_rates=normalize_funding_rates(funding_frame),
        decision_rows=load_anomaly_decision_timing_csv(decision_timing_path),
        config=config,
    )

def build_barrier_outcomes_from_source(
    *,
    source: MarketDataSource,
    decision_timing_path: str | Path,
    config: TradeSimulationConfig | None = None,
) -> tuple[BarrierOutcomeRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    return build_barrier_outcome_rows(
        candles_1m=normalize_candles_1m(frame),
        decision_rows=load_anomaly_decision_timing_csv(decision_timing_path),
        config=config,
    )


def build_trade_simulation_rows(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    funding_rates: Sequence[FundingRate] | Iterable[FundingRate] = (),
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    candles_by_symbol = _candles_by_symbol(candles_1m)
    funding_by_symbol = _funding_by_symbol(funding_rates)

    rows: list[TradeSimulationRow] = []
    active_until_by_variant: dict[tuple[str, str, str, str, str, float], int] = {}
    strategy = get_strategy(cfg.strategy_name)
    for decision in sorted(decision_rows, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id)):
        if decision.target_horizon_minutes != cfg.target_horizon_minutes:
            continue
        if decision.strategy_name != strategy.metadata.strategy_name or decision.strategy_version != strategy.metadata.strategy_version:
            continue
        if not _is_simulatable_decision(decision, config=cfg):
            continue
        symbol_candles = candles_by_symbol.get(decision.symbol, [])
        for close_fraction in _target_close_fraction_grid(strategy=strategy, decision=decision):
            key = (
                decision.strategy_name,
                decision.strategy_version,
                decision.symbol,
                decision.stop_policy_id,
                decision.target_policy_id,
                close_fraction,
            )
            active_until_ms = active_until_by_variant.get(key)
            if active_until_ms is not None and decision.snapshot_time_ms <= active_until_ms:
                continue
            row = _simulate_decision(
                decision=decision,
                candles=symbol_candles,
                funding_rates=funding_by_symbol.get(decision.symbol, []),
                config=cfg,
                target_close_fraction=close_fraction,
            )
            rows.append(row)
            active_until_by_variant[key] = row.exit_time_ms
    return tuple(rows)


def _candles_by_symbol(candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> dict[str, list[Candle1m]]:
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for candles in candles_by_symbol.values():
        candles.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    return candles_by_symbol


def _funding_by_symbol(funding_rates: Sequence[FundingRate] | Iterable[FundingRate]) -> dict[str, list[FundingRate]]:
    funding_by_symbol: dict[str, list[FundingRate]] = {}
    for funding_rate in funding_rates:
        funding_by_symbol.setdefault(funding_rate.symbol, []).append(funding_rate)
    for rates in funding_by_symbol.values():
        rates.sort(key=lambda item: item.timestamp_ms)
    return funding_by_symbol


def build_barrier_outcome_rows(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    config: TradeSimulationConfig | None = None,
) -> tuple[BarrierOutcomeRow, ...]:
    """Build static realized barrier outcomes for future utility modeling.

    This is a labels-only artifact. It intentionally does not change decision
    selection or trade simulation PnL. It records what would have happened after
    the next 1m open if the selected structural side used its registered stop and
    target references, with same-candle target/stop collisions resolved
    pessimistically as stop_loss_first.
    """
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    candles_by_symbol = _candles_by_symbol(candles_1m)
    strategy = get_strategy(cfg.strategy_name)
    rows: list[BarrierOutcomeRow] = []
    for decision in sorted(decision_rows, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id)):
        if decision.target_horizon_minutes != cfg.target_horizon_minutes:
            continue
        if decision.strategy_name != strategy.metadata.strategy_name or decision.strategy_version != strategy.metadata.strategy_version:
            continue
        if not _has_static_barrier_decision(decision):
            continue
        try:
            rows.append(_realized_barrier_outcome(decision=decision, candles=candles_by_symbol.get(decision.symbol, []), config=cfg))
        except TradeSimulationInputError:
            continue
    return tuple(rows)


def build_random_entry_time_control_rows(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    funding_rates: Sequence[FundingRate] | Iterable[FundingRate] = (),
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    candles_by_symbol = _candles_by_symbol(candles_1m)
    funding_by_symbol = _funding_by_symbol(funding_rates)
    candidates_by_symbol: dict[str, list[ExpectedValueRow]] = {}
    strategy = get_strategy(cfg.strategy_name)
    for decision in decision_rows:
        if decision.target_horizon_minutes != cfg.target_horizon_minutes:
            continue
        if decision.strategy_name != strategy.metadata.strategy_name or decision.strategy_version != strategy.metadata.strategy_version:
            continue
        if not _is_simulatable_decision(decision, config=cfg):
            continue
        candidates_by_symbol.setdefault(decision.symbol, []).append(decision)

    rng = random.Random(cfg.random_seed)
    rows: list[TradeSimulationRow] = []
    active_until_by_variant: dict[tuple[str, str, str, str, str, float], int] = {}
    for symbol in sorted(candidates_by_symbol):
        decisions = sorted(candidates_by_symbol[symbol], key=lambda item: (item.snapshot_time_ms, item.event_id))
        shuffled_snapshots = [decision.snapshot_time_ms for decision in decisions]
        rng.shuffle(shuffled_snapshots)
        for decision, random_snapshot_time_ms in zip(decisions, shuffled_snapshots):
            control_decision = replace(
                decision,
                event_id=f"{decision.event_id}::random_entry_time",
                state_time_ms=random_snapshot_time_ms,
                snapshot_time_ms=random_snapshot_time_ms,
                feature_cutoff_time_ms=random_snapshot_time_ms,
                future_start_time_ms=random_snapshot_time_ms + ONE_MINUTE_MS,
            )
            control_decision = _reanchor_random_control(
                decision=control_decision,
                candles=candles_by_symbol.get(control_decision.symbol, []),
            )
            if control_decision is None:
                continue
            for close_fraction in _target_close_fraction_grid(strategy=strategy, decision=control_decision):
                key = (
                    control_decision.strategy_name,
                    control_decision.strategy_version,
                    control_decision.symbol,
                    control_decision.stop_policy_id,
                    control_decision.target_policy_id,
                    close_fraction,
                )
                active_until_ms = active_until_by_variant.get(key)
                if active_until_ms is not None and control_decision.snapshot_time_ms <= active_until_ms:
                    continue
                try:
                    row = _simulate_decision(
                        decision=control_decision,
                        candles=candles_by_symbol.get(control_decision.symbol, []),
                        funding_rates=funding_by_symbol.get(control_decision.symbol, []),
                        config=cfg,
                        target_close_fraction=close_fraction,
                    )
                except TradeSimulationInputError:
                    continue
                rows.append(row)
                active_until_by_variant[key] = row.exit_time_ms
    return tuple(rows)




@dataclass(frozen=True, slots=True)
class _MarketTimeCandidate:
    snapshot_time_ms: int
    session_bucket: int
    volatility_bucket: int


def build_matched_market_time_control_rows(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    funding_rates: Sequence[FundingRate] | Iterable[FundingRate] = (),
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    """Build bounded same-symbol/session/volatility random market-time controls.

    This is deliberately different from ``build_random_entry_time_control_rows``.
    The older control permutes existing signal times and therefore remains
    conditioned on the strategy signal universe. This control samples from the
    candle stream itself, excluding known signal snapshots for the same symbol,
    while matching coarse session and trailing-volatility buckets.
    """
    cfg = config or TradeSimulationConfig()
    if not cfg.matched_market_time_control_enabled:
        return ()
    _validate_strategy_horizon(config=cfg)
    candles_by_symbol = _candles_by_symbol(candles_1m)
    funding_by_symbol = _funding_by_symbol(funding_rates)
    candidate_index_by_symbol = {
        symbol: _market_time_candidate_index(candles=candles, config=cfg)
        for symbol, candles in candles_by_symbol.items()
    }
    candidate_times_by_symbol = {
        symbol: tuple(candidate.snapshot_time_ms for candidate in candidates)
        for symbol, candidates in candidate_index_by_symbol.items()
    }
    candidates_by_symbol_bucket: dict[tuple[str, int, int], list[_MarketTimeCandidate]] = {}
    for symbol, candidates in candidate_index_by_symbol.items():
        for candidate in candidates:
            candidates_by_symbol_bucket.setdefault((symbol, candidate.session_bucket, candidate.volatility_bucket), []).append(candidate)

    strategy = get_strategy(cfg.strategy_name)
    decisions = tuple(
        decision
        for decision in decision_rows
        if decision.target_horizon_minutes == cfg.target_horizon_minutes
        and decision.strategy_name == strategy.metadata.strategy_name
        and decision.strategy_version == strategy.metadata.strategy_version
        and _is_simulatable_decision(decision, config=cfg)
    )
    signal_times_by_symbol: dict[str, set[int]] = {}
    for decision in decisions:
        signal_times_by_symbol.setdefault(decision.symbol, set()).add(decision.snapshot_time_ms)

    rows: list[TradeSimulationRow] = []
    active_until_by_variant: dict[tuple[str, str, str, str, str, float], int] = {}
    for decision in sorted(decisions, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id)):
        reference = _nearest_market_time_candidate(
            candidates=candidate_index_by_symbol.get(decision.symbol, ()),
            candidate_times=candidate_times_by_symbol.get(decision.symbol, ()),
            snapshot_time_ms=decision.snapshot_time_ms,
        )
        if reference is None:
            continue
        raw_pool = candidates_by_symbol_bucket.get((decision.symbol, reference.session_bucket, reference.volatility_bucket), [])
        signal_times = signal_times_by_symbol.get(decision.symbol, set())
        pool = [candidate for candidate in raw_pool if candidate.snapshot_time_ms not in signal_times]
        bounded_pool = _bounded_market_time_pool(
            candidates=pool,
            max_candidates=cfg.max_matched_market_time_candidates_per_decision,
        )
        if not bounded_pool:
            continue
        rng = random.Random(f"{cfg.random_seed}:{decision.strategy_name}:{decision.event_id}:matched_market_time")
        selected = rng.choice(bounded_pool)
        control_decision = replace(
            decision,
            event_id=f"{decision.event_id}::matched_market_time",
            state_time_ms=selected.snapshot_time_ms,
            snapshot_time_ms=selected.snapshot_time_ms,
            feature_cutoff_time_ms=selected.snapshot_time_ms,
            future_start_time_ms=selected.snapshot_time_ms + ONE_MINUTE_MS,
        )
        control_decision = _reanchor_random_control(
            decision=control_decision,
            candles=candles_by_symbol.get(control_decision.symbol, []),
        )
        if control_decision is None:
            continue
        for close_fraction in _target_close_fraction_grid(strategy=strategy, decision=control_decision):
            key = (
                control_decision.strategy_name,
                control_decision.strategy_version,
                control_decision.symbol,
                control_decision.stop_policy_id,
                control_decision.target_policy_id,
                close_fraction,
            )
            active_until_ms = active_until_by_variant.get(key)
            if active_until_ms is not None and control_decision.snapshot_time_ms <= active_until_ms:
                continue
            try:
                row = _simulate_decision(
                    decision=control_decision,
                    candles=candles_by_symbol.get(control_decision.symbol, []),
                    funding_rates=funding_by_symbol.get(control_decision.symbol, []),
                    config=cfg,
                    target_close_fraction=close_fraction,
                )
            except TradeSimulationInputError:
                continue
            rows.append(row)
            active_until_by_variant[key] = row.exit_time_ms
    return tuple(rows)


def _market_time_candidate_index(*, candles: Sequence[Candle1m], config: TradeSimulationConfig) -> tuple[_MarketTimeCandidate, ...]:
    lookback = config.matched_market_time_lookback_minutes
    if len(candles) <= lookback + config.target_horizon_minutes + 1:
        return ()
    values: list[tuple[int, int, float]] = []
    horizon_ms = config.target_horizon_minutes * ONE_MINUTE_MS
    for index in range(lookback, len(candles) - 1):
        snapshot_time_ms = candles[index].open_time_ms
        if candles[index].available_time_ms > snapshot_time_ms + ONE_MINUTE_MS:
            continue
        if snapshot_time_ms + horizon_ms >= candles[-1].available_time_ms:
            continue
        window = candles[index - lookback : index]
        last_close = window[-1].close
        if last_close <= 0.0:
            continue
        trailing_range = max(candle.high for candle in window) - min(candle.low for candle in window)
        if trailing_range < 0.0 or not math.isfinite(trailing_range):
            continue
        values.append((snapshot_time_ms, _session_bucket(snapshot_time_ms, config=config), trailing_range / last_close))
    if not values:
        return ()
    ranked = sorted(value for _, _, value in values)
    denominator = max(1, len(ranked) - 1)
    result: list[_MarketTimeCandidate] = []
    for snapshot_time_ms, session_bucket, value in values:
        rank = _lower_bound(ranked, value)
        volatility_bucket = min(
            config.matched_market_time_volatility_buckets - 1,
            int(rank * config.matched_market_time_volatility_buckets / denominator),
        )
        result.append(_MarketTimeCandidate(snapshot_time_ms, session_bucket, volatility_bucket))
    return tuple(result)


def _nearest_market_time_candidate(
    *,
    candidates: Sequence[_MarketTimeCandidate],
    candidate_times: Sequence[int],
    snapshot_time_ms: int,
) -> _MarketTimeCandidate | None:
    if not candidates:
        return None
    insertion = _lower_bound_int(candidate_times, snapshot_time_ms)
    before = candidates[insertion - 1] if insertion > 0 else None
    after = candidates[insertion] if insertion < len(candidates) else None
    if before is None:
        return after
    if after is None:
        return before
    if abs(before.snapshot_time_ms - snapshot_time_ms) <= abs(after.snapshot_time_ms - snapshot_time_ms):
        return before
    return after


def _bounded_market_time_pool(*, candidates: Sequence[_MarketTimeCandidate], max_candidates: int) -> tuple[_MarketTimeCandidate, ...]:
    if len(candidates) <= max_candidates:
        return tuple(candidates)
    step = len(candidates) / float(max_candidates)
    return tuple(candidates[min(len(candidates) - 1, int(index * step))] for index in range(max_candidates))


def _session_bucket(snapshot_time_ms: int, *, config: TradeSimulationConfig) -> int:
    return snapshot_time_ms // (config.matched_market_time_session_minutes * ONE_MINUTE_MS)


def _lower_bound(values: Sequence[float], needle: float) -> int:
    low = 0
    high = len(values)
    while low < high:
        mid = (low + high) // 2
        if values[mid] < needle:
            low = mid + 1
        else:
            high = mid
    return low


def _lower_bound_int(values: Sequence[int], needle: int) -> int:
    low = 0
    high = len(values)
    while low < high:
        mid = (low + high) // 2
        if values[mid] < needle:
            low = mid + 1
        else:
            high = mid
    return low

def _reanchor_random_control(
    *,
    decision: ExpectedValueRow,
    candles: Sequence[Candle1m],
) -> ExpectedValueRow | None:
    asof = [candle for candle in candles if candle.available_time_ms <= decision.snapshot_time_ms]
    window = asof[-decision.target_horizon_minutes :]
    if not window:
        return None
    current = window[-1].close
    structural_low = min(candle.low for candle in window)
    structural_high = max(candle.high for candle in window)
    if decision.best_action == "long":
        stop_price, target_price = structural_low, structural_high
        stop_distance, target_distance = current - structural_low, structural_high - current
    else:
        stop_price, target_price = structural_high, structural_low
        stop_distance, target_distance = structural_high - current, current - structural_low
    if stop_distance <= 0.0 or target_distance <= 0.0:
        return None
    selected_rr = target_distance / stop_distance
    return replace(
        decision,
        entry_reference_price=current,
        stop_reference_price=stop_price,
        target_reference_price=target_price,
        stop_distance=stop_distance,
        target_distance=target_distance,
        RR_long_proxy=selected_rr if decision.best_action == "long" else 0.0,
        RR_short_proxy=selected_rr if decision.best_action == "short" else 0.0,
        RR_long_acceptable=decision.selected_RR_acceptable if decision.best_action == "long" else False,
        RR_short_acceptable=decision.selected_RR_acceptable if decision.best_action == "short" else False,
        selected_RR=selected_rr,
        selected_RR_acceptable=decision.selected_RR_acceptable,
        is_RR_still_acceptable=decision.selected_RR_acceptable,
        execution_policy_resolved=True,
    )


def build_trade_simulation_metric_rows(
    *,
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    simulation_rows: Sequence[TradeSimulationRow] | Iterable[TradeSimulationRow],
    random_entry_time_control_rows: Sequence[TradeSimulationRow] | Iterable[TradeSimulationRow] = (),
    matched_market_time_control_rows: Sequence[TradeSimulationRow] | Iterable[TradeSimulationRow] = (),
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationMetricRow, ...]:
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    strategy = get_strategy(cfg.strategy_name)
    decisions = tuple(
        row
        for row in decision_rows
        if row.target_horizon_minutes == cfg.target_horizon_minutes
        and row.strategy_name == strategy.metadata.strategy_name
        and row.strategy_version == strategy.metadata.strategy_version
    )
    trades = tuple(simulation_rows)
    random_entry_trades = tuple(random_entry_time_control_rows)
    matched_market_trades = tuple(matched_market_time_control_rows)
    metrics: list[TradeSimulationMetricRow] = []

    def add(
        name: str,
        value: str | float | int,
        row_count: int,
        notes: str,
        *,
        variant: tuple[str, str, float] | None = None,
    ) -> None:
        execution_variant_id = "all_declared_variants"
        stop_policy_id = ""
        target_policy_id = ""
        target_close_fraction: float | None = None
        if variant is not None:
            stop_policy_id, target_policy_id, target_close_fraction = variant
            execution_variant_id = _execution_variant_id(*variant)
        metrics.append(
            TradeSimulationMetricRow(
                simulation_version=cfg.simulation_version,
                target_horizon_minutes=cfg.target_horizon_minutes,
                execution_variant_id=execution_variant_id,
                stop_policy_id=stop_policy_id,
                target_policy_id=target_policy_id,
                target_close_fraction=target_close_fraction,
                metric_name=name,
                metric_value=_metric_value(value),
                row_count=row_count,
                notes=notes,
            )
        )

    add("decision_rows", len(decisions), len(decisions), "decision timing rows considered for this horizon")
    add(
        "simulated_trade_variant_rows",
        len(trades),
        len(trades),
        "research rows across declared execution variants; not positions in one deployable portfolio",
    )

    variants = sorted({_simulation_variant(row) for row in (*trades, *random_entry_trades, *matched_market_trades)})
    if not variants:
        empty_note = "no simulatable execution variant rows; zero-valued negative control for an empty run"
        add("always_no_trade_baseline_net_pnl", 0.0, len(decisions), empty_note)
        add("random_entry_time_control_rows", 0, len(decisions), empty_note)
        add("random_entry_time_control_total_net_pnl", 0.0, 0, empty_note)
        add("matched_market_time_control_rows", 0, len(decisions), empty_note)
        add("matched_market_time_control_total_net_pnl", 0.0, 0, empty_note)
        add("delta_vs_always_no_trade_net_pnl", 0.0, 0, empty_note)
        add("delta_vs_random_entry_time_net_pnl", 0.0, 0, empty_note)
        add("delta_vs_matched_market_time_net_pnl", 0.0, 0, empty_note)
    for variant in variants:
        scoped_trades = tuple(row for row in trades if _simulation_variant(row) == variant)
        scoped_random = tuple(row for row in random_entry_trades if _simulation_variant(row) == variant)
        scoped_matched_market = tuple(row for row in matched_market_trades if _simulation_variant(row) == variant)
        stop_policy_id, target_policy_id, _ = variant
        scoped_decisions = tuple(
            row
            for row in decisions
            if row.stop_policy_id == stop_policy_id and row.target_policy_id == target_policy_id
        )
        total_net_pnl = sum(row.net_pnl for row in scoped_trades)
        random_entry_total_net_pnl = sum(row.net_pnl for row in scoped_random)
        matched_market_total_net_pnl = sum(row.net_pnl for row in scoped_matched_market)
        add("simulated_trade_rows", len(scoped_trades), len(scoped_trades), "executed rows for this one structural execution variant", variant=variant)
        add(
            "non_trade_decision_rows",
            max(0, len(scoped_decisions) - len(scoped_trades)),
            len(scoped_decisions),
            "eligible decisions not simulated for this execution variant",
            variant=variant,
        )
        add("always_no_trade_baseline_net_pnl", 0.0, len(scoped_decisions), "always no-trade baseline for this variant", variant=variant)
        add("random_entry_time_control_rows", len(scoped_random), len(scoped_decisions), "deterministic random-time control for this variant", variant=variant)
        add("random_entry_time_control_total_net_pnl", random_entry_total_net_pnl, len(scoped_random), "random-time control net PnL for this variant", variant=variant)
        add(
            "matched_market_time_control_rows",
            len(scoped_matched_market),
            len(scoped_decisions),
            "bounded same-symbol/session/volatility random market-time control rows for this variant",
            variant=variant,
        )
        add(
            "matched_market_time_control_total_net_pnl",
            matched_market_total_net_pnl,
            len(scoped_matched_market),
            "matched market-time control net PnL for this variant",
            variant=variant,
        )
        add("delta_vs_always_no_trade_net_pnl", total_net_pnl, len(scoped_trades), "variant net PnL minus no-trade baseline", variant=variant)
        add("delta_vs_random_entry_time_net_pnl", total_net_pnl - random_entry_total_net_pnl, len(scoped_trades), "variant net PnL minus its matched random-time control", variant=variant)
        add(
            "delta_vs_matched_market_time_net_pnl",
            total_net_pnl - matched_market_total_net_pnl,
            len(scoped_trades),
            "variant net PnL minus its bounded same-symbol/session/volatility market-time control",
            variant=variant,
        )
        if scoped_trades:
            add("win_rate", _mean(1.0 if row.net_pnl > 0.0 else 0.0 for row in scoped_trades), len(scoped_trades), "share of positive rows for this variant", variant=variant)
            add("mean_net_pnl", _mean(row.net_pnl for row in scoped_trades), len(scoped_trades), "mean net PnL in price units for this variant", variant=variant)
            add("mean_net_pnl_r", _mean(row.net_pnl_r for row in scoped_trades), len(scoped_trades), "mean net PnL in structural stop-distance R", variant=variant)
            add("total_net_pnl", total_net_pnl, len(scoped_trades), "sum net PnL for this variant only", variant=variant)
            add("stop_loss_share", _mean(1.0 if "stop" in row.exit_reason else 0.0 for row in scoped_trades), len(scoped_trades), "share whose remaining position exited at structural stop", variant=variant)
            add("target_hit_share", _mean(1.0 if row.target_was_hit else 0.0 for row in scoped_trades), len(scoped_trades), "share touching the structural target", variant=variant)
    return tuple(metrics)


def _simulation_variant(row: TradeSimulationRow) -> tuple[str, str, float]:
    return row.stop_policy_id, row.target_policy_id, row.target_close_fraction


def _execution_variant_id(stop_policy_id: str, target_policy_id: str, target_close_fraction: float) -> str:
    fraction = format(target_close_fraction, ".8g")
    return f"{stop_policy_id}::{target_policy_id}::close_fraction={fraction}"


def trade_simulation_rows_to_artifact(rows: Sequence[TradeSimulationRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        result.append(_with_core_atr_csv_alias(asdict(row)))
    return result


def _with_core_atr_csv_alias(payload: dict[str, object]) -> dict[str, object]:
    return {"ATR_1d_asof_t" if key == "core_atr_1440" else key: value for key, value in payload.items()}


def trade_simulation_metric_rows_to_artifact(rows: Sequence[TradeSimulationMetricRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def load_anomaly_trade_simulation_csv(path: str | Path) -> tuple[TradeSimulationRow, ...]:
    return tuple(
        _load_simulation_artifact(
            path=path,
            schema_name="anomaly_trade_simulation.csv",
            row_builder=_simulation_from_mapping,
        )
    )


def barrier_outcome_rows_to_artifact(rows: Sequence[BarrierOutcomeRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def _has_static_barrier_decision(decision: ExpectedValueRow) -> bool:
    if decision.best_action not in {"long", "short"}:
        return False
    if not decision.execution_policy_resolved:
        return False
    return (
        decision.stop_reference_price is not None
        and decision.target_reference_price is not None
        and decision.stop_distance is not None
        and decision.target_distance is not None
    )


@dataclass(frozen=True, slots=True)
class _StaticBarrierOutcome:
    target_hit: bool
    stop_hit: bool
    timeout: bool
    first_resolution: str
    first_hit_candle: Candle1m | None
    target_hit_candle: Candle1m | None
    stop_hit_candle: Candle1m | None
    intracandle_collision: bool
    exit_price_before_costs: float


def _realized_barrier_outcome(
    *,
    decision: ExpectedValueRow,
    candles: Sequence[Candle1m],
    config: TradeSimulationConfig,
) -> BarrierOutcomeRow:
    if decision.execution_reference_model != config.execution_reference_model:
        raise TradeSimulationInputError(
            f"decision execution_reference_model={decision.execution_reference_model!r} does not match barrier outcome config {config.execution_reference_model!r}"
        )
    if not _has_static_barrier_decision(decision):
        raise TradeSimulationInputError(f"missing static barrier inputs for decision {decision.event_id}")
    entry_candle = _entry_candle(decision=decision, candles=candles)
    exit_candles = [
        candle
        for candle in candles
        if candle.available_time_ms >= entry_candle.available_time_ms
        and candle.available_time_ms <= decision.snapshot_time_ms + decision.target_horizon_minutes * ONE_MINUTE_MS
    ]
    if not exit_candles:
        raise TradeSimulationInputError(f"no barrier outcome candles for decision {decision.event_id}")
    assert decision.stop_reference_price is not None
    assert decision.target_reference_price is not None
    assert decision.stop_distance is not None
    assert decision.target_distance is not None
    side = decision.best_action
    outcome = _resolve_static_barrier_outcome(
        side=side,
        candles=exit_candles,
        target_price=decision.target_reference_price,
        stop_price=decision.stop_reference_price,
        stop_trigger=BarrierTrigger(decision.stop_trigger),
        target_trigger=BarrierTrigger(decision.target_trigger),
    )
    return BarrierOutcomeRow(
        barrier_outcome_version=BARRIER_OUTCOME_VERSION,
        strategy_name=decision.strategy_name,
        strategy_version=decision.strategy_version,
        event_id=decision.event_id,
        symbol=decision.symbol,
        snapshot_time_ms=decision.snapshot_time_ms,
        feature_cutoff_time_ms=decision.feature_cutoff_time_ms,
        target_horizon_minutes=decision.target_horizon_minutes,
        execution_reference_model=config.execution_reference_model,
        entry_price_basis=config.entry_price_basis,
        side=side,
        entry_reference_time_ms=entry_candle.open_time_ms,
        entry_reference_price=entry_candle.open,
        stop_reference_price=decision.stop_reference_price,
        target_reference_price=decision.target_reference_price,
        stop_distance=decision.stop_distance,
        target_distance=decision.target_distance,
        stop_policy_id=decision.stop_policy_id,
        target_policy_id=decision.target_policy_id,
        stop_trigger=decision.stop_trigger,
        target_trigger=decision.target_trigger,
        target_hit=outcome.target_hit,
        stop_hit=outcome.stop_hit,
        timeout=outcome.timeout,
        first_resolution=outcome.first_resolution,
        first_hit_time_ms=None if outcome.first_hit_candle is None else outcome.first_hit_candle.open_time_ms,
        target_hit_time_ms=None if outcome.target_hit_candle is None else outcome.target_hit_candle.open_time_ms,
        stop_hit_time_ms=None if outcome.stop_hit_candle is None else outcome.stop_hit_candle.open_time_ms,
        intracandle_collision=outcome.intracandle_collision,
        barrier_resolution=outcome.first_resolution,
        net_pnl_before_model=_side_pnl(side, entry_candle.open, outcome.exit_price_before_costs),
        temporal_contract=BARRIER_OUTCOME_TEMPORAL_CONTRACT,
    )


def _resolve_static_barrier_outcome(
    *,
    side: str,
    candles: Sequence[Candle1m],
    target_price: float,
    stop_price: float,
    stop_trigger: BarrierTrigger,
    target_trigger: BarrierTrigger,
) -> _StaticBarrierOutcome:
    for candle in candles:
        target_hit = _barrier_hit(side=side, candle=candle, price=target_price, trigger=target_trigger, is_target=True)
        stop_hit = _barrier_hit(side=side, candle=candle, price=stop_price, trigger=stop_trigger, is_target=False)
        if target_hit and stop_hit:
            return _StaticBarrierOutcome(
                target_hit=True,
                stop_hit=True,
                timeout=False,
                first_resolution="stop_loss_first",
                first_hit_candle=candle,
                target_hit_candle=candle,
                stop_hit_candle=candle,
                intracandle_collision=True,
                exit_price_before_costs=stop_price if stop_trigger is BarrierTrigger.TOUCH else candle.close,
            )
        if stop_hit:
            return _StaticBarrierOutcome(
                target_hit=False,
                stop_hit=True,
                timeout=False,
                first_resolution="stop_loss_first",
                first_hit_candle=candle,
                target_hit_candle=None,
                stop_hit_candle=candle,
                intracandle_collision=False,
                exit_price_before_costs=stop_price if stop_trigger is BarrierTrigger.TOUCH else candle.close,
            )
        if target_hit:
            return _StaticBarrierOutcome(
                target_hit=True,
                stop_hit=False,
                timeout=False,
                first_resolution="target_first",
                first_hit_candle=candle,
                target_hit_candle=candle,
                stop_hit_candle=None,
                intracandle_collision=False,
                exit_price_before_costs=target_price,
            )
    return _StaticBarrierOutcome(
        target_hit=False,
        stop_hit=False,
        timeout=True,
        first_resolution="horizon_close",
        first_hit_candle=None,
        target_hit_candle=None,
        stop_hit_candle=None,
        intracandle_collision=False,
        exit_price_before_costs=candles[-1].close,
    )


def _is_simulatable_decision(decision: ExpectedValueRow, *, config: TradeSimulationConfig) -> bool:
    if decision.best_action not in {"long", "short"}:
        return False
    if config.require_prediction_confident and not decision.is_prediction_confident:
        return False
    if config.require_rr_acceptable and not _selected_side_rr_acceptable(decision):
        return False
    return True


def _selected_side_rr_acceptable(decision: ExpectedValueRow) -> bool:
    if decision.best_action == "long":
        return decision.RR_long_acceptable
    if decision.best_action == "short":
        return decision.RR_short_acceptable
    return False


def _validate_strategy_horizon(*, config: TradeSimulationConfig) -> None:
    strategy = get_strategy(config.strategy_name)
    if strategy.metadata.horizon_minutes != config.target_horizon_minutes:
        raise TradeSimulationInputError(
            f"simulation target_horizon_minutes={config.target_horizon_minutes} "
            f"does not match strategy horizon {strategy.metadata.horizon_minutes}"
        )


def _simulate_decision(
    *,
    decision: ExpectedValueRow,
    candles: Sequence[Candle1m],
    funding_rates: Sequence[FundingRate],
    config: TradeSimulationConfig,
    target_close_fraction: float,
) -> TradeSimulationRow:
    if decision.execution_reference_model != config.execution_reference_model:
        raise TradeSimulationInputError(
            f"decision execution_reference_model={decision.execution_reference_model!r} does not match simulation config {config.execution_reference_model!r}"
        )
    if decision.cost_model != config.cost_model:
        raise TradeSimulationInputError(f"decision cost_model={decision.cost_model!r} does not match simulation config {config.cost_model!r}")
    if not decision.execution_policy_resolved:
        raise TradeSimulationInputError(f"unresolved structural execution policy for decision {decision.event_id}")
    if decision.stop_reference_price is None or decision.target_reference_price is None:
        raise TradeSimulationInputError(f"missing structural execution price for decision {decision.event_id}")
    if decision.stop_distance is None or decision.target_distance is None:
        raise TradeSimulationInputError(f"missing structural execution distance for decision {decision.event_id}")
    entry_candle = _entry_candle(decision=decision, candles=candles)
    exit_candles = [
        candle
        for candle in candles
        if candle.available_time_ms >= entry_candle.available_time_ms
        and candle.available_time_ms <= decision.snapshot_time_ms + decision.target_horizon_minutes * ONE_MINUTE_MS
    ]
    if not exit_candles:
        raise TradeSimulationInputError(f"no simulation candles for decision {decision.event_id}")
    side = decision.best_action
    toxic_entry_penalty = _toxic_entry_penalty(decision=decision, candles=candles, config=config)
    entry_price = _entry_price(
        side=side,
        entry_open=entry_candle.open,
        slippage_bps=decision.slippage_bps,
        toxic_entry_penalty=toxic_entry_penalty,
    )
    target_price = decision.target_reference_price
    stop_price = decision.stop_reference_price
    strategy = get_strategy(decision.strategy_name)
    stop_policy = strategy.execution_policies.stop_for_side(PositionSide(side))
    outcome = _resolve_structural_exit(
        side=side,
        candles=exit_candles,
        target_price=target_price,
        initial_stop_price=stop_price,
        stop_trigger=BarrierTrigger(decision.stop_trigger),
        target_trigger=BarrierTrigger(decision.target_trigger),
        target_close_fraction=target_close_fraction,
        trailing_anchor=stop_policy.trailing_anchor,
        swing_confirmation_bars=stop_policy.swing_confirmation_bars,
    )
    final_exit_price = _exit_price(
        side=side,
        exit_reason=outcome.final_exit_reason,
        candle=outcome.exit_candle,
        target_price=target_price,
        stop_price=outcome.final_stop_price,
        slippage_bps=decision.slippage_bps,
        stop_trigger=BarrierTrigger(decision.stop_trigger),
    )
    target_fill_price = (
        _exit_price(
            side=side,
            exit_reason="target_hit",
            candle=outcome.target_hit_candle,
            target_price=target_price,
            stop_price=outcome.final_stop_price,
            slippage_bps=decision.slippage_bps,
            stop_trigger=BarrierTrigger(decision.stop_trigger),
        )
        if outcome.target_hit_candle is not None
        else None
    )
    realized_fraction = target_close_fraction if target_fill_price is not None else 0.0
    remaining_fraction = 1.0 - realized_fraction
    target_pnl = 0.0 if target_fill_price is None else _side_pnl(side, entry_price, target_fill_price) * realized_fraction
    final_pnl = _side_pnl(side, entry_price, final_exit_price) * remaining_fraction
    gross_pnl = target_pnl + final_pnl
    fee_rate = decision.fee_bps / 10_000.0
    total_cost = entry_price * fee_rate
    if target_fill_price is not None:
        total_cost += target_fill_price * realized_fraction * fee_rate
    total_cost += final_exit_price * remaining_fraction * fee_rate
    full_holding_funding = _funding_cost(
        side=side,
        entry_price=entry_price,
        entry_time_ms=entry_candle.open_time_ms,
        exit_time_ms=outcome.exit_candle.open_time_ms,
        funding_rates=funding_rates,
    )
    funding_cost = full_holding_funding
    if outcome.target_hit_candle is not None and realized_fraction > 0.0:
        post_target_funding = _funding_cost(
            side=side,
            entry_price=entry_price,
            entry_time_ms=outcome.target_hit_candle.open_time_ms,
            exit_time_ms=outcome.exit_candle.open_time_ms,
            funding_rates=funding_rates,
        )
        funding_cost = full_holding_funding - realized_fraction * post_target_funding
    net_pnl = gross_pnl - total_cost - funding_cost
    weighted_exit_price = (
        (target_fill_price or 0.0) * realized_fraction
        + final_exit_price * remaining_fraction
    )
    exit_reason = outcome.final_exit_reason
    if outcome.target_hit_candle is not None and target_close_fraction < 1.0:
        exit_reason = "partial_target_then_stop" if outcome.final_exit_reason == "stop_loss" else "partial_target_then_horizon"
    return TradeSimulationRow(
        simulation_version=config.simulation_version,
        strategy_name=decision.strategy_name,
        strategy_version=decision.strategy_version,
        event_id=decision.event_id,
        symbol=decision.symbol,
        snapshot_time_ms=decision.snapshot_time_ms,
        feature_cutoff_time_ms=decision.feature_cutoff_time_ms,
        target_horizon_minutes=decision.target_horizon_minutes,
        execution_reference_model=config.execution_reference_model,
        entry_price_basis=config.entry_price_basis,
        decision_action=decision.best_action,
        simulated_side=side,
        entry_reference_time_ms=entry_candle.open_time_ms,
        entry_reference_open=entry_candle.open,
        entry_price=entry_price,
        core_atr_1440=decision.core_atr_1440,
        execution_policy_version=decision.execution_policy_version,
        stop_policy_id=decision.stop_policy_id,
        target_policy_id=decision.target_policy_id,
        stop_anchor=decision.stop_anchor,
        target_anchor=decision.target_anchor,
        stop_trigger=decision.stop_trigger,
        target_trigger=decision.target_trigger,
        target_close_fraction=target_close_fraction,
        stop_distance=decision.stop_distance,
        target_distance=decision.target_distance,
        stop_price=stop_price,
        final_stop_price=outcome.final_stop_price,
        target_price=target_price,
        target_was_hit=outcome.target_hit_candle is not None,
        target_hit_time_ms=(
            None if outcome.target_hit_candle is None else outcome.target_hit_candle.open_time_ms
        ),
        fee_bps=decision.fee_bps,
        slippage_bps=decision.slippage_bps,
        cost_model=config.cost_model,
        total_cost=total_cost,
        funding_cost=funding_cost,
        exit_time_ms=outcome.exit_candle.open_time_ms,
        exit_price=weighted_exit_price,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        net_pnl_r=net_pnl / decision.stop_distance,
        net_return=net_pnl / entry_price,
        barrier_resolution=outcome.barrier_resolution,
        temporal_contract=TRADE_SIMULATION_TEMPORAL_CONTRACT,
    )


def _entry_candle(*, decision: ExpectedValueRow, candles: Sequence[Candle1m]) -> Candle1m:
    for candle in candles:
        if candle.open_time_ms > decision.snapshot_time_ms and candle.available_time_ms > decision.snapshot_time_ms:
            return candle
    raise TradeSimulationInputError(f"next 1m open is missing for decision {decision.event_id}")


def _funding_cost(
    *,
    side: str,
    entry_price: float,
    entry_time_ms: int,
    exit_time_ms: int,
    funding_rates: Sequence[FundingRate],
) -> float:
    rate_sum = sum(
        item.funding_rate
        for item in funding_rates
        if entry_time_ms <= item.timestamp_ms <= exit_time_ms
    )
    signed_long_cost = entry_price * rate_sum
    return signed_long_cost if side == "long" else -signed_long_cost


def _entry_price(*, side: str, entry_open: float, slippage_bps: float, toxic_entry_penalty: float) -> float:
    penalty = slippage_bps / 10_000.0
    if side == "long":
        return entry_open * (1.0 + penalty) + toxic_entry_penalty
    return entry_open * (1.0 - penalty) - toxic_entry_penalty


def _toxic_entry_penalty(*, decision: ExpectedValueRow, candles: Sequence[Candle1m], config: TradeSimulationConfig) -> float:
    if config.toxic_entry_atr_1m_fraction == 0.0:
        return 0.0
    asof_candles = [candle for candle in candles if candle.available_time_ms <= decision.snapshot_time_ms]
    if not asof_candles:
        return 0.0
    last_closed = max(asof_candles, key=lambda item: item.available_time_ms)
    atr_1m_proxy = max(last_closed.high - last_closed.low, 0.0)
    return atr_1m_proxy * config.toxic_entry_atr_1m_fraction


@dataclass(frozen=True, slots=True)
class _StructuralExitOutcome:
    exit_candle: Candle1m
    final_exit_reason: str
    barrier_resolution: str
    final_stop_price: float
    target_hit_candle: Candle1m | None


def _resolve_structural_exit(
    *,
    side: str,
    candles: Sequence[Candle1m],
    target_price: float,
    initial_stop_price: float,
    stop_trigger: BarrierTrigger,
    target_trigger: BarrierTrigger,
    target_close_fraction: float,
    trailing_anchor: StructuralAnchor | None,
    swing_confirmation_bars: int,
) -> _StructuralExitOutcome:
    stop_price = initial_stop_price
    target_hit_candle: Candle1m | None = None
    for index, candle in enumerate(candles):
        target_hit = target_hit_candle is None and _barrier_hit(
            side=side, candle=candle, price=target_price, trigger=target_trigger, is_target=True
        )
        stop_hit = _barrier_hit(
            side=side, candle=candle, price=stop_price, trigger=stop_trigger, is_target=False
        )
        if target_hit and stop_hit:
            return _StructuralExitOutcome(candle, "stop_loss", "stop_loss_first", stop_price, target_hit_candle)
        if stop_hit:
            resolution = "partial_then_single_barrier" if target_hit_candle is not None else "single_barrier"
            return _StructuralExitOutcome(candle, "stop_loss", resolution, stop_price, target_hit_candle)
        if target_hit:
            target_hit_candle = candle
            if target_close_fraction >= 1.0:
                return _StructuralExitOutcome(candle, "target_hit", "single_barrier", stop_price, candle)
        trailing_level = _confirmed_trailing_level(
            side=side,
            candles=candles,
            confirmation_index=index,
            confirmation_bars=swing_confirmation_bars,
            trailing_anchor=trailing_anchor,
        )
        if trailing_level is not None:
            if side == "short" and candle.close < trailing_level < stop_price:
                stop_price = trailing_level
            elif side == "long" and stop_price < trailing_level < candle.close:
                stop_price = trailing_level
    resolution = "partial_then_horizon_close" if target_hit_candle is not None else "horizon_close"
    return _StructuralExitOutcome(candles[-1], "horizon_close", resolution, stop_price, target_hit_candle)


def _barrier_hit(*, side: str, candle: Candle1m, price: float, trigger: BarrierTrigger, is_target: bool) -> bool:
    if trigger is BarrierTrigger.CLOSE_BEYOND:
        if side == "long":
            return candle.close >= price if is_target else candle.close <= price
        return candle.close <= price if is_target else candle.close >= price
    if side == "long":
        return candle.high >= price if is_target else candle.low <= price
    return candle.low <= price if is_target else candle.high >= price


def _confirmed_trailing_level(
    *,
    side: str,
    candles: Sequence[Candle1m],
    confirmation_index: int,
    confirmation_bars: int,
    trailing_anchor: StructuralAnchor | None,
) -> float | None:
    if trailing_anchor is None or confirmation_index < 2 * confirmation_bars:
        return None
    pivot_index = confirmation_index - confirmation_bars
    start = pivot_index - confirmation_bars
    stop = confirmation_index + 1
    window = candles[start:stop]
    pivot = candles[pivot_index]
    if side == "short" and trailing_anchor is StructuralAnchor.CONFIRMED_SWING_HIGH:
        return pivot.high if pivot.high == max(item.high for item in window) else None
    if side == "long" and trailing_anchor is StructuralAnchor.CONFIRMED_SWING_LOW:
        return pivot.low if pivot.low == min(item.low for item in window) else None
    return None


def _side_pnl(side: str, entry_price: float, exit_price: float) -> float:
    return exit_price - entry_price if side == "long" else entry_price - exit_price


def _target_close_fraction_grid(*, strategy, decision: ExpectedValueRow) -> tuple[float, ...]:
    policies = tuple(
        policy
        for policy in strategy.execution_policies.take_profit_policies
        if policy.policy_id == decision.target_policy_id and policy.side.value == decision.best_action
    )
    if len(policies) != 1:
        raise TradeSimulationInputError(
            f"decision target policy is not declared by strategy: {decision.target_policy_id!r}"
        )
    return policies[0].close_fraction_grid


def _exit_price(
    *,
    side: str,
    exit_reason: str,
    candle: Candle1m,
    target_price: float,
    stop_price: float,
    slippage_bps: float,
    stop_trigger: BarrierTrigger = BarrierTrigger.TOUCH,
) -> float:
    penalty = slippage_bps / 10_000.0
    if exit_reason == "target_hit":
        raw = target_price
    elif exit_reason == "stop_loss":
        raw = candle.close if stop_trigger is BarrierTrigger.CLOSE_BEYOND else stop_price
    else:
        raw = candle.close
    if side == "long":
        return raw * (1.0 - penalty)
    return raw * (1.0 + penalty)


def _load_simulation_artifact(*, path: str | Path, schema_name: str, row_builder: object) -> tuple[object, ...]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise TradeSimulationArtifactError(f"simulation artifact is missing: {artifact_path}")
    schema = get_artifact_schema(schema_name)
    expected_columns = list(schema.required_columns)
    rows: list[object] = []
    with artifact_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise TradeSimulationArtifactError(f"{schema_name} columns must match {expected_columns}, got {actual_columns}")
        for row_index, row in enumerate(reader):
            try:
                rows.append(row_builder(row))  # type: ignore[operator]
            except (TypeError, ValueError, MarketDataContractError) as exc:
                raise TradeSimulationArtifactError(f"invalid {schema_name} row {row_index}: {exc}") from exc
    return tuple(rows)


def _simulation_from_mapping(row: Mapping[str, object]) -> TradeSimulationRow:
    return TradeSimulationRow(
        simulation_version=_required_str(row, "simulation_version"),
        strategy_name=_required_str(row, "strategy_name"),
        strategy_version=_required_str(row, "strategy_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        execution_reference_model=_required_str(row, "execution_reference_model"),
        entry_price_basis=_required_str(row, "entry_price_basis"),
        decision_action=_required_str(row, "decision_action"),
        simulated_side=_required_str(row, "simulated_side"),
        entry_reference_time_ms=_required_int(row, "entry_reference_time_ms"),
        entry_reference_open=_required_float(row, "entry_reference_open"),
        entry_price=_required_float(row, "entry_price"),
        core_atr_1440=_required_float(row, "ATR_1d_asof_t"),
        execution_policy_version=_required_str(row, "execution_policy_version"),
        stop_policy_id=_required_str(row, "stop_policy_id"),
        target_policy_id=_required_str(row, "target_policy_id"),
        stop_anchor=_required_str(row, "stop_anchor"),
        target_anchor=_required_str(row, "target_anchor"),
        stop_trigger=_required_str(row, "stop_trigger"),
        target_trigger=_required_str(row, "target_trigger"),
        target_close_fraction=_required_float(row, "target_close_fraction"),
        stop_distance=_required_float(row, "stop_distance"),
        target_distance=_required_float(row, "target_distance"),
        stop_price=_required_float(row, "stop_price"),
        final_stop_price=_required_float(row, "final_stop_price"),
        target_price=_required_float(row, "target_price"),
        target_was_hit=_required_bool(row, "target_was_hit"),
        target_hit_time_ms=_optional_int(row, "target_hit_time_ms"),
        fee_bps=_required_float(row, "fee_bps"),
        slippage_bps=_required_float(row, "slippage_bps"),
        cost_model=_required_str(row, "cost_model"),
        total_cost=_required_float(row, "total_cost"),
        funding_cost=_required_float(row, "funding_cost"),
        exit_time_ms=_required_int(row, "exit_time_ms"),
        exit_price=_required_float(row, "exit_price"),
        exit_reason=_required_str(row, "exit_reason"),
        gross_pnl=_required_float(row, "gross_pnl"),
        net_pnl=_required_float(row, "net_pnl"),
        net_pnl_r=_required_float(row, "net_pnl_r"),
        net_return=_required_float(row, "net_return"),
        barrier_resolution=_required_str(row, "barrier_resolution"),
        temporal_contract=_required_str(row, "temporal_contract"),
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return float(sum(items)) / float(len(items))


def _metric_value(value: str | float | int) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return float(value)


def _optional_int(row: Mapping[str, object], name: str) -> int | None:
    value = row[name]
    return None if _is_missing(value) else int(value)


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _is_missing(value: object) -> bool:
    if value is None or value == "":
        return True
    return isinstance(value, float) and math.isnan(value)
