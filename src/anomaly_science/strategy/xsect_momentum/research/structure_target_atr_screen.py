"""Grid D: structure-priced analogue of the diagnostic ATR take profit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_structural import (
    HourlyBarStore,
    HourlyBars,
)
from anomaly_science.strategy.xsect_momentum.research.position_management_hypothesis_grid import (
    GRID_OUT,
    PROTOCOL,
    _plateau,
)
from anomaly_science.strategy.xsect_momentum.research.position_management_screen import (
    HOURLY_ROOT,
    IS_END,
    OUT,
    RUN_ROOT,
    SCALES,
    SCREEN_OUT,
    _entry_atr,
    _event_return,
    _first_level_event,
    _path_indices,
    _side,
    _signed_return,
)


TARGET_OUT = GRID_OUT / "structure_atr_target"
MULTIPLES = (3, 4, 5)
FRACTIONS = (0.25, 0.50, 0.75, 1.00)
APPLICATIONS = ("symmetric", "long_only", "short_only")


def _qualifying_target(
    bars: HourlyBars,
    entry_time: pd.Timestamp,
    entry_price: float,
    weight: float,
    atr: float,
    scale: int,
    minimum_atr: int,
    lookback_hours: int = 336,
) -> tuple[float, float] | None:
    end = int(np.searchsorted(bars.timestamp_ns, int(entry_time.value), side="left"))
    latest = end - scale - 1
    earliest_time = int((entry_time - pd.Timedelta(hours=lookback_hours)).value)
    earliest = max(scale, int(np.searchsorted(bars.timestamp_ns, earliest_time, side="left")))
    if latest < earliest or not np.isfinite(atr) or atr <= 0.0:
        return None
    values = bars.high if weight > 0.0 else bars.low
    candidates: list[tuple[float, int, float]] = []
    for index in range(earliest, latest + 1):
        window = values[index - scale : index + scale + 1]
        if len(window) != 2 * scale + 1 or not np.isfinite(window).all():
            continue
        level = float(values[index])
        is_pivot = level >= float(window.max()) if weight > 0.0 else level <= float(window.min())
        distance = (level - entry_price) / atr if weight > 0.0 else (entry_price - level) / atr
        if is_pivot and distance >= minimum_atr:
            candidates.append((float(distance), -index, level))
    if not candidates:
        return None
    distance, _, level = min(candidates)
    return level, distance


def _feature_rows(ledger: pd.DataFrame, store: HourlyBarStore) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for trade in ledger.to_dict("records"):
        bars = store.load(str(trade["symbol"]))
        if bars is None:
            raise FileNotFoundError(trade["symbol"])
        entry = pd.Timestamp(trade["entry_time"])
        exit_ = pd.Timestamp(trade["exit_time"])
        weight = float(trade["weight"])
        entry_price = float(trade["entry_price"])
        final_price = float(trade["exit_price"])
        entry_index = int(np.searchsorted(bars.timestamp_ns, int(entry.value), side="left"))
        atr = _entry_atr(bars, entry_index)
        indices = _path_indices(bars, entry, exit_, trade["exit_reason"] == "end_of_sample")
        hold_gross = _signed_return(weight, entry_price, final_price)
        notional = abs(weight * float(trade["equity_at_entry"]))
        row: dict[str, object] = {
            "trade_id": int(trade["trade_id"]),
            "year": entry.year,
            "side": _side(weight),
            "entry_notional": notional,
            "cost_rate": float(trade["pnl_cost"] / notional),
            "hold_gross_return": hold_gross,
        }
        for scale in SCALES:
            for multiple in MULTIPLES:
                target = _qualifying_target(
                    bars, entry, entry_price, weight, atr, scale, multiple,
                )
                event = _first_level_event(
                    bars, indices, weight, target[0] if target else None, "target",
                )
                row[f"s{scale}_a{multiple}_return"] = _event_return(weight, entry_price, event)
                row[f"s{scale}_a{multiple}_distance"] = target[1] if target else np.nan
                row[f"s{scale}_a{multiple}_touched"] = event is not None
        rows.append(row)
    return pd.DataFrame(rows)


def _metrics(
    run_id: str,
    policy_id: str,
    pnl: np.ndarray,
    features: pd.DataFrame,
    **extra: object,
) -> dict[str, object]:
    positive = float(pnl[pnl > 0.0].sum())
    negative = float(-pnl[pnl < 0.0].sum())
    count = max(1, int(np.ceil(len(pnl) * 0.01)))
    top = float(np.partition(pnl, len(pnl) - count)[-count:].sum())
    years = features["year"].to_numpy(dtype=int)
    return {
        "run_id": run_id,
        "policy_id": policy_id,
        "family": "structure_atr_target",
        "admissible": True,
        **extra,
        "trades": len(pnl),
        "net_pnl": float(pnl.sum()),
        "return_on_entry_notional": float(pnl.sum() / features["entry_notional"].sum()),
        "profit_factor": float(positive / negative) if negative else np.nan,
        "win_rate": float((pnl > 0.0).mean()),
        "top1_drop_remaining_pnl": float(pnl.sum() - top),
        **{f"pnl_{year}": float(pnl[years == year].sum()) for year in (2023, 2024, 2025)},
    }


def main() -> None:
    TARGET_OUT.mkdir(parents=True, exist_ok=True)
    feature_root = TARGET_OUT / "features"
    feature_root.mkdir(parents=True, exist_ok=True)
    run_ids = sorted(path.stem for path in (SCREEN_OUT / "mandate_policy_results").glob("*.parquet"))
    store = HourlyBarStore(HOURLY_ROOT, IS_END)
    rows: list[dict[str, object]] = []
    source_hashes: dict[str, str] = {}
    for run_number, run_id in enumerate(run_ids, start=1):
        print(f"[{run_number}/{len(run_ids)}] structure targets {run_id}", flush=True)
        ledger_path = RUN_ROOT / f"{run_id}_trades.parquet"
        source_hashes[str(ledger_path)] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        ledger = pd.read_parquet(ledger_path).reset_index(drop=True)
        ledger["trade_id"] = np.arange(len(ledger), dtype=np.int64)
        ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
        ledger["exit_time"] = pd.to_datetime(ledger["exit_time"], utc=True)
        features = _feature_rows(ledger, store)
        features.to_parquet(feature_root / f"{run_id}.parquet", index=False)
        hold_net = features["hold_gross_return"] - features["cost_rate"]
        side = features["side"].to_numpy()
        notional = features["entry_notional"].to_numpy(dtype=float)
        for scale in SCALES:
            for multiple in MULTIPLES:
                event = features[f"s{scale}_a{multiple}_return"]
                for fraction in FRACTIONS:
                    managed_gross = fraction * event.fillna(features["hold_gross_return"]) + (1.0 - fraction) * features["hold_gross_return"]
                    managed_net = managed_gross - features["cost_rate"]
                    for application in APPLICATIONS:
                        if application == "symmetric":
                            net = managed_net.to_numpy(dtype=float)
                        else:
                            target_side = "long" if application == "long_only" else "short"
                            net = np.where(side == target_side, managed_net, hold_net)
                        pnl = net * notional
                        policy_id = f"{application}::s{scale}_a{multiple}_f{int(fraction * 100)}"
                        rows.append(_metrics(
                            run_id, policy_id, pnl, features,
                            application=application, scale=scale, atr_multiple=multiple,
                            fraction=fraction,
                            target_coverage=float(event.notna().mean()),
                            target_touch_share=float(features[f"s{scale}_a{multiple}_touched"].mean()),
                        ))
    summary = pd.DataFrame(rows)
    plateau = _plateau(summary)
    summary.to_parquet(TARGET_OUT / "config_summary.parquet", index=False)
    summary.to_csv(TARGET_OUT / "config_summary.csv", index=False)
    plateau.to_parquet(TARGET_OUT / "plateau.parquet", index=False)
    plateau.to_csv(TARGET_OUT / "plateau.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "run_count": len(run_ids),
        "policy_count": int(summary["policy_id"].nunique()),
        "source_ledger_sha256": source_hashes,
        "physical_target_contract": "exact_confirmed_pre_entry_swing; ATR selects eligible swing only",
    }
    (TARGET_OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"completed structure target grid policies={summary.policy_id.nunique()} "
        f"strict_survivors={int(plateau.neighbourhood_supported.sum())}",
        flush=True,
    )


if __name__ == "__main__":
    main()
