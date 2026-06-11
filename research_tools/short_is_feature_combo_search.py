"""IS-only feature combo search for structural short candidates.

The script searches only the first anchored period of the current 365d artifact
and deliberately leaves the second half untouched.  It separates:

- entry-known features available by the failed-retest entry;
- post-entry management features available only after 1/3/5/10/15 minutes.

The goal is not to prove an edge, but to build a richer candidate catalogue
for frozen OOS validation.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.short_wfa_plateau_engine import (
    FAMILY_GROUPS,
    GUARDS,
    SESSION_FILTERS,
    STOP_MODELS,
    _apply_guard,
    _metrics,
    _score,
)


BASE_POLICIES = ("tp075_full", "tp1_full", "tp1_be075")


@dataclass(frozen=True)
class FeatureMask:
    name: str
    availability: str
    family: str
    mask: pd.Series


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def _str_contains(frame: pd.DataFrame, col: str, pattern: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[col].astype(str).str.contains(pattern, case=False, na=False)


def _load_enriched(failed_dir: Path, split_days: int) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    trades = pd.read_csv(failed_dir / "short_structural_075_path_replay_trades.csv")
    trades = trades[trades["status"].eq("closed") & trades["policy"].isin(BASE_POLICIES)].copy()
    trades["date_ts"] = pd.to_datetime(trades["date"], errors="coerce", utc=True).dt.tz_convert(None)
    start = pd.to_datetime(trades["date_ts"].min())
    split = start + pd.Timedelta(days=int(split_days))
    trades = trades[(trades["date_ts"] >= start) & (trades["date_ts"] < split)].copy()

    signal_cols = [
        "signal_id",
        "confirm_close_position",
        "confirm_return_pct",
        "confirm_quote_ratio_vs_norm",
        "confirm_trade_ratio_vs_norm",
        "confirm_taker_buy_share",
        "confirm_taker_buy_share_norm",
        "confirm_taker_buy_vs_norm_pct",
        "confirm_taker_buy_norm_ratio",
        "confirm_taker_buy_below_norm_flag",
        "pre_confirm_return_from_seed_close_pct",
        "post_pump_preconfirm_quote_top1_share",
        "post_pump_preconfirm_trade_top1_share",
        "post_pump_preconfirm_distribution_bucket",
        "seed_1m_quote_top1_share",
        "seed_1m_trade_top1_share",
        "seed_1m_distribution_bucket",
        "pump_high_timing_pct",
        "seed_first_half_return_pct",
        "seed_second_half_return_pct",
        "seed_last_2m_return_pct",
        "has_lower_high_before_break",
    ]
    signals_path = failed_dir / "failed_pump_short_signals.csv"
    signals = pd.read_csv(signals_path, usecols=lambda c: c in set(signal_cols))
    signals = signals.drop_duplicates("signal_id", keep="last")
    enriched = trades.merge(signals, on="signal_id", how="left", suffixes=("", "_signal"))
    return enriched.reset_index(drop=True), start, split


def _feature_masks(df: pd.DataFrame) -> list[FeatureMask]:
    masks: list[FeatureMask] = []

    def add(name: str, availability: str, family: str, mask: pd.Series) -> None:
        masks.append(FeatureMask(name, availability, family, mask.fillna(False).astype(bool)))

    # Entry-known structural shape.
    risk = _num(df, "initial_risk_pct")
    break_depth = _num(df, "structural_break_depth_pct")
    retest_dist = _num(df, "failed_retest_distance_pct")
    minutes_break = _num(df, "minutes_from_seed_to_break")
    add("risk_1_4pct", "entry_known", "risk", risk.between(0.01, 0.04, inclusive="both"))
    add("risk_2_6pct", "entry_known", "risk", risk.between(0.02, 0.06, inclusive="both"))
    add("risk_3_8pct", "entry_known", "risk", risk.between(0.03, 0.08, inclusive="both"))
    add("break_ge_1p5pct", "entry_known", "structure", break_depth >= 0.015)
    add("break_ge_2pct", "entry_known", "structure", break_depth >= 0.020)
    add("break_ge_3pct", "entry_known", "structure", break_depth >= 0.030)
    add("retest_ge_0p8pct", "entry_known", "structure", retest_dist >= 0.008)
    add("retest_ge_1p2pct", "entry_known", "structure", retest_dist >= 0.012)
    add("retest_ge_1p5pct", "entry_known", "structure", retest_dist >= 0.015)
    add("break_le_10m", "entry_known", "timing", minutes_break <= 10)
    add("break_le_15m", "entry_known", "timing", minutes_break <= 15)
    add("break_le_20m", "entry_known", "timing", minutes_break <= 20)
    add("break_5_20m", "entry_known", "timing", minutes_break.between(5, 20, inclusive="both"))
    add("post_oneprint", "entry_known", "distribution", _str_contains(df, "post_dist", "one_print"))
    add("post_distributed", "entry_known", "distribution", _str_contains(df, "post_dist", "distributed"))
    add("seed_oneprint", "entry_known", "distribution", _str_contains(df, "seed_dist", "one_print"))
    add("seed_distributed", "entry_known", "distribution", _str_contains(df, "seed_dist", "distributed"))

    # Confirm candle flow/acceptance known before entry.
    confirm_pos = _num(df, "confirm_close_position")
    confirm_ret = _num(df, "confirm_return_pct")
    confirm_quote = _num(df, "confirm_quote_ratio_vs_norm")
    confirm_trade = _num(df, "confirm_trade_ratio_vs_norm")
    taker = _num(df, "confirm_taker_buy_share")
    taker_vs = _num(df, "confirm_taker_buy_vs_norm_pct")
    pre_ret = _num(df, "pre_confirm_return_from_seed_close_pct")
    add("confirm_close_low_third", "entry_known", "confirm_price", confirm_pos <= 0.33)
    add("confirm_close_low_half", "entry_known", "confirm_price", confirm_pos <= 0.50)
    add("confirm_return_le_0", "entry_known", "confirm_price", confirm_ret <= 0)
    add("confirm_return_le_minus_1pct", "entry_known", "confirm_price", confirm_ret <= -0.01)
    add("preconfirm_return_le_0", "entry_known", "confirm_price", pre_ret <= 0)
    add("preconfirm_return_le_2pct", "entry_known", "confirm_price", pre_ret <= 0.02)
    add("confirm_quote_ge_2x", "entry_known", "flow", confirm_quote >= 2)
    add("confirm_quote_ge_5x", "entry_known", "flow", confirm_quote >= 5)
    add("confirm_trade_ge_2x", "entry_known", "flow", confirm_trade >= 2)
    add("confirm_trade_ge_5x", "entry_known", "flow", confirm_trade >= 5)
    add("taker_le_45", "entry_known", "buyer_pressure", taker <= 0.45)
    add("taker_le_50", "entry_known", "buyer_pressure", taker <= 0.50)
    add("taker_45_55", "entry_known", "buyer_pressure", taker.between(0.45, 0.55, inclusive="both"))
    add("taker_ge_55", "entry_known", "buyer_pressure", taker >= 0.55)
    add("taker_below_norm", "entry_known", "buyer_pressure", df.get("confirm_taker_buy_below_norm_flag", False).astype(str).str.lower().eq("true"))
    add("taker_vs_norm_le_0", "entry_known", "buyer_pressure", taker_vs <= 0)

    # Pre-confirm concentration/exhaustion.
    post_qtop = _num(df, "post_pump_preconfirm_quote_top1_share")
    post_ttop = _num(df, "post_pump_preconfirm_trade_top1_share")
    seed_qtop = _num(df, "seed_1m_quote_top1_share")
    seed_ttop = _num(df, "seed_1m_trade_top1_share")
    high_timing = _num(df, "pump_high_timing_pct")
    seed_last2 = _num(df, "seed_last_2m_return_pct")
    add("post_preconfirm_top1_ge60", "entry_known", "concentration", (post_qtop >= 0.60) | (post_ttop >= 0.60))
    add("post_preconfirm_top1_le35", "entry_known", "concentration", (post_qtop <= 0.35) & (post_ttop <= 0.35))
    add("seed_top1_ge60", "entry_known", "concentration", (seed_qtop >= 0.60) | (seed_ttop >= 0.60))
    add("seed_top1_le35", "entry_known", "concentration", (seed_qtop <= 0.35) & (seed_ttop <= 0.35))
    add("pump_high_early", "entry_known", "verticality", high_timing <= 0.25)
    add("pump_high_late", "entry_known", "verticality", high_timing >= 0.75)
    add("seed_last2_negative", "entry_known", "verticality", seed_last2 < 0)
    add("seed_last2_positive", "entry_known", "verticality", seed_last2 > 0)

    # Post-entry management state. These must not be used for initial entry.
    for minute in (3, 5, 10, 15):
        mfe = _num(df, f"m{minute}_mfe_r")
        mae = _num(df, f"m{minute}_mae_r")
        close = _num(df, f"m{minute}_close_r")
        red = _num(df, f"m{minute}_red_close_share")
        take = _num(df, f"m{minute}_taker_buy_share")
        add(f"m{minute}_mfe_ge_025", "post_entry_management", "downside_acceptance", mfe >= 0.25)
        add(f"m{minute}_mfe_ge_050", "post_entry_management", "downside_acceptance", mfe >= 0.50)
        add(f"m{minute}_close_r_gt_0", "post_entry_management", "downside_acceptance", close > 0)
        add(f"m{minute}_close_r_ge_025", "post_entry_management", "downside_acceptance", close >= 0.25)
        add(f"m{minute}_red_share_ge_60", "post_entry_management", "downside_acceptance", red >= 0.60)
        add(f"m{minute}_mae_le_025", "post_entry_management", "adverse_control", mae <= 0.25)
        add(f"m{minute}_mae_le_050", "post_entry_management", "adverse_control", mae <= 0.50)
        add(f"m{minute}_taker_le_50", "post_entry_management", "buyer_pressure_after_entry", take <= 0.50)
    return masks


def _candidate_ledger(base: pd.DataFrame, family_group: str, session_filter: str, stop_model: str, base_policy: str, guard_name: str) -> pd.DataFrame:
    guard_by_name = {guard.name: guard for guard in GUARDS}
    guard = guard_by_name[guard_name]
    families = FAMILY_GROUPS[family_group]
    frame = base[base["family"].isin(families) & base["stop_model"].eq(stop_model) & base["policy"].eq(base_policy)].copy()
    sessions = SESSION_FILTERS[session_filter]
    if sessions is not None:
        frame = frame[frame["session_bucket"].isin(sessions)].copy()
    if frame.empty:
        return frame
    frame["_net_r"] = _apply_guard(frame, base_policy, guard)
    family_rank = {family: idx for idx, family in enumerate(families)}
    frame["_family_rank"] = frame["family"].map(family_rank).fillna(999).astype(int)
    return (
        frame.sort_values(["entry_timestamp_ms", "signal_id", "_family_rank"])
        .drop_duplicates(["signal_id", "stop_model"], keep="first")
        .copy()
    )


def _summarize_candidate(frame: pd.DataFrame, *, feature_name: str, availability: str, feature_family: str) -> dict[str, object]:
    metrics = _metrics(frame, frame["_net_r"])
    out = {**metrics}
    out["feature_name"] = feature_name
    out["feature_availability"] = availability
    out["feature_family"] = feature_family
    out["score"] = _score(metrics)
    out["candidate_pass"] = bool(
        out["trades"] >= 20
        and out["symbols"] >= 15
        and out["days"] >= 15
        and out["avg_r"] > 0
        and out["median_r"] > 0
        and out["positive_day_rate"] >= 0.50
        and out["top_trade_independence_pct"] >= 0.05
        and out["top_symbol_independence_pct"] >= 0.05
    )
    return out


def _search(base: pd.DataFrame, masks: list[FeatureMask]) -> pd.DataFrame:
    rows = []
    family_groups = list(FAMILY_GROUPS)
    session_filters = list(SESSION_FILTERS)
    guards = ["no_guard", "m5_mfe025", "m5_mfe050", "m10_mfe025", "m10_mfe050", "m15_mfe050"]
    for family_group in family_groups:
        for session_filter in session_filters:
            for stop_model in STOP_MODELS:
                for base_policy in BASE_POLICIES:
                    for guard_name in guards:
                        ledger = _candidate_ledger(base, family_group, session_filter, stop_model, base_policy, guard_name)
                        if len(ledger) < 20:
                            continue
                        base_row = _summarize_candidate(
                            ledger,
                            feature_name="none",
                            availability="base_surface",
                            feature_family="none",
                        )
                        base_row.update(
                            {
                                "family_group": family_group,
                                "session_filter": session_filter,
                                "stop_model": stop_model,
                                "base_policy": base_policy,
                                "guard": guard_name,
                            }
                        )
                        rows.append(base_row)

                        single_results: list[tuple[FeatureMask, dict[str, object], pd.DataFrame]] = []
                        for feature in masks:
                            sub = ledger.loc[feature.mask.reindex(ledger.index, fill_value=False)].copy()
                            if len(sub) < 20:
                                continue
                            result = _summarize_candidate(
                                sub,
                                feature_name=feature.name,
                                availability=feature.availability,
                                feature_family=feature.family,
                            )
                            result.update(
                                {
                                    "family_group": family_group,
                                    "session_filter": session_filter,
                                    "stop_model": stop_model,
                                    "base_policy": base_policy,
                                    "guard": guard_name,
                                }
                            )
                            rows.append(result)
                            if result["candidate_pass"]:
                                single_results.append((feature, result, sub))

                        top_singles = sorted(single_results, key=lambda item: float(item[1]["score"]), reverse=True)[:12]
                        for i in range(len(top_singles)):
                            f1, _, _ = top_singles[i]
                            for j in range(i + 1, len(top_singles)):
                                f2, _, _ = top_singles[j]
                                if f1.family == f2.family:
                                    continue
                                availability = (
                                    "post_entry_management"
                                    if "post_entry_management" in {f1.availability, f2.availability}
                                    else "entry_known"
                                )
                                mask = f1.mask.reindex(ledger.index, fill_value=False) & f2.mask.reindex(ledger.index, fill_value=False)
                                sub = ledger.loc[mask].copy()
                                if len(sub) < 20:
                                    continue
                                result = _summarize_candidate(
                                    sub,
                                    feature_name=f"{f1.name} & {f2.name}",
                                    availability=availability,
                                    feature_family=f"{f1.family}+{f2.family}",
                                )
                                result.update(
                                    {
                                        "family_group": family_group,
                                        "session_filter": session_filter,
                                        "stop_model": stop_model,
                                        "base_policy": base_policy,
                                        "guard": guard_name,
                                    }
                                )
                                rows.append(result)
    return pd.DataFrame(rows)


def _feature_leaders(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return result
    work = result[result["feature_name"].ne("none")].copy()
    grouped = (
        work.groupby(["feature_name", "feature_availability", "feature_family"], dropna=False)
        .agg(
            rows=("feature_name", "size"),
            pass_rows=("candidate_pass", "sum"),
            median_score=("score", "median"),
            max_score=("score", "max"),
            median_avg_r=("avg_r", "median"),
            median_median_r=("median_r", "median"),
            median_top_trade=("top_trade_independence_pct", "median"),
        )
        .reset_index()
    )
    return grouped.sort_values(["pass_rows", "median_score", "max_score"], ascending=[False, False, False])


def _write_report(out_dir: Path, result: pd.DataFrame, leaders: pd.DataFrame, *, start: pd.Timestamp, split: pd.Timestamp) -> None:
    report_path = out_dir / "short_is_feature_combo_report.md"
    cols = [
        "candidate_pass",
        "family_group",
        "session_filter",
        "stop_model",
        "base_policy",
        "guard",
        "feature_name",
        "feature_availability",
        "trades",
        "avg_r",
        "median_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "score",
        "sum_r",
    ]
    leader_cols = [
        "feature_name",
        "feature_availability",
        "feature_family",
        "rows",
        "pass_rows",
        "median_score",
        "median_avg_r",
        "median_median_r",
        "median_top_trade",
    ]
    entry = result[result["feature_availability"].isin(["base_surface", "entry_known"])].copy()
    mgmt = result[result["feature_availability"].eq("post_entry_management")].copy()
    lines = [
        "# IS Feature Combo Search",
        "",
        "Search period only:",
        "",
        "```text",
        f"IS: {start.date().isoformat()} -> {split.date().isoformat()}",
        f"candidate rows: {len(result)}",
        f"passes: {int(result['candidate_pass'].sum()) if not result.empty else 0}",
        f"entry/base passes: {int(entry['candidate_pass'].sum()) if not entry.empty else 0}",
        f"post-entry-management passes: {int(mgmt['candidate_pass'].sum()) if not mgmt.empty else 0}",
        "```",
        "",
        "Top entry-known / base rows:",
        "",
        "```text",
        entry[cols].sort_values(["candidate_pass", "score", "sum_r"], ascending=[False, False, False]).head(40).to_string(index=False) if not entry.empty else "EMPTY",
        "```",
        "",
        "Top post-entry management rows:",
        "",
        "```text",
        mgmt[cols].sort_values(["candidate_pass", "score", "sum_r"], ascending=[False, False, False]).head(40).to_string(index=False) if not mgmt.empty else "EMPTY",
        "```",
        "",
        "Feature leaders:",
        "",
        "```text",
        leaders[leader_cols].head(60).to_string(index=False) if not leaders.empty else "EMPTY",
        "```",
        "",
        "Boundary:",
        "",
        "Post-entry management features are not entry filters. They can only be",
        "used after the trade is already open, for hold/exit decisions.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(*, failed_dir: Path, split_days: int) -> dict[str, object]:
    base, start, split = _load_enriched(failed_dir, split_days)
    masks = _feature_masks(base)
    result = _search(base, masks)
    leaders = _feature_leaders(result)
    out_dir = failed_dir
    result_path = out_dir / "short_is_feature_combo_screen.csv"
    leaders_path = out_dir / "short_is_feature_combo_feature_leaders.csv"
    result.to_csv(result_path, index=False)
    leaders.to_csv(leaders_path, index=False)
    _write_report(out_dir, result, leaders, start=start, split=split)
    return {
        "is_start": start.date().isoformat(),
        "split_date": split.date().isoformat(),
        "base_rows": len(base),
        "feature_masks": len(masks),
        "candidate_rows": len(result),
        "passes": int(result["candidate_pass"].sum()) if not result.empty else 0,
        "entry_base_passes": int(result[result["feature_availability"].isin(["base_surface", "entry_known"])]["candidate_pass"].sum()) if not result.empty else 0,
        "management_passes": int(result[result["feature_availability"].eq("post_entry_management")]["candidate_pass"].sum()) if not result.empty else 0,
        "result_path": str(result_path),
        "leaders_path": str(leaders_path),
        "report_path": str(out_dir / "short_is_feature_combo_report.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    parser.add_argument("--split-days", type=int, default=180)
    args = parser.parse_args()
    result = run(failed_dir=args.failed_dir, split_days=args.split_days)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
