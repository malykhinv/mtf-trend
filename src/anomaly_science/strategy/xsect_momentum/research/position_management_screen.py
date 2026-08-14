"""Frozen mandate-level screen for causal position-management primitives.

Protocol: docs/strategies/xsect_momentum_position_management_screen_v1.md
This is a fast Stage-1 screen.  It does not replace exact portfolio replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_structural import (
    HourBar,
    HourlyBarStore,
    HourlyBars,
    StructuralAnchorSpec,
    confirmed_structural_anchor,
    stop_fill_price,
)


OUT = Path(".output/results/xsect_momentum")
RUN_ROOT = OUT / "hourly_structural_runs_v2"
SCREEN_OUT = OUT / "position_management_screen_v1"
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
SUMMARY = OUT / "hourly_structural_summary_v2.parquet"
PROTOCOL = Path("docs/strategies/xsect_momentum_position_management_screen_v1.md")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")
SCALES = (6, 12, 24)
FRACTIONS = (0.25, 0.50, 0.75, 1.00)
ATR_MULTIPLES = (1, 2, 3, 5)


Family = Literal[
    "hold_control",
    "time_exit",
    "structural_stop",
    "structural_target",
    "structural_bracket",
    "mfe_armed_structural_trail",
    "favourable_add",
    "adverse_add",
    "fixed_atr_take_profit_diagnostic",
]


@dataclass(frozen=True, slots=True)
class PolicySpec:
    policy_id: str
    family: Family
    admissible: bool = True
    scale: int | None = None
    fraction: float | None = None
    hours: int | None = None
    atr_multiple: int | None = None
    starter_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class ExitEvent:
    timestamp: pd.Timestamp
    price: float


def registered_policies() -> tuple[PolicySpec, ...]:
    policies: list[PolicySpec] = [PolicySpec("hold", "hold_control")]
    policies.extend(
        PolicySpec(f"time_{hours}h", "time_exit", hours=hours)
        for hours in (24, 48, 72, 120, 168)
    )
    for scale in SCALES:
        for fraction in FRACTIONS:
            suffix = f"s{scale}_f{int(fraction * 100)}"
            policies.append(PolicySpec(f"stop_{suffix}", "structural_stop", scale=scale, fraction=fraction))
            policies.append(PolicySpec(f"target_{suffix}", "structural_target", scale=scale, fraction=fraction))
            policies.append(PolicySpec(f"bracket_{suffix}", "structural_bracket", scale=scale, fraction=fraction))
    for scale in SCALES:
        for multiple in ATR_MULTIPLES:
            for fraction in (0.25, 0.50, 1.00):
                policies.append(PolicySpec(
                    f"trail_s{scale}_a{multiple}_f{int(fraction * 100)}",
                    "mfe_armed_structural_trail",
                    scale=scale,
                    atr_multiple=multiple,
                    fraction=fraction,
                ))
    for family, prefix in (("favourable_add", "favadd"), ("adverse_add", "advadd")):
        for starter in (0.50, 0.75):
            for multiple in (1, 2):
                policies.append(PolicySpec(
                    f"{prefix}_s{int(starter * 100)}_a{multiple}",
                    family,  # type: ignore[arg-type]
                    starter_fraction=starter,
                    atr_multiple=multiple,
                ))
    for multiple in (1, 2, 3, 4, 5):
        for fraction in FRACTIONS:
            policies.append(PolicySpec(
                f"atr_tp_a{multiple}_f{int(fraction * 100)}",
                "fixed_atr_take_profit_diagnostic",
                admissible=False,
                atr_multiple=multiple,
                fraction=fraction,
            ))
    return tuple(policies)


def _side(weight: float) -> Literal["long", "short"]:
    return "long" if weight > 0.0 else "short"


def _signed_return(weight: float, entry_price: float, exit_price: float) -> float:
    return float(np.sign(weight) * (exit_price / entry_price - 1.0))


def _target_fill(weight: float, target: float | None, bar: HourBar) -> float | None:
    if target is None:
        return None
    if weight > 0.0:
        if bar.open >= target:
            return target
        if bar.high >= target:
            return target
    else:
        if bar.open <= target:
            return target
        if bar.low <= target:
            return target
    return None


def _path_indices(bars: HourlyBars, entry: pd.Timestamp, exit_: pd.Timestamp, include_exit: bool) -> np.ndarray:
    start = int(np.searchsorted(bars.timestamp_ns, int(entry.value), side="left"))
    end_side = "right" if include_exit else "left"
    end = int(np.searchsorted(bars.timestamp_ns, int(exit_.value), side=end_side))
    return np.arange(start, end, dtype=np.int64)


def _hour_bar(bars: HourlyBars, index: int) -> HourBar:
    return HourBar(
        timestamp=pd.Timestamp(int(bars.timestamp_ns[index]), tz="UTC"),
        open=float(bars.open[index]),
        high=float(bars.high[index]),
        low=float(bars.low[index]),
        close=float(bars.close[index]),
    )


def _entry_atr(bars: HourlyBars, entry_index: int, length: int = 14) -> float:
    start = entry_index - length
    if start < 1:
        return np.nan
    high = bars.high[start:entry_index]
    low = bars.low[start:entry_index]
    previous_close = bars.close[start - 1 : entry_index - 1]
    true_range = np.maximum.reduce((high - low, np.abs(high - previous_close), np.abs(low - previous_close)))
    return float(np.mean(true_range)) if np.isfinite(true_range).all() else np.nan


def _first_level_event(
    bars: HourlyBars,
    indices: np.ndarray,
    weight: float,
    level: float | None,
    kind: Literal["stop", "target"],
    after: pd.Timestamp | None = None,
) -> ExitEvent | None:
    for index in indices:
        bar = _hour_bar(bars, int(index))
        if after is not None and bar.timestamp <= after:
            continue
        fill = stop_fill_price(weight, level, bar) if kind == "stop" else _target_fill(weight, level, bar)
        if fill is not None:
            return ExitEvent(bar.timestamp, float(fill))
    return None


def _activation_index(
    bars: HourlyBars,
    indices: np.ndarray,
    weight: float,
    entry_price: float,
    atr: float,
    multiple: int,
    favourable: bool,
) -> int | None:
    distance = multiple * atr
    for index in indices:
        if weight > 0.0:
            touched = bars.high[index] >= entry_price + distance if favourable else bars.low[index] <= entry_price - distance
        else:
            touched = bars.low[index] <= entry_price - distance if favourable else bars.high[index] >= entry_price + distance
        if touched:
            next_index = int(index) + 1
            return next_index if next_index in indices else None
    return None


def _confirmed_path_pivots(
    bars: HourlyBars,
    indices: np.ndarray,
    weight: float,
    entry_price: float,
    scale: int,
) -> list[tuple[int, float]]:
    if not len(indices):
        return []
    start = int(indices[0])
    end = int(indices[-1])
    values = bars.low if weight > 0.0 else bars.high
    pivots: list[tuple[int, float]] = []
    for pivot_index in range(max(scale, start), end - scale + 1):
        window = values[pivot_index - scale : pivot_index + scale + 1]
        if len(window) != 2 * scale + 1 or not np.isfinite(window).all():
            continue
        level = float(values[pivot_index])
        is_pivot = level <= float(window.min()) if weight > 0.0 else level >= float(window.max())
        protects_profit = level > entry_price if weight > 0.0 else level < entry_price
        confirmation_index = pivot_index + scale + 1
        if is_pivot and protects_profit and confirmation_index <= end:
            pivots.append((confirmation_index, level))
    return pivots


def _trail_event(
    bars: HourlyBars,
    indices: np.ndarray,
    weight: float,
    entry_price: float,
    atr: float,
    scale: int,
    multiple: int,
) -> ExitEvent | None:
    armed_index = _activation_index(bars, indices, weight, entry_price, atr, multiple, True)
    if armed_index is None:
        return None
    pivots = _confirmed_path_pivots(bars, indices, weight, entry_price, scale)
    by_confirmation: dict[int, list[float]] = {}
    for confirmation_index, level in pivots:
        by_confirmation.setdefault(confirmation_index, []).append(level)
    stop: float | None = None
    for index in indices:
        index = int(index)
        if index < armed_index:
            continue
        for level in by_confirmation.get(index, []):
            if stop is None:
                stop = level
            elif weight > 0.0:
                stop = max(stop, level)
            else:
                stop = min(stop, level)
        if stop is None:
            continue
        bar = _hour_bar(bars, index)
        fill = stop_fill_price(weight, stop, bar)
        if fill is not None:
            return ExitEvent(bar.timestamp, float(fill))
    return None


def _event_return(weight: float, entry_price: float, event: ExitEvent | None) -> float:
    return _signed_return(weight, entry_price, event.price) if event is not None else np.nan


def _trade_feature_row(trade: pd.Series, bars: HourlyBars) -> dict[str, object]:
    entry = pd.Timestamp(trade["entry_time"])
    exit_ = pd.Timestamp(trade["exit_time"])
    include_exit = trade["exit_reason"] == "end_of_sample"
    indices = _path_indices(bars, entry, exit_, include_exit)
    entry_index = int(np.searchsorted(bars.timestamp_ns, int(entry.value), side="left"))
    atr = _entry_atr(bars, entry_index)
    weight = float(trade["weight"])
    entry_price = float(trade["entry_price"])
    final_price = float(trade["exit_price"])
    hold_return = _signed_return(weight, entry_price, final_price)
    row: dict[str, object] = {
        "trade_id": int(trade["trade_id"]),
        "symbol": trade["symbol"],
        "entry_time": entry,
        "exit_time": exit_,
        "year": entry.year,
        "side": _side(weight),
        "weight": weight,
        "entry_notional": float(abs(weight * trade["equity_at_entry"])),
        "baseline_cost_rate": float(trade["pnl_cost"] / abs(weight * trade["equity_at_entry"])),
        "hold_gross_return": hold_return,
        "entry_atr": atr,
        "entry_atr_pct": atr / entry_price,
    }
    for hours in (24, 48, 72, 120, 168):
        timestamp = entry + pd.Timedelta(hours=hours)
        index = int(np.searchsorted(bars.timestamp_ns, int(timestamp.value), side="left"))
        if timestamp < exit_ and index < len(bars.timestamp_ns) and int(bars.timestamp_ns[index]) == int(timestamp.value):
            row[f"time_{hours}_return"] = _signed_return(weight, entry_price, float(bars.open[index]))
        else:
            row[f"time_{hours}_return"] = hold_return

    for scale in SCALES:
        spec = StructuralAnchorSpec(scale, scale, 336)
        adverse = confirmed_structural_anchor(bars, entry, entry_price, _side(weight), spec)
        favourable_side: Literal["long", "short"] = "short" if weight > 0.0 else "long"
        favourable = confirmed_structural_anchor(bars, entry, entry_price, favourable_side, spec)
        stop_event = _first_level_event(
            bars, indices, weight, adverse.price if adverse else None, "stop",
        )
        target_event = _first_level_event(
            bars, indices, weight, favourable.price if favourable else None, "target",
        )
        row[f"stop_s{scale}_return"] = _event_return(weight, entry_price, stop_event)
        row[f"stop_s{scale}_time"] = stop_event.timestamp if stop_event else pd.NaT
        row[f"target_s{scale}_return"] = _event_return(weight, entry_price, target_event)
        row[f"target_s{scale}_time"] = target_event.timestamp if target_event else pd.NaT
        if stop_event is not None and (target_event is None or stop_event.timestamp <= target_event.timestamp):
            row[f"bracket_s{scale}_stop_return"] = _event_return(weight, entry_price, stop_event)
            row[f"bracket_s{scale}_target_return"] = np.nan
            row[f"bracket_s{scale}_remaining_return"] = np.nan
            row[f"bracket_s{scale}_outcome"] = "stop_first"
        elif target_event is not None:
            later_stop = _first_level_event(
                bars, indices, weight, adverse.price if adverse else None, "stop", after=target_event.timestamp,
            )
            row[f"bracket_s{scale}_stop_return"] = np.nan
            row[f"bracket_s{scale}_target_return"] = _event_return(weight, entry_price, target_event)
            row[f"bracket_s{scale}_remaining_return"] = (
                _event_return(weight, entry_price, later_stop) if later_stop else hold_return
            )
            row[f"bracket_s{scale}_outcome"] = "target_first"
        else:
            row[f"bracket_s{scale}_stop_return"] = np.nan
            row[f"bracket_s{scale}_target_return"] = np.nan
            row[f"bracket_s{scale}_remaining_return"] = hold_return
            row[f"bracket_s{scale}_outcome"] = "no_touch"
        for multiple in ATR_MULTIPLES:
            trail = _trail_event(bars, indices, weight, entry_price, atr, scale, multiple)
            row[f"trail_s{scale}_a{multiple}_return"] = _event_return(weight, entry_price, trail)

    for multiple in (1, 2, 3, 4, 5):
        target = entry_price + np.sign(weight) * multiple * atr
        event = _first_level_event(bars, indices, weight, float(target), "target")
        row[f"atr_tp_a{multiple}_return"] = _event_return(weight, entry_price, event)
    for direction, favourable in (("favadd", True), ("advadd", False)):
        for multiple in (1, 2):
            add_index = _activation_index(bars, indices, weight, entry_price, atr, multiple, favourable)
            if add_index is None:
                row[f"{direction}_a{multiple}_add_return"] = np.nan
            else:
                add_price = float(bars.open[add_index])
                row[f"{direction}_a{multiple}_add_return"] = _signed_return(weight, add_price, final_price)
    return row


def _apply_policy(features: pd.DataFrame, policy: PolicySpec) -> tuple[pd.Series, pd.Series]:
    hold = features["hold_gross_return"]
    cost_scale = pd.Series(1.0, index=features.index)
    if policy.family == "hold_control":
        gross = hold
    elif policy.family == "time_exit":
        gross = features[f"time_{policy.hours}_return"]
    elif policy.family in {"structural_stop", "structural_target"}:
        prefix = "stop" if policy.family == "structural_stop" else "target"
        event = features[f"{prefix}_s{policy.scale}_return"]
        fraction = float(policy.fraction)
        gross = event.fillna(hold) * fraction + hold * (1.0 - fraction)
    elif policy.family == "structural_bracket":
        fraction = float(policy.fraction)
        stop = features[f"bracket_s{policy.scale}_stop_return"]
        target = features[f"bracket_s{policy.scale}_target_return"]
        remaining = features[f"bracket_s{policy.scale}_remaining_return"]
        gross = np.where(
            stop.notna(), stop,
            np.where(target.notna(), fraction * target + (1.0 - fraction) * remaining, hold),
        )
        gross = pd.Series(gross, index=features.index)
    elif policy.family == "mfe_armed_structural_trail":
        event = features[f"trail_s{policy.scale}_a{policy.atr_multiple}_return"]
        fraction = float(policy.fraction)
        gross = event.fillna(hold) * fraction + hold * (1.0 - fraction)
    elif policy.family in {"favourable_add", "adverse_add"}:
        prefix = "favadd" if policy.family == "favourable_add" else "advadd"
        added = features[f"{prefix}_a{policy.atr_multiple}_add_return"]
        starter = float(policy.starter_fraction)
        hit = added.notna()
        gross = starter * hold + (1.0 - starter) * added.fillna(0.0)
        cost_scale = pd.Series(np.where(hit, 1.0, starter), index=features.index)
    elif policy.family == "fixed_atr_take_profit_diagnostic":
        event = features[f"atr_tp_a{policy.atr_multiple}_return"]
        fraction = float(policy.fraction)
        gross = event.fillna(hold) * fraction + hold * (1.0 - fraction)
    else:
        raise ValueError(f"unsupported family {policy.family}")
    return gross.astype(float), cost_scale


def _summarize(run_id: str, policy: PolicySpec, results: pd.DataFrame) -> dict[str, object]:
    pnl = results["pnl"]
    positive = float(pnl.loc[pnl > 0.0].sum())
    negative = float(-pnl.loc[pnl < 0.0].sum())
    drop_count = max(1, int(np.ceil(len(results) * 0.01)))
    remaining = float(pnl.sum() - pnl.nlargest(drop_count).sum())
    return {
        "run_id": run_id,
        **asdict(policy),
        "trades": len(results),
        "net_pnl": float(pnl.sum()),
        "return_on_entry_notional": float(pnl.sum() / results["entry_notional"].sum()),
        "profit_factor": float(positive / negative) if negative else np.nan,
        "win_rate": float((pnl > 0.0).mean()),
        "top1_drop_remaining_pnl": remaining,
        "worst_trade_pnl": float(pnl.min()),
        "worst_trade_return": float(results["net_return"].min()),
        **{
            f"pnl_{year}": float(results.loc[results["year"] == year, "pnl"].sum())
            for year in (2023, 2024, 2025)
        },
    }


def _policy_distance(left: pd.Series, right: pd.Series) -> int:
    parameters = ("scale", "fraction", "hours", "atr_multiple", "starter_fraction")
    differences = 0
    for parameter in parameters:
        a, b = left[parameter], right[parameter]
        if pd.isna(a) and pd.isna(b):
            continue
        if a != b:
            differences += 1
    return differences


def _plateau_table(summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for policy_id, group in summary.groupby("policy_id", sort=False):
        positive = group.loc[group["net_pnl"] > 0.0, "net_pnl"]
        positive_total = float(positive.sum())
        core = {
            "positive_configs": int((group["net_pnl"] > 0.0).sum()),
            "median_profit_factor": float(group["profit_factor"].median()),
            "pooled_pnl_2023": float(group["pnl_2023"].sum()),
            "pooled_pnl_2024": float(group["pnl_2024"].sum()),
            "pooled_pnl_2025": float(group["pnl_2025"].sum()),
            "pooled_top1_drop_remaining": float(group["top1_drop_remaining_pnl"].sum()),
            "max_positive_config_share": (
                float(positive.max() / positive_total) if positive_total else np.nan
            ),
        }
        first = group.iloc[0]
        core_pass = (
            core["positive_configs"] >= 12
            and core["median_profit_factor"] >= 1.05
            and min(core["pooled_pnl_2023"], core["pooled_pnl_2024"], core["pooled_pnl_2025"]) > 0.0
            and core["pooled_top1_drop_remaining"] > 0.0
            and core["max_positive_config_share"] <= 0.25
            and bool(first["admissible"])
        )
        rows.append({
            "policy_id": policy_id,
            "family": first["family"],
            "admissible": bool(first["admissible"]),
            "scale": first["scale"],
            "fraction": first["fraction"],
            "hours": first["hours"],
            "atr_multiple": first["atr_multiple"],
            "starter_fraction": first["starter_fraction"],
            **core,
            "core_plateau_gates_pass": core_pass,
        })
    plateau = pd.DataFrame(rows)
    supported: list[bool] = []
    for _, row in plateau.iterrows():
        neighbours = plateau.loc[
            (plateau["family"] == row["family"])
            & plateau["core_plateau_gates_pass"]
            & (plateau["policy_id"] != row["policy_id"])
        ]
        adjacent = any(_policy_distance(row, other) == 1 for _, other in neighbours.iterrows())
        supported.append(bool(row["core_plateau_gates_pass"] and adjacent))
    plateau["neighbourhood_supported"] = supported
    plateau["rejection_reason"] = np.select(
        [
            ~plateau["admissible"],
            plateau["positive_configs"] < 12,
            plateau["median_profit_factor"] < 1.05,
            plateau[["pooled_pnl_2023", "pooled_pnl_2024", "pooled_pnl_2025"]].min(axis=1) <= 0.0,
            plateau["pooled_top1_drop_remaining"] <= 0.0,
            plateau["max_positive_config_share"] > 0.25,
            ~plateau["neighbourhood_supported"],
        ],
        [
            "diagnostic_only", "fewer_than_12_positive_configs", "median_pf_below_1.05",
            "nonpositive_year", "fails_top1_concentration", "single_config_concentration",
            "no_adjacent_supported_policy",
        ],
        default="accepted_for_stage2",
    )
    return plateau


def main() -> None:
    SCREEN_OUT.mkdir(parents=True, exist_ok=True)
    partition_root = SCREEN_OUT / "mandate_policy_results"
    partition_root.mkdir(parents=True, exist_ok=True)
    summary = pd.read_parquet(SUMMARY)
    runs = summary.loc[(summary["control"] == "real") & (summary["exit_variant"] == "no_stop"), "run_id"].tolist()
    policies = registered_policies()
    store = HourlyBarStore(HOURLY_ROOT, IS_END)
    checkpoint_path = SCREEN_OUT / "policy_config_summary.checkpoint.parquet"
    summary_rows: list[dict[str, object]] = (
        pd.read_parquet(checkpoint_path).to_dict("records")
        if checkpoint_path.exists()
        else []
    )
    source_hashes: dict[str, str] = {}
    for run_number, run_id in enumerate(runs, start=1):
        output_path = partition_root / f"{run_id}.parquet"
        ledger_path = RUN_ROOT / f"{run_id}_trades.parquet"
        source_hashes[str(ledger_path)] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        existing_policy_count = len({
            row["policy_id"] for row in summary_rows if row["run_id"] == run_id
        })
        if output_path.exists() and existing_policy_count == len(policies):
            print(f"[{run_number}/{len(runs)}] resume after {run_id}", flush=True)
            continue
        if existing_policy_count:
            summary_rows = [row for row in summary_rows if row["run_id"] != run_id]
        print(f"[{run_number}/{len(runs)}] features {run_id}", flush=True)
        ledger = pd.read_parquet(ledger_path).reset_index(drop=True)
        ledger["trade_id"] = np.arange(len(ledger), dtype=np.int64)
        ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
        ledger["exit_time"] = pd.to_datetime(ledger["exit_time"], utc=True)
        feature_rows: list[dict[str, object]] = []
        for trade in ledger.to_dict("records"):
            bars = store.load(str(trade["symbol"]))
            if bars is None:
                raise FileNotFoundError(f"missing hourly bars for {trade['symbol']}")
            feature_rows.append(_trade_feature_row(pd.Series(trade), bars))
        features = pd.DataFrame(feature_rows)
        result_parts: list[pd.DataFrame] = []
        for policy in policies:
            gross, cost_scale = _apply_policy(features, policy)
            net = gross - features["baseline_cost_rate"] * cost_scale
            result = features[["trade_id", "year", "side", "entry_notional"]].copy()
            result["policy_id"] = policy.policy_id
            result["net_return"] = net
            result["pnl"] = net * result["entry_notional"]
            result_parts.append(result)
            summary_rows.append(_summarize(run_id, policy, result))
        pd.concat(result_parts, ignore_index=True).to_parquet(output_path, index=False)
        pd.DataFrame(summary_rows).to_parquet(checkpoint_path, index=False)
        print(f"[{run_number}/{len(runs)}] wrote {output_path}", flush=True)
    config_summary = pd.DataFrame(summary_rows)
    plateau = _plateau_table(config_summary)
    config_summary.to_parquet(SCREEN_OUT / "policy_config_summary.parquet", index=False)
    config_summary.to_csv(SCREEN_OUT / "policy_config_summary.csv", index=False)
    plateau.to_parquet(SCREEN_OUT / "policy_plateau.parquet", index=False)
    plateau.to_csv(SCREEN_OUT / "policy_plateau.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "is_end_exclusive": IS_END.isoformat(),
        "source_hourly_file_count": len(list(HOURLY_ROOT.glob("*.parquet"))),
        "run_count": len(runs),
        "policy_count": len(policies),
        "source_ledger_sha256": source_hashes,
        "stage": "mandate_level_screen_not_exact_portfolio_replay",
    }
    (SCREEN_OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    accepted = plateau.loc[plateau["neighbourhood_supported"]]
    print(f"completed {len(runs)} runs x {len(policies)} policies; plateau survivors={len(accepted)}", flush=True)


if __name__ == "__main__":
    main()
