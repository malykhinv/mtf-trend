"""Causal coarse local-flow, OI, and liquidation features at selected events."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from anomaly_science.strategy.residual_absorption.data import read_is_event_window_minutes
from anomaly_science.strategy.residual_absorption.spec import RESIDUAL_ABSORPTION_RESEARCH_SPLIT

LOCAL_ACTIVITY_SCHEMA_VERSION = "residual_absorption_local_activity_v1"
WINDOWS_MINUTES = (5, 15, 30)
MIN_ABSOLUTE_RETURN = 1e-4
MIN_ABSOLUTE_SIGNED_QUOTE = 1_000.0

RAW_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "trade_count",
    "taker_buy_quote_volume",
    "open_interest",
    "long_liquidations_vol",
    "short_liquidations_vol",
    "oi_available",
    "missing_oi_flag",
    "liquidation_available",
    "missing_liquidation_flag",
)


def compute_local_window_features(
    minutes: pd.DataFrame,
    *,
    snapshot_time_ms: int,
    window_minutes: int,
) -> dict[str, object]:
    """Compute one registered window from bars closed no later than snapshot."""

    start = snapshot_time_ms - window_minutes * 60_000
    window = minutes.loc[
        minutes["timestamp"].ge(start) & minutes["timestamp"].lt(snapshot_time_ms)
    ].sort_values("timestamp", kind="mergesort")
    prefix = f"local_{window_minutes}m_"
    coverage = len(window) / window_minutes
    output: dict[str, object] = {
        f"{prefix}minute_count": len(window),
        f"{prefix}coverage": coverage,
        f"{prefix}missing": len(window) != window_minutes,
    }
    if window.empty:
        return output
    first_open = float(window.iloc[0]["open"])
    last_close = float(window.iloc[-1]["close"])
    log_return = math.log(last_close / first_open) if first_open > 0 and last_close > 0 else np.nan
    quote = float(window["quote_volume"].sum())
    trades = float(window["trade_count"].sum())
    taker_buy = float(window["taker_buy_quote_volume"].sum())
    signed_quote = 2.0 * taker_buy - quote
    prices = np.concatenate(([first_open], window["close"].to_numpy(dtype=float)))
    log_steps = np.diff(np.log(prices)) if np.all(prices > 0.0) else np.asarray([np.nan])
    path_length = float(np.nansum(np.abs(log_steps)))
    oi_mask = window["oi_available"].astype(bool) & window["open_interest"].notna()
    oi_values = window.loc[oi_mask, "open_interest"].to_numpy(dtype=float)
    oi_change = float(oi_values[-1] - oi_values[0]) if len(oi_values) >= 2 else np.nan
    oi_relative = (
        oi_change / float(oi_values[0])
        if len(oi_values) >= 2 and float(oi_values[0]) != 0.0
        else np.nan
    )
    long_liq = float(window["long_liquidations_vol"].fillna(0.0).sum())
    short_liq = float(window["short_liquidations_vol"].fillna(0.0).sum())
    liquidation_total = long_liq + short_liq
    output.update(
        {
            f"{prefix}log_return": log_return,
            f"{prefix}quote_volume": quote,
            f"{prefix}trade_count": trades,
            f"{prefix}signed_taker_quote_volume": signed_quote,
            f"{prefix}taker_imbalance": signed_quote / quote if quote > 0.0 else np.nan,
            f"{prefix}high_low_range": (
                float(window["high"].max() / window["low"].min() - 1.0)
                if float(window["low"].min()) > 0.0
                else np.nan
            ),
            f"{prefix}realized_volatility": float(np.sqrt(np.nansum(log_steps**2))),
            f"{prefix}path_efficiency": (
                abs(log_return) / path_length
                if path_length > 0.0 and np.isfinite(log_return)
                else np.nan
            ),
            f"{prefix}return_per_million_quote": (
                log_return / (quote / 1_000_000.0)
                if quote > 0.0 and np.isfinite(log_return)
                else np.nan
            ),
            f"{prefix}oi_start": float(oi_values[0]) if len(oi_values) else np.nan,
            f"{prefix}oi_end": float(oi_values[-1]) if len(oi_values) else np.nan,
            f"{prefix}oi_change": oi_change,
            f"{prefix}oi_relative_change": oi_relative,
            f"{prefix}oi_coverage": float(oi_mask.mean()),
            f"{prefix}oi_missing_flag_rate": float(window["missing_oi_flag"].astype(bool).mean()),
            f"{prefix}long_liquidation_volume": long_liq,
            f"{prefix}short_liquidation_volume": short_liq,
            f"{prefix}liquidation_imbalance": (
                (short_liq - long_liq) / liquidation_total
                if liquidation_total > 0.0
                else np.nan
            ),
            f"{prefix}liquidation_observed": liquidation_total > 0.0,
            f"{prefix}liquidation_coverage": float(
                window["liquidation_available"].astype(bool).mean()
            ),
            f"{prefix}liquidation_missing_flag_rate": float(
                window["missing_liquidation_flag"].astype(bool).mean()
            ),
        }
    )
    return output


def build_stage2_local_features(
    *,
    stage1_dir: str | Path,
    source_dir: str | Path,
    progress: Callable[[str], None] | None = print,
) -> Path:
    root = Path(stage1_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    rows: list[dict[str, object]] = []
    groups = tuple(profiles.groupby("symbol", sort=True))
    for completed, (symbol, group) in enumerate(groups, start=1):
        source = Path(source_dir) / f"{symbol}.parquet"
        if not source.is_file():
            continue
        minutes = read_is_event_window_minutes(
            source,
            snapshot_times_ms=group["snapshot_time_ms"],
            window_minutes=max(WINDOWS_MINUTES),
            columns=RAW_COLUMNS,
        )
        for raw in group.itertuples(index=False):
            snapshot = int(raw.snapshot_time_ms)
            RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(snapshot)
            row: dict[str, object] = {
                "schema_version": LOCAL_ACTIVITY_SCHEMA_VERSION,
                "event_id": str(raw.event_id),
                "symbol": str(symbol),
                "snapshot_time_ms": snapshot,
                "feature_cutoff_time_ms": snapshot,
                "impulse_direction": int(raw.impulse_direction),
            }
            for window_minutes in WINDOWS_MINUTES:
                row.update(
                    compute_local_window_features(
                        minutes,
                        snapshot_time_ms=snapshot,
                        window_minutes=window_minutes,
                    )
                )
            signed_flow = float(row.get("local_15m_signed_taker_quote_volume", np.nan))
            price_return = float(row.get("local_15m_log_return", np.nan))
            oi_change = float(row.get("local_15m_oi_change", np.nan))
            flow_valid = np.isfinite(signed_flow) and abs(signed_flow) >= MIN_ABSOLUTE_SIGNED_QUOTE
            return_valid = np.isfinite(price_return) and abs(price_return) >= MIN_ABSOLUTE_RETURN
            row.update(
                {
                    "local_15m_price_flow_alignment": price_return * signed_flow,
                    "local_15m_direction_adjusted_taker_imbalance": int(raw.impulse_direction)
                    * float(row.get("local_15m_taker_imbalance", np.nan)),
                    "local_15m_direction_adjusted_oi_change": int(raw.impulse_direction) * oi_change,
                    "local_15m_oi_change_per_signed_million": (
                        oi_change / (signed_flow / 1_000_000.0) if flow_valid else np.nan
                    ),
                    "local_15m_return_per_signed_million": (
                        price_return / (signed_flow / 1_000_000.0) if flow_valid else np.nan
                    ),
                    "local_15m_aggressive_flow_per_return_floor": (
                        abs(signed_flow) / max(abs(price_return), MIN_ABSOLUTE_RETURN)
                        if np.isfinite(signed_flow) and np.isfinite(price_return)
                        else np.nan
                    ),
                    "local_15m_signed_flow_denominator_valid": bool(flow_valid),
                    "local_15m_return_denominator_valid": bool(return_valid),
                }
            )
            rows.append(row)
        if progress and (completed % 50 == 0 or completed == len(groups)):
            progress(f"stage2 local activity {completed}/{len(groups)}")
    output = root / "stage2_local_activity_features_is.parquet"
    frame = pd.DataFrame(rows).sort_values(["snapshot_time_ms", "symbol"])
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise ValueError("stage2 local feature cutoff exceeds snapshot")
    frame.to_parquet(output, index=False, compression="zstd")
    audit = {
        "audit_version": "stage2_local_activity_audit_v1",
        "status": "PASS" if len(frame) == len(profiles) else "FAIL",
        "feature_row_count": len(frame),
        "profile_row_count": len(profiles),
        "key_unique": not bool(frame.duplicated(["event_id", "symbol"]).any()),
        "oos_rows": int((frame["snapshot_time_ms"] >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms).sum()),
    }
    (root / "stage2_local_activity_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    if audit["status"] != "PASS" or not audit["key_unique"] or audit["oos_rows"]:
        raise ValueError("stage2 local activity audit failed")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal coarse stage-2 local features.")
    parser.add_argument("--stage1-dir", type=Path, default=Path(".output/research/residual_absorption/stage1_is_v1"))
    parser.add_argument("--source", type=Path, default=Path(".output/market/binance_vision/um_futures/enriched_1m"))
    args = parser.parse_args()
    print(build_stage2_local_features(stage1_dir=args.stage1_dir, source_dir=args.source))


__all__ = [
    "LOCAL_ACTIVITY_SCHEMA_VERSION",
    "build_stage2_local_features",
    "compute_local_window_features",
]


if __name__ == "__main__":
    main()
