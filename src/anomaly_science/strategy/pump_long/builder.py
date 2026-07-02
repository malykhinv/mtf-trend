"""Per-decision pump-long trade outcomes over the cached 1m market data.

Consumes the canonical supervised pump-fade decision dataset (every row is a
causally-qualified new-running-high decision) and the enriched 1m symbol
cache, and emits one outcome row per (decision, stop variant): realized entry,
structural-trailing exit, R multiple, MFE, eligibility under the registered
retrace-kill rule, and the stratification fields the EV layer needs. Pure
mechanics — no portfolio rules here (exposure limits live in the EV layer).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.execution import (
    STATUS_FILLED,
    first_retrace_kill_index,
    last_confirmed_swing_low,
    simulate_pump_long_trade,
)
from anomaly_science.strategy.pump_long.spec import (
    PUMP_LONG_EXECUTION_SPEC,
    PUMP_LONG_OUTCOME_SCHEMA_VERSION,
    STOP_VARIANT_CONFIRMED_SWING_LOW,
    STOP_VARIANT_EVENT_BASE,
    PumpLongExecutionSpec,
)

_CANDLE_INTERVAL_MS = 60_000

_DECISION_CARRY_COLUMNS: tuple[str, ...] = (
    "event_id",
    "group",
    "symbol",
    "snapshot_time_ms",
    "decision_index",
    "base_level",
    "anchor_high",
    "pump_elapsed_min",
    "recurrence_chain_id",
    "n_prior_48h",
    "last_prior_faded",
    "frac_prior_faded_48h",
    "min_since_last_prior",
    "remaining_to_base",
    "is_nature_anchor",
    "y",
    "label_available",
)

STATUS_NO_NEXT_BAR = "no_next_bar"


class PumpLongBuildError(ValueError):
    """Raised when market data cannot produce causal pump-long outcomes."""


@dataclass(frozen=True, slots=True)
class PumpLongBuildStats:
    symbol: str
    decision_rows: int
    outcome_rows: int
    ineligible_rows: int
    unfilled_rows: int


def build_pump_long_outcomes(
    *,
    decisions_path: Path,
    cache_dir: Path,
    output_path: Path,
    spec: PumpLongExecutionSpec = PUMP_LONG_EXECUTION_SPEC,
    workers: int = 4,
    limit_symbols: int | None = None,
) -> tuple[PumpLongBuildStats, ...]:
    decisions = pd.read_parquet(decisions_path, columns=list(_DECISION_CARRY_COLUMNS))
    symbols = sorted(decisions["symbol"].unique())
    if limit_symbols is not None:
        symbols = symbols[:limit_symbols]
    stats: list[PumpLongBuildStats] = []
    frames: list[pd.DataFrame] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _build_symbol_outcomes_worker,
                symbol=symbol,
                decision_records=decisions.loc[decisions["symbol"] == symbol].to_dict("records"),
                cache_path=cache_dir / f"{symbol}.parquet",
                spec=spec,
            ): symbol
            for symbol in symbols
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            symbol_stats, frame = future.result()
            stats.append(symbol_stats)
            if len(frame):
                frames.append(frame)
            if completed % 50 == 0 or completed == len(symbols):
                print(f"pump-long outcomes: {completed}/{len(symbols)} symbols", flush=True)
    combined = (
        pd.concat(frames, ignore_index=True).sort_values(
            ["symbol", "snapshot_time_ms", "stop_variant"]
        ).reset_index(drop=True)
        if frames
        else pd.DataFrame()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output_path, index=False)
    return tuple(stats)


def _build_symbol_outcomes_worker(
    *,
    symbol: str,
    decision_records: list[dict],
    cache_path: Path,
    spec: PumpLongExecutionSpec,
) -> tuple[PumpLongBuildStats, pd.DataFrame]:
    frame, _quality = _load_symbol(cache_path)
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)

    kill_cache: dict[str, int | None] = {}
    ignition_cache: dict[str, int] = {}
    rows: list[dict] = []
    ineligible = 0
    unfilled = 0
    for record in decision_records:
        event_id = str(record["event_id"])
        decision_open_ms = int(record["snapshot_time_ms"]) - _CANDLE_INTERVAL_MS
        decision_bar = int(np.searchsorted(timestamps, decision_open_ms))
        if decision_bar >= len(timestamps) or int(timestamps[decision_bar]) != decision_open_ms:
            raise PumpLongBuildError(f"{symbol}: decision bar not found for {event_id}")
        if event_id not in ignition_cache:
            ignition_open_ms = int(event_id.rsplit(":", 1)[-1])
            ignition_bar = int(np.searchsorted(timestamps, ignition_open_ms))
            if ignition_bar >= len(timestamps) or int(timestamps[ignition_bar]) != ignition_open_ms:
                raise PumpLongBuildError(f"{symbol}: ignition bar not found for {event_id}")
            ignition_cache[event_id] = ignition_bar
        ignition_bar = ignition_cache[event_id]

        base = float(record["base_level"])
        if event_id not in kill_cache:
            last_event_decision = max(
                int(item["snapshot_time_ms"]) for item in decision_records
                if str(item["event_id"]) == event_id
            )
            last_bar = int(np.searchsorted(timestamps, last_event_decision - _CANDLE_INTERVAL_MS))
            kill_cache[event_id] = first_retrace_kill_index(
                close=close, high=high,
                ignition_index=ignition_bar, end_index=last_bar,
                base=base, spec=spec,
            )
        kill_index = kill_cache[event_id]
        eligible = kill_index is None or decision_bar < kill_index
        if not eligible:
            ineligible += 1

        entry_index = decision_bar + 1
        has_next_bar = (
            entry_index < len(timestamps)
            and int(timestamps[entry_index]) == int(record["snapshot_time_ms"])
        )
        swing_stop = last_confirmed_swing_low(
            low=low, start_index=ignition_bar, decision_index=decision_bar,
            confirmation_bars=spec.swing_confirmation_bars,
        )
        for stop_variant, stop_price in (
            (STOP_VARIANT_EVENT_BASE, base),
            (STOP_VARIANT_CONFIRMED_SWING_LOW, swing_stop if swing_stop is not None else base),
        ):
            carried = {name: record[name] for name in _DECISION_CARRY_COLUMNS}
            if not has_next_bar:
                rows.append({
                    **carried,
                    "outcome_schema_version": PUMP_LONG_OUTCOME_SCHEMA_VERSION,
                    "stop_variant": stop_variant,
                    "entry_eligible": eligible,
                    "status": STATUS_NO_NEXT_BAR,
                    "exit_reason": "not_filled",
                    "entry_time_ms": pd.NA,
                    "exit_time_ms": pd.NA,
                    "entry_price": np.nan, "exit_price": np.nan,
                    "initial_stop_price": stop_price, "final_stop_price": stop_price,
                    "stop_distance_fraction": np.nan,
                    "gross_return": np.nan, "net_return": np.nan, "net_r": np.nan,
                    "mfe_return": np.nan, "holding_minutes": 0,
                })
                unfilled += 1
                continue
            result = simulate_pump_long_trade(
                open_=open_, high=high, low=low, close=close,
                entry_index=entry_index, initial_stop_price=float(stop_price), spec=spec,
            )
            if result.status != STATUS_FILLED:
                unfilled += 1
            rows.append({
                **carried,
                "outcome_schema_version": PUMP_LONG_OUTCOME_SCHEMA_VERSION,
                "stop_variant": stop_variant,
                "entry_eligible": eligible,
                "status": result.status,
                "exit_reason": result.exit_reason,
                "entry_time_ms": int(timestamps[result.entry_index]),
                "exit_time_ms": int(timestamps[result.exit_index]) + _CANDLE_INTERVAL_MS,
                "entry_price": result.entry_price,
                "exit_price": result.exit_price,
                "initial_stop_price": result.initial_stop_price,
                "final_stop_price": result.final_stop_price,
                "stop_distance_fraction": result.stop_distance_fraction,
                "gross_return": result.gross_return,
                "net_return": result.net_return,
                "net_r": result.net_r,
                "mfe_return": result.mfe_return,
                "holding_minutes": result.holding_minutes,
            })
    stats = PumpLongBuildStats(
        symbol=symbol,
        decision_rows=len(decision_records),
        outcome_rows=len(rows),
        ineligible_rows=ineligible,
        unfilled_rows=unfilled,
    )
    return stats, pd.DataFrame(rows)


__all__ = [
    "PumpLongBuildError",
    "PumpLongBuildStats",
    "STATUS_NO_NEXT_BAR",
    "build_pump_long_outcomes",
]
