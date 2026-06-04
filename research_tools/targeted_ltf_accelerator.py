"""Targeted subminute LTF cache accelerator for runner discovery.

This module owns data-loading acceleration only. It does not decide signals,
does not change trading thresholds, and only materializes data that belongs to
the requested closed timestamp windows.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from research_tools.anomaly_strategy_backtest import (
    DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
    _cache_data_path,
    _emit_progress_1pct,
    _fetch_binance_futures_aggtrades_rows,
    _merge_targeted_timestamp_windows,
    _overlapping_intervals,
    _read_entry_cache_metadata_with_delta,
    _timeframe_to_milliseconds,
    _timestamp_to_utc,
    _trusted_materialized_entry_cache_missing_intervals,
    _write_direct_aggtrade_target_ltf_delta,
)

ACCELERATOR_MODEL = "targeted_ltf_accelerator_v1"
ACCELERATOR_SOURCE_PRIORITY = "trusted_target_ltf_cache_then_binance_futures_aggTrades_rest"
ACCELERATOR_DATA_SOURCE = "binance_futures_aggTrades_direct_to_target_ltf"


def ensure_targeted_ltf_accelerated_cache(
    *,
    cache_dir: Path,
    windows_by_symbol: Mapping[str, Iterable[tuple[int, int]]],
    target_timeframes: Iterable[str],
    progress_label: str | None = None,
    max_merged_span_ms: int | None = 60 * 60_000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch one aggTrade stream per missing interval and write all target LTFs.

    The accelerator first subtracts trusted target-LTF bucket coverage. Only the
    remaining bucket intervals are fetched from raw aggTrades, then the same raw
    trade frame is materialized to every requested target timeframe whose bucket
    interval overlaps that fetch. This preserves the core decision contract:
    data availability changes, not signal logic.
    """

    targets = tuple(dict.fromkeys(str(timeframe) for timeframe in target_timeframes))
    for target in targets:
        target_ms = _timeframe_to_milliseconds(target)
        if (
            target == "1s"
            or target_ms <= 0
            or target_ms >= 60_000
            or target_ms % 1000 != 0
        ):
            raise ValueError(f"target subminute timeframe must be direct aggTrade LTF, got: {target}")

    normalized_windows: dict[str, list[tuple[int, int]]] = {}
    raw_windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    for raw_symbol, raw_windows in windows_by_symbol.items():
        symbol = str(raw_symbol).strip()
        if not symbol or symbol == "__all__":
            continue
        valid_raw = sorted((int(start), int(end)) for start, end in raw_windows if int(start) <= int(end))
        if not valid_raw:
            continue
        raw_windows_by_symbol[symbol] = valid_raw
        merged = _merge_targeted_timestamp_windows(valid_raw, max_merged_span_ms=max_merged_span_ms)
        if merged:
            normalized_windows[symbol] = merged

    raw_windows_total = sum(len(windows) for windows in raw_windows_by_symbol.values())
    merged_windows_total = sum(len(windows) for windows in normalized_windows.values())
    plan_rows: list[dict[str, object]] = [
        {
            "symbol": "__all__",
            "status": "window_plan",
            "target_timeframes": ",".join(targets),
            "symbols_with_windows": int(len(normalized_windows)),
            "raw_targeted_windows": int(raw_windows_total),
            "merged_targeted_windows": int(merged_windows_total),
            "max_merged_span_ms": "unbounded" if max_merged_span_ms is None else int(max_merged_span_ms),
            "merge_policy": "direct_aggtrades_to_target_ltf_gap_and_max_span",
            "data_source": ACCELERATOR_DATA_SOURCE,
            "source_priority": ACCELERATOR_SOURCE_PRIORITY,
            "accelerator_model": ACCELERATOR_MODEL,
            "intermediate_1s_cache": False,
        }
    ]
    if not normalized_windows:
        return pd.DataFrame(plan_rows), pd.DataFrame([{"status": "not_run", "reason": "no_targeted_windows"}])

    fetch_rows: list[dict[str, object]] = []
    materialize_rows: list[dict[str, object]] = []
    total_windows = max(1, merged_windows_total)
    done_windows = 0
    started_at = time.monotonic()
    next_progress_pct = 0
    for symbol in sorted(normalized_windows):
        metadata_by_target = {
            target: _read_entry_cache_metadata_with_delta(
                cache_dir,
                symbol,
                target_timeframe=target,
                columns=[
                    "timestamp",
                    "aggregation_source_timeframe",
                    "aggregation_version",
                    "aggtrade_coverage_verified",
                ],
            )
            for target in targets
        }
        for window_start, window_end in normalized_windows[symbol]:
            missing_intervals_by_target: dict[str, list[tuple[int, int]]] = {}
            for target in targets:
                missing_intervals = _trusted_materialized_entry_cache_missing_intervals(
                    cache_dir,
                    symbol,
                    target_timeframe=target,
                    window_start_ms=int(window_start),
                    window_end_ms=int(window_end),
                    metadata=metadata_by_target.get(target),
                )
                if not missing_intervals:
                    materialize_rows.append(
                        {
                            "symbol": symbol,
                            "target_timeframe": target,
                            "source_timeframe": "aggTrades",
                            "status": "exists_covered_requested_intervals",
                            "path": str(_cache_data_path(cache_dir, symbol, target)),
                            "requested_window_start_ms": int(window_start),
                            "requested_window_end_ms": int(window_end),
                            "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                            "data_source": ACCELERATOR_DATA_SOURCE,
                            "source_priority": ACCELERATOR_SOURCE_PRIORITY,
                            "accelerator_model": ACCELERATOR_MODEL,
                        }
                    )
                else:
                    missing_intervals_by_target[target] = missing_intervals

            missing_fetch_intervals = _merge_targeted_timestamp_windows(
                (
                    interval
                    for intervals in missing_intervals_by_target.values()
                    for interval in intervals
                ),
                merge_gap_ms=0,
                max_merged_span_ms=max_merged_span_ms,
            )
            if not missing_fetch_intervals:
                fetch_rows.append(
                    {
                        "symbol": symbol,
                        "status": "target_ltf_exists_covered_requested_window",
                        "error": "",
                        "start_timestamp_ms": int(window_start),
                        "end_timestamp_ms": int(window_end),
                        "fetch_start_timestamp_ms": float("nan"),
                        "fetch_end_timestamp_ms": float("nan"),
                        "start_timestamp_utc": _timestamp_to_utc(int(window_start)),
                        "end_timestamp_utc": _timestamp_to_utc(int(window_end)),
                        "target_timeframes": ",".join(targets),
                        "aggtrade_rows_fetched": 0,
                        "cache_subtraction_model": "trusted_target_ltf_bucket_interval_subtraction",
                        "requested_window_ms": int(window_end - window_start + 1),
                        "fetched_window_ms": 0,
                        "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                        "data_source": ACCELERATOR_DATA_SOURCE,
                        "source_priority": ACCELERATOR_SOURCE_PRIORITY,
                        "accelerator_model": ACCELERATOR_MODEL,
                        "intermediate_1s_cache": False,
                    }
                )
                done_windows += 1
                if progress_label is not None and total_windows:
                    next_progress_pct = _emit_progress_1pct(
                        label=f"{progress_label}: targeted aggTrades->{','.join(targets)}",
                        done=done_windows,
                        total=total_windows,
                        started_at=started_at,
                        next_progress_pct=next_progress_pct,
                    )
                continue

            for fetch_start, fetch_end in missing_fetch_intervals:
                error = ""
                trades = pd.DataFrame()
                status = "ok"
                try:
                    trades = _fetch_binance_futures_aggtrades_rows(
                        symbol,
                        start_timestamp_ms=int(fetch_start),
                        end_timestamp_ms=int(fetch_end),
                    )
                    if trades.empty:
                        status = "empty_aggtrades"
                    for target, target_intervals in missing_intervals_by_target.items():
                        overlapping = _overlapping_intervals(
                            target_intervals,
                            (int(fetch_start), int(fetch_end)),
                        )
                        for target_start, target_end in overlapping:
                            write_status, rows_written, path = _write_direct_aggtrade_target_ltf_delta(
                                cache_dir=cache_dir,
                                symbol=symbol,
                                target_timeframe=target,
                                trades=trades,
                                start_timestamp_ms=int(target_start),
                                end_timestamp_ms=int(target_end),
                            )
                            materialize_rows.append(
                                {
                                    "symbol": symbol,
                                    "target_timeframe": target,
                                    "source_timeframe": "aggTrades",
                                    "status": write_status,
                                    "path": path,
                                    "new_rows": int(rows_written),
                                    "requested_window_start_ms": int(window_start),
                                    "requested_window_end_ms": int(window_end),
                                    "materialized_start_ms": int(target_start),
                                    "materialized_end_ms": int(target_end),
                                    "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                                    "data_source": ACCELERATOR_DATA_SOURCE,
                                    "source_priority": ACCELERATOR_SOURCE_PRIORITY,
                                    "accelerator_model": ACCELERATOR_MODEL,
                                }
                            )
                except Exception as exc:
                    status = "error"
                    error = f"{type(exc).__name__}: {exc}"
                    for target in missing_intervals_by_target:
                        materialize_rows.append(
                            {
                                "symbol": symbol,
                                "target_timeframe": target,
                                "source_timeframe": "aggTrades",
                                "status": "error",
                                "error": error,
                                "requested_window_start_ms": int(window_start),
                                "requested_window_end_ms": int(window_end),
                                "fetch_start_timestamp_ms": int(fetch_start),
                                "fetch_end_timestamp_ms": int(fetch_end),
                                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                                "data_source": ACCELERATOR_DATA_SOURCE,
                                "source_priority": ACCELERATOR_SOURCE_PRIORITY,
                                "accelerator_model": ACCELERATOR_MODEL,
                            }
                        )
                fetch_rows.append(
                    {
                        "symbol": symbol,
                        "status": status,
                        "error": error,
                        "start_timestamp_ms": int(window_start),
                        "end_timestamp_ms": int(window_end),
                        "fetch_start_timestamp_ms": int(fetch_start),
                        "fetch_end_timestamp_ms": int(fetch_end),
                        "start_timestamp_utc": _timestamp_to_utc(int(fetch_start)),
                        "end_timestamp_utc": _timestamp_to_utc(int(fetch_end)),
                        "target_timeframes": ",".join(targets),
                        "aggtrade_rows_fetched": int(len(trades)) if not trades.empty else 0,
                        "cache_subtraction_model": "trusted_target_ltf_bucket_interval_subtraction",
                        "requested_window_ms": int(window_end - window_start + 1),
                        "fetched_window_ms": int(fetch_end - fetch_start + 1),
                        "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                        "data_source": ACCELERATOR_DATA_SOURCE,
                        "source_priority": ACCELERATOR_SOURCE_PRIORITY,
                        "accelerator_model": ACCELERATOR_MODEL,
                        "intermediate_1s_cache": False,
                    }
                )
            done_windows += 1
            if progress_label is not None and total_windows:
                next_progress_pct = _emit_progress_1pct(
                    label=f"{progress_label}: targeted aggTrades->{','.join(targets)}",
                    done=done_windows,
                    total=total_windows,
                    started_at=started_at,
                    next_progress_pct=next_progress_pct,
                )

    fetch = pd.concat(
        [pd.DataFrame(plan_rows).assign(row_type="plan"), pd.DataFrame(fetch_rows).assign(row_type="fetch")],
        ignore_index=True,
        sort=False,
    )
    return fetch, pd.DataFrame(materialize_rows)
