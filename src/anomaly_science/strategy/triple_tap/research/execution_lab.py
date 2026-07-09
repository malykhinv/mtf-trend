"""Causal structural entry/exit policy lab for triple-tap IS research.

No policy creates a fixed-percent or ATR target.  Entry and exit levels are
limited to the already-observed breakout level, prior culmination, original
structural stop, and original structural measured-move target.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_long.research.context import DEV_END_MS
from anomaly_science.strategy.triple_tap.detect import CACHE_1M, TFS, _load_1m
from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.audit import (
    _calendar_stats,
    _causal_quantile_selection,
    _ledger,
    _metrics,
)
from anomaly_science.strategy.triple_tap.research.economics import COST_RATE, RES
from anomaly_science.strategy.triple_tap.research.labels import HORIZON_TF_BARS

POLICY_OUT = RES / "execution_policies.parquet"
SURFACE_OUT = Path("research/triple_tap_execution_policy_surface.json")
PARTIAL_FRACTIONS = (0.25, 0.50, 0.75)
STRUCTURAL_TARGET_FRACTIONS = (0.25, 0.50, 0.75)
BREAK_EVEN_TRIGGER_R = (0.30, 0.40, 0.50, 0.60, 0.70, 1.00)
BASELINE_SLIPPAGE_RATE = 0.005
INACTIVITY_HOURS = (6.0, 12.0)
INACTIVITY_VOL_RETURN_MULT = 0.75
INACTIVITY_RECENT_MINUTES = 30
EXPECTED_FORWARD_MONTHS = 5  # Aug-Dec 2025 after the ten-week training warm-up


def _result(
    *,
    status: str,
    r_multiple: float = np.nan,
    fill_time_ms: float = np.nan,
    outcome_time_ms: float = np.nan,
    entry_price: float = np.nan,
    stop: float = np.nan,
) -> dict:
    dist_stop = (entry_price - stop) / entry_price if entry_price > stop > 0 else np.nan
    return {
        "status": status,
        "label": status,
        "r_multiple": r_multiple,
        "fill_time_ms": fill_time_ms,
        "outcome_time_ms": outcome_time_ms,
        "entry_price": entry_price,
        "stop": stop,
        "dist_stop": dist_stop,
    }


def _simulate_from_fill(
    ts: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    qv: np.ndarray | None = None,
    *,
    first_bar: int,
    fill_time_ms: int,
    entry: float,
    stop: float,
    final_target: float,
    end_ms: int,
    middle_target: float | None = None,
    partial_fraction: float = 0.0,
    trail_to_level: float | None = None,
    break_even_trigger_r: float | None = None,
    inactivity_ms: int | None = None,
) -> dict:
    risk = entry - stop
    end = int(np.searchsorted(ts, min(end_ms, DEV_END_MS), side="left"))
    full_horizon = end_ms <= DEV_END_MS
    if risk <= 0 or first_bar >= end:
        return _result(status="invalid", fill_time_ms=fill_time_ms, entry_price=entry, stop=stop)
    final_r = (final_target - entry) / risk
    middle_valid = middle_target is not None and entry < middle_target < final_target
    if middle_target is not None and not middle_valid:
        return _result(status="no_middle_anchor")
    middle_r = (middle_target - entry) / risk if middle_valid else np.nan
    realised_r = 0.0
    remaining = 1.0
    middle_hit = False
    active_stop = stop
    break_even_hit = False
    max_favorable_r = 0.0
    cost_floor_r = 2.0 * (COST_RATE + BASELINE_SLIPPAGE_RATE) / ((entry - stop) / entry)
    effective_be_trigger_r = (
        max(float(break_even_trigger_r), cost_floor_r)
        if break_even_trigger_r is not None
        else np.nan
    )
    if inactivity_ms is not None and qv is None:
        raise ValueError("inactivity exit policy requires quote-volume array")
    pre_vol_start = max(0, first_bar - 60)
    pre_entry_vol = (
        float(np.median(qv[pre_vol_start:first_bar]))
        if qv is not None and first_bar > pre_vol_start
        else np.nan
    )

    for j in range(first_bar, end):
        if low[j] <= active_stop:
            stop_r = (active_stop - entry) / risk
            return _result(
                status="stop", r_multiple=realised_r + remaining * stop_r,
                fill_time_ms=fill_time_ms, outcome_time_ms=int(ts[j]) + 60_000,
                entry_price=entry, stop=stop,
            )
        max_favorable_r = max(max_favorable_r, (float(high[j]) - entry) / risk)
        if (
            not break_even_hit
            and break_even_trigger_r is not None
            and max_favorable_r >= effective_be_trigger_r
        ):
            active_stop = max(active_stop, entry)
            break_even_hit = True
            if low[j] <= active_stop:
                return _result(
                    status="break_even_same_bar",
                    r_multiple=realised_r,
                    fill_time_ms=fill_time_ms,
                    outcome_time_ms=int(ts[j]) + 60_000,
                    entry_price=entry,
                    stop=stop,
                )
        if inactivity_ms is not None and int(ts[j]) >= fill_time_ms + inactivity_ms:
            recent_bars = max(1, INACTIVITY_RECENT_MINUTES)
            v0 = max(first_bar, j - recent_bars + 1)
            recent_vol = float(np.median(qv[v0:j + 1])) if qv is not None and j >= v0 else np.nan
            low_volume = (
                np.isfinite(pre_entry_vol)
                and pre_entry_vol > 0
                and np.isfinite(recent_vol)
                and recent_vol <= pre_entry_vol * INACTIVITY_VOL_RETURN_MULT
            )
            no_movement = (
                break_even_trigger_r is None
                or max_favorable_r < effective_be_trigger_r
            )
            if low_volume and no_movement:
                close_r = (float(close[j]) - entry) / risk
                if close_r < 0:
                    return _result(
                        status="inactivity_exit_loss",
                        r_multiple=realised_r + remaining * close_r,
                        fill_time_ms=fill_time_ms,
                        outcome_time_ms=int(ts[j]) + 60_000,
                        entry_price=entry,
                        stop=stop,
                    )
                active_stop = max(active_stop, entry)
                break_even_hit = True
                if low[j] <= active_stop:
                    return _result(
                        status="inactivity_to_break_even_same_bar",
                        r_multiple=realised_r,
                        fill_time_ms=fill_time_ms,
                        outcome_time_ms=int(ts[j]) + 60_000,
                        entry_price=entry,
                        stop=stop,
                    )
        if middle_valid and not middle_hit and high[j] >= middle_target:
            if partial_fraction >= 1.0:
                return _result(
                    status="middle_target", r_multiple=float(middle_r),
                    fill_time_ms=fill_time_ms, outcome_time_ms=int(ts[j]) + 60_000,
                    entry_price=entry, stop=stop,
                )
            middle_hit = True
            realised_r = partial_fraction * middle_r
            remaining = 1.0 - partial_fraction
            if trail_to_level is not None:
                active_stop = max(stop, min(float(trail_to_level), entry))
            # New stop becomes active only after the middle target.  If both are
            # inside one 1m bar, assume the new stop is subsequently hit.
            if low[j] <= active_stop:
                stop_r = (active_stop - entry) / risk
                return _result(
                    status="middle_then_stop_same_bar",
                    r_multiple=realised_r + remaining * stop_r,
                    fill_time_ms=fill_time_ms, outcome_time_ms=int(ts[j]) + 60_000,
                    entry_price=entry, stop=stop,
                )
        if high[j] >= final_target:
            return _result(
                status="final_target", r_multiple=realised_r + remaining * final_r,
                fill_time_ms=fill_time_ms, outcome_time_ms=int(ts[j]) + 60_000,
                entry_price=entry, stop=stop,
            )

    if not full_horizon:
        return _result(status="unresolved_is_boundary")
    timeout_r = (float(close[end - 1]) - entry) / risk
    return _result(
        status="timeout", r_multiple=realised_r + remaining * timeout_r,
        fill_time_ms=fill_time_ms, outcome_time_ms=int(ts[end - 1]) + 60_000,
        entry_price=entry, stop=stop,
    )


def _simulate_retest(
    ts: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    qv: np.ndarray | None = None,
    *,
    signal_fill_ms: int,
    level: float,
    stop: float,
    final_target: float,
    end_ms: int,
    middle_target: float | None,
    partial_fraction: float,
    trail_after_middle: bool,
    break_even_trigger_r: float | None,
    inactivity_ms: int | None,
) -> dict:
    start = int(np.searchsorted(ts, signal_fill_ms, side="left"))
    end = int(np.searchsorted(ts, min(end_ms, DEV_END_MS), side="left"))
    for j in range(start, end):
        if low[j] <= level:
            fill_ms = int(ts[j])
            if low[j] <= stop:
                return _result(
                    status="retest_same_bar_stop", r_multiple=-1.0,
                    fill_time_ms=fill_ms, outcome_time_ms=int(ts[j]) + 60_000,
                    entry_price=level, stop=stop,
                )
            # Do not award any target from the ambiguous fill bar.
            return _simulate_from_fill(
                ts, high, low, close, qv, first_bar=j + 1, fill_time_ms=fill_ms,
                entry=level, stop=stop, final_target=final_target, end_ms=end_ms,
                middle_target=middle_target, partial_fraction=partial_fraction,
                trail_to_level=level if trail_after_middle else None,
                break_even_trigger_r=break_even_trigger_r,
                inactivity_ms=inactivity_ms,
            )
        if high[j] >= final_target:
            return _result(status="missed_without_retest")
    return _result(status="no_retest")


def build_policy_labels(out_path: Path = POLICY_OUT) -> pd.DataFrame:
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    setups = setups.loc[
        setups["valid_entry"],
        TRADE_KEY + ["level", "culmination", "take", "stop"],
    ]
    labels = pd.read_parquet(RES / "labels_close.parquet")[
        TRADE_KEY + ["entry_price", "fill_time_ms"]
    ]
    signals = labels.merge(setups, on=TRADE_KEY, validate="one_to_one")
    rows: list[dict] = []
    for symbol, group in signals.groupby("symbol"):
        base = _load_1m(CACHE_1M / f"{symbol}.parquet")
        ts, high, low, close, qv = (
            base["timestamp"], base["high"], base["low"], base["close"], base["quote_volume"]
        )
        for row in group.itertuples(index=False):
            end_ms = int(row.fill_time_ms) + HORIZON_TF_BARS * TFS[row.tf] * 60_000
            first_bar = int(np.searchsorted(ts, int(row.fill_time_ms), side="left"))
            middle = float(row.culmination) if np.isfinite(row.culmination) else None
            policies = [("final_full", None, 0.0, False, None, None)]
            for target_fraction in STRUCTURAL_TARGET_FRACTIONS:
                structural_target = float(row.level) + target_fraction * (
                    float(row.take) - float(row.level)
                )
                policies.append(
                    (f"structure_{target_fraction:.2f}_full", structural_target, 1.0, False, None, None)
                )
                if target_fraction == 0.50:
                    for close_fraction in (0.50, 0.75):
                        base_policy = f"structure_0.50_part_{close_fraction:.2f}_runner_initial_stop"
                        policies.append((base_policy, structural_target, close_fraction, False, None, None))
                        policies.append(
                            (
                                f"structure_0.50_part_{close_fraction:.2f}_runner_level_stop",
                                structural_target,
                                close_fraction,
                                True,
                                None,
                                None,
                            )
                        )
                        for be_r in BREAK_EVEN_TRIGGER_R:
                            policies.append(
                                (
                                    f"{base_policy}_be_{be_r:.2f}",
                                    structural_target,
                                    close_fraction,
                                    False,
                                    be_r,
                                    None,
                                )
                            )
                            for inactive_h in INACTIVITY_HOURS:
                                policies.append(
                                    (
                                        f"{base_policy}_be_{be_r:.2f}_inactive_{inactive_h:.0f}h",
                                        structural_target,
                                        close_fraction,
                                        False,
                                        be_r,
                                        int(inactive_h * 3_600_000),
                                    )
                                )
            if middle is not None:
                policies.append(("culmination_full", middle, 1.0, False, None, None))
                for fraction in PARTIAL_FRACTIONS:
                    policies.append((f"culm_{fraction:.2f}_runner_initial_stop", middle, fraction, False, None, None))
                    policies.append((f"culm_{fraction:.2f}_runner_level_stop", middle, fraction, True, None, None))
            for entry_mode in ("close", "level_retest"):
                for policy, middle_target, fraction, trail, be_r, inactivity_ms in policies:
                    if entry_mode == "close":
                        result = _simulate_from_fill(
                            ts, high, low, close, qv, first_bar=first_bar,
                            fill_time_ms=int(row.fill_time_ms), entry=float(row.entry_price),
                            stop=float(row.stop), final_target=float(row.take), end_ms=end_ms,
                            middle_target=middle_target, partial_fraction=fraction,
                            trail_to_level=float(row.level) if trail else None,
                            break_even_trigger_r=be_r,
                            inactivity_ms=inactivity_ms,
                        )
                    else:
                        result = _simulate_retest(
                            ts, high, low, close, qv, signal_fill_ms=int(row.fill_time_ms),
                            level=float(row.level), stop=float(row.stop), final_target=float(row.take),
                            end_ms=end_ms, middle_target=middle_target,
                            partial_fraction=fraction, trail_after_middle=trail,
                            break_even_trigger_r=be_r,
                            inactivity_ms=inactivity_ms,
                        )
                    result.update(
                        **{key: getattr(row, key) for key in TRADE_KEY},
                        entry_mode=entry_mode,
                        exit_policy=policy,
                    )
                    rows.append(result)
    result = pd.DataFrame(rows)
    resolved = result["outcome_time_ms"].notna()
    if not (result.loc[resolved, "outcome_time_ms"] <= DEV_END_MS).all():
        raise ValueError("execution policy crossed frozen IS boundary")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    print(f"saved {len(result)} causal policy rows -> {out_path}", flush=True)
    return result


def evaluate_policy_surface(
    policy_path: Path = POLICY_OUT,
    out_path: Path = SURFACE_OUT,
) -> dict:
    policies = pd.read_parquet(policy_path)
    scores = pd.read_parquet(RES / "ev_walkforward.parquet")
    frame = policies.merge(scores, on=TRADE_KEY, how="inner", validate="many_to_one")
    frame = frame[frame["r_multiple"].notna() & frame["dist_stop"].gt(0)].copy()
    rows: list[dict] = []
    for (entry_mode, exit_policy), group in frame.groupby(["entry_mode", "exit_policy"]):
        for quantile in (0.65, 0.70, 0.75, 0.80, 0.85):
            selected = _causal_quantile_selection(group, quantile)
            ledger, rejects = _ledger(
                group.loc[selected], risk_pct=0.02, max_open=3, slip_pct=0.005
            )
            metrics = _metrics(ledger)
            if not ledger.empty:
                months = _calendar_stats(ledger, "M")
                positive_day_pct = 100 * metrics["positive_days"] / metrics["trading_days"]
                positive_months = sum(row["net_r"] > 0 for row in months)
                monthly_net_r = np.array([row["net_r"] for row in months], dtype=float)
            else:
                months, positive_day_pct, positive_months = [], np.nan, 0
                monthly_net_r = np.array([], dtype=float)
            row = {
                "entry_mode": entry_mode,
                "exit_policy": exit_policy,
                "prior_score_quantile": quantile,
                **metrics,
                "positive_day_pct": positive_day_pct,
                "positive_months": positive_months,
                "months": len(months),
                "month_coverage_pct": 100 * len(months) / EXPECTED_FORWARD_MONTHS,
                "positive_month_pct": 100 * positive_months / EXPECTED_FORWARD_MONTHS,
                "monthly_net_r_min": float(monthly_net_r.min()) if len(monthly_net_r) else np.nan,
                "monthly_net_r_median": float(np.median(monthly_net_r)) if len(monthly_net_r) else np.nan,
                "monthly_net_r_std": float(monthly_net_r.std()) if len(monthly_net_r) else np.nan,
                "all_months_positive": bool(
                    len(months) == EXPECTED_FORWARD_MONTHS
                    and positive_months == EXPECTED_FORWARD_MONTHS
                ),
                "rejects": rejects,
            }
            row["quality_gates_passed"] = {
                "win_rate_50": metrics.get("win_rate_pct", 0) >= 50,
                "positive_days_70": positive_day_pct >= 70,
                "all_months_positive": row["all_months_positive"],
                "top_removal_30": metrics.get("top_winners_to_zero_pct_of_trades", 0) >= 30,
                "drawdown_15_20": -20 <= metrics.get("max_drawdown_pct", -100) <= -15,
            }
            row["gate_count"] = sum(row["quality_gates_passed"].values())
            rows.append(row)
    rows.sort(
        key=lambda row: (
            row["positive_month_pct"],
            row["month_coverage_pct"],
            row["monthly_net_r_min"],
            row.get("top_winners_to_zero_pct_of_trades", 0),
            row.get("max_drawdown_pct", -100),
            row.get("profit_factor") or 0,
        ),
        reverse=True,
    )
    report = {
        "scope": "IS diagnostic; frozen baseline expected-R scores; no policy-specific refit",
        "policy_rows": len(frame),
        "surface": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(rows)} policy/selection variants -> {out_path}", flush=True)
    return report


if __name__ == "__main__":
    build_policy_labels()
    evaluate_policy_surface()
