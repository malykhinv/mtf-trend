"""Explicit point-in-time views over stored event-scoped enrichment data."""

from __future__ import annotations

import pandas as pd

from anomaly_science.contracts.enrichment import (
    EventEnrichmentContractError,
    EventScopedEnrichmentRequest,
)
from anomaly_science.contracts.time import validate_timestamp_ms


def causal_event_enrichment_view(
    frame: pd.DataFrame,
    *,
    request: EventScopedEnrichmentRequest,
    decision_snapshot_time_ms: int,
    event_time_column: str,
    available_time_column: str,
    symbol_column: str = "symbol",
) -> pd.DataFrame:
    """Return the exact high-resolution prefix available at a decision snapshot.

    The stored request may contain a post-event tail for later decisions.  This
    function is the mandatory as-of boundary: early decisions cannot observe it.
    """

    validate_timestamp_ms(decision_snapshot_time_ms, field_name="decision_snapshot_time_ms")
    if decision_snapshot_time_ms < request.selection.selection_snapshot_time_ms:
        raise EventEnrichmentContractError(
            "enrichment cannot be consumed before the coarse event selection snapshot"
        )
    required = {symbol_column, event_time_column, available_time_column}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise EventEnrichmentContractError(
            f"event enrichment frame is missing required columns: {missing}"
        )

    event_time = pd.to_numeric(frame[event_time_column], errors="raise")
    available_time = pd.to_numeric(frame[available_time_column], errors="raise")
    symbol = frame[symbol_column].astype(str)
    mask = (
        symbol.eq(request.selection.symbol)
        & event_time.ge(request.requested_start_time_ms)
        & event_time.lt(request.requested_end_time_ms_exclusive)
        & event_time.le(decision_snapshot_time_ms)
        & available_time.le(decision_snapshot_time_ms)
    )
    return frame.loc[mask].copy().sort_values(
        [event_time_column, available_time_column],
        kind="mergesort",
    ).reset_index(drop=True)


__all__ = ["causal_event_enrichment_view"]
