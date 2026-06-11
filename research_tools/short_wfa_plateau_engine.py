"""Walk-forward and plateau screen for structural failed-retest shorts.

This script consumes the 1m path replay artifact produced by
`short_structural_075_path_replay.py`.  It intentionally tests a bounded
parameter surface around one trading idea instead of searching every possible
threshold:

failed retest / lower high -> short, SL above retest high, no profit exit
before 0.75R, and optional no-continuation guard after 3-15 minutes.

The output is research-only.  Daily rolling OOS windows overlap, so WFA metrics
are window diagnostics rather than independent trade counts.
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

from research_tools.session_edge_workbench import _top_remove_pct_to_negative
from research_tools.short_enhancement_strategy_screen import _max_drawdown


FEE_RATE = 0.0004
EXIT_SLIPPAGE_PCT = 0.0005


FAMILY_GROUPS: dict[str, tuple[str, ...]] = {
    "fast_deep": ("fast_deep_break_taker_above",),
    "A_oneprint": ("A_post_oneprint_break1p5_retest0p8",),
    "deep08": ("deep_break_retest0p8",),
    "deep04": ("deep_break_retest0p4",),
    "A_plus_fast": ("A_post_oneprint_break1p5_retest0p8", "fast_deep_break_taker_above"),
    "A_fast_deep08": (
        "A_post_oneprint_break1p5_retest0p8",
        "fast_deep_break_taker_above",
        "deep_break_retest0p8",
    ),
}

SESSION_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "non_us": ("asia_only", "asia_europe_overlap", "europe_only", "europe_us_overlap", "off_session"),
    "europe_or_off": ("europe_only", "off_session"),
    "not_asia_overlap": ("asia_only", "europe_only", "europe_us_overlap", "us_only", "off_session"),
}

BASE_POLICIES = ("tp075_full", "tp1_full", "tp1_be075")
STOP_MODELS = ("existing_stop", "last_lower_high", "recent5", "recent10")


@dataclass(frozen=True)
class GuardSpec:
    name: str
    minute: int
    min_mfe_r: float | None = None
    min_close_r: float | None = None


GUARDS: tuple[GuardSpec, ...] = (
    GuardSpec("no_guard", 0, None, None),
    GuardSpec("m3_mfe025", 3, 0.25, None),
    GuardSpec("m3_mfe025_close0", 3, 0.25, 0.0),
    GuardSpec("m5_mfe025", 5, 0.25, None),
    GuardSpec("m5_mfe050", 5, 0.50, None),
    GuardSpec("m5_close0", 5, None, 0.0),
    GuardSpec("m5_mfe025_close0", 5, 0.25, 0.0),
    GuardSpec("m5_mfe050_close0", 5, 0.50, 0.0),
    GuardSpec("m10_mfe025", 10, 0.25, None),
    GuardSpec("m10_mfe050", 10, 0.50, None),
    GuardSpec("m10_mfe025_close0", 10, 0.25, 0.0),
    GuardSpec("m10_close0", 10, None, 0.0),
    GuardSpec("m15_mfe050", 15, 0.50, None),
)

WFA_MATRIX = ((60, 7), (60, 14), (90, 7), (90, 14), (90, 30), (120, 14), (120, 30))


def _guard_exit_r(frame: pd.DataFrame, minute: int) -> pd.Series:
    close_r = pd.to_numeric(frame[f"m{minute}_close_r"], errors="coerce")
    entry = pd.to_numeric(frame["entry_price"], errors="coerce")
    risk_abs = entry * pd.to_numeric(frame["initial_risk_pct"], errors="coerce")
    exit_mid = entry - close_r * risk_abs
    fill = exit_mid * (1.0 + EXIT_SLIPPAGE_PCT)
    fees_r = FEE_RATE * (entry + fill) / risk_abs
    return (entry - fill) / risk_abs - fees_r


def _apply_guard(frame: pd.DataFrame, base_policy: str, guard: GuardSpec) -> pd.Series:
    net = pd.to_numeric(frame["net_r"], errors="coerce").copy()
    if guard.minute <= 0:
        return net
    mfe_col = f"m{guard.minute}_mfe_r"
    close_col = f"m{guard.minute}_close_r"
    if mfe_col not in frame.columns or close_col not in frame.columns:
        return net
    fail = pd.Series(False, index=frame.index)
    if guard.min_mfe_r is not None:
        fail |= pd.to_numeric(frame[mfe_col], errors="coerce") < float(guard.min_mfe_r)
    if guard.min_close_r is not None:
        fail |= pd.to_numeric(frame[close_col], errors="coerce") <= float(guard.min_close_r)
    hold_ok = pd.to_numeric(frame["hold_minutes"], errors="coerce") >= float(guard.minute)
    target_col = "time_to_1r_min" if base_policy in {"tp1_full", "tp1_be075"} else "time_to_075r_min"
    target_time = pd.to_numeric(frame[target_col], errors="coerce")
    target_not_done = target_time.isna() | (target_time > float(guard.minute))
    fail &= hold_ok & target_not_done
    if fail.any():
        guarded = _guard_exit_r(frame, guard.minute)
        net.loc[fail] = guarded.loc[fail]
    return net


def _top_independence(values: pd.Series) -> tuple[float, int, int]:
    return _top_remove_pct_to_negative(pd.to_numeric(values, errors="coerce").dropna())


def _metrics(frame: pd.DataFrame, values: pd.Series) -> dict[str, float | int]:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return {
            "trades": 0,
            "symbols": 0,
            "days": 0,
            "avg_r": np.nan,
            "median_r": np.nan,
            "sum_r": 0.0,
            "win_rate": np.nan,
            "positive_day_rate": np.nan,
            "top_trade_independence_pct": 0.0,
            "top_symbol_independence_pct": 0.0,
            "max_drawdown_r": np.nan,
        }
    work = frame.loc[vals.index].copy()
    work["_r"] = vals
    daily = work.groupby("date")["_r"].sum()
    trade_pct, removed_trades, winning_trades = _top_independence(vals)
    symbol_pct, removed_symbols, winning_symbols = _top_independence(work.groupby("symbol")["_r"].sum())
    return {
        "trades": int(len(vals)),
        "symbols": int(work["symbol"].nunique()),
        "days": int(work["date"].nunique()),
        "avg_r": float(vals.mean()),
        "median_r": float(vals.median()),
        "sum_r": float(vals.sum()),
        "win_rate": float((vals > 0).mean()),
        "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
        "top_trade_independence_pct": float(trade_pct),
        "removed_top_trades_to_negative": int(removed_trades),
        "winning_trades": int(winning_trades),
        "top_symbol_independence_pct": float(symbol_pct),
        "removed_top_symbols_to_negative": int(removed_symbols),
        "winning_symbols": int(winning_symbols),
        "max_drawdown_r": float(_max_drawdown(work.sort_values("entry_timestamp_ms")["_r"])),
    }


def _passes(metrics: dict[str, float | int], *, min_trades: int) -> bool:
    return bool(
        int(metrics.get("trades", 0)) >= min_trades
        and float(metrics.get("avg_r", -999.0)) > 0.0
        and float(metrics.get("median_r", -999.0)) > 0.0
        and float(metrics.get("positive_day_rate", 0.0)) >= 0.50
        and float(metrics.get("top_trade_independence_pct", 0.0)) >= 0.05
        and float(metrics.get("top_symbol_independence_pct", 0.0)) >= 0.05
    )


def _score(metrics: dict[str, float | int]) -> float:
    avg = np.tanh(float(metrics.get("avg_r", 0.0)) / 0.20)
    med = np.tanh(float(metrics.get("median_r", 0.0)) / 0.20)
    day = float(metrics.get("positive_day_rate", 0.0))
    trade_ind = min(float(metrics.get("top_trade_independence_pct", 0.0)), 0.50) / 0.50
    sym_ind = min(float(metrics.get("top_symbol_independence_pct", 0.0)), 0.50) / 0.50
    sample = min(float(metrics.get("trades", 0)) / 250.0, 1.0)
    dd = min(abs(float(metrics.get("max_drawdown_r", 0.0))) / 20.0, 1.0)
    return float(0.18 * avg + 0.20 * med + 0.16 * day + 0.18 * trade_ind + 0.12 * sym_ind + 0.08 * sample - 0.12 * dd)


def _candidate_ledger(base: pd.DataFrame, family_group: str, session_filter: str, stop_model: str, base_policy: str, guard: GuardSpec) -> pd.DataFrame:
    families = FAMILY_GROUPS[family_group]
    frame = base[base["family"].isin(families) & base["stop_model"].eq(stop_model) & base["policy"].eq(base_policy)].copy()
    sessions = SESSION_FILTERS[session_filter]
    if sessions is not None:
        frame = frame[frame["session_bucket"].isin(sessions)].copy()
    if frame.empty:
        return frame
    frame["_net_r"] = _apply_guard(frame, base_policy, guard)
    # If a signal belongs to several family labels in a combined family group,
    # trade it once.  Prefer the narrower family order listed in FAMILY_GROUPS.
    family_rank = {family: idx for idx, family in enumerate(families)}
    frame["_family_rank"] = frame["family"].map(family_rank).fillna(999).astype(int)
    frame = frame.sort_values(["entry_timestamp_ms", "signal_id", "_family_rank", "stop_model"])
    frame = frame.drop_duplicates(["signal_id", "stop_model", "base_policy_key"], keep="first") if "base_policy_key" in frame else frame.drop_duplicates(["signal_id", "stop_model"], keep="first")
    return frame


def _build_full_screen(base: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows = []
    ledgers: dict[str, pd.DataFrame] = {}
    for family_group in FAMILY_GROUPS:
        for session_filter in SESSION_FILTERS:
            for stop_model in STOP_MODELS:
                for base_policy in BASE_POLICIES:
                    for guard_idx, guard in enumerate(GUARDS):
                        ledger = _candidate_ledger(base, family_group, session_filter, stop_model, base_policy, guard)
                        if ledger.empty:
                            continue
                        candidate_id = "|".join([family_group, session_filter, stop_model, base_policy, guard.name])
                        metrics = _metrics(ledger, ledger["_net_r"])
                        row = {
                            **metrics,
                            "candidate_id": candidate_id,
                            "family_group": family_group,
                            "session_filter": session_filter,
                            "stop_model": stop_model,
                            "base_policy": base_policy,
                            "guard": guard.name,
                            "guard_idx": guard_idx,
                            "guard_minute": guard.minute,
                            "guard_min_mfe_r": np.nan if guard.min_mfe_r is None else guard.min_mfe_r,
                            "guard_min_close_r": np.nan if guard.min_close_r is None else guard.min_close_r,
                        }
                        row["full_pass"] = _passes(metrics, min_trades=25)
                        row["score"] = _score(metrics)
                        rows.append(row)
                        ledgers[candidate_id] = ledger
    screen = pd.DataFrame(rows)
    if screen.empty:
        return screen, ledgers
    screen["plateau_neighbor_passes"] = 0
    surface_cols = ["family_group", "session_filter", "stop_model", "base_policy"]
    for _, idxs in screen.groupby(surface_cols).groups.items():
        sub = screen.loc[list(idxs), ["guard_idx", "full_pass"]].copy()
        pass_by_idx = dict(zip(sub["guard_idx"], sub["full_pass"]))
        for idx in idxs:
            guard_idx = int(screen.at[idx, "guard_idx"])
            count = sum(bool(pass_by_idx.get(other, False)) for other in (guard_idx - 1, guard_idx, guard_idx + 1))
            screen.at[idx, "plateau_neighbor_passes"] = int(count)
    screen["plateau_pass"] = screen["full_pass"].astype(bool) & (screen["plateau_neighbor_passes"] >= 2)
    return screen.sort_values(["plateau_pass", "score", "sum_r"], ascending=[False, False, False]), ledgers


def _window_metrics(ledger: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | int]:
    mask = (ledger["date_ts"] >= start) & (ledger["date_ts"] < end)
    sub = ledger.loc[mask]
    return _metrics(sub, sub["_net_r"]) if not sub.empty else _metrics(sub, pd.Series(dtype=float))


def _quick_window_metrics(ledger: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | int]:
    mask = (ledger["date_ts"] >= start) & (ledger["date_ts"] < end)
    sub = ledger.loc[mask]
    vals = pd.to_numeric(sub["_net_r"], errors="coerce").dropna()
    if vals.empty:
        return {
            "trades": 0,
            "days": 0,
            "avg_r": np.nan,
            "median_r": np.nan,
            "sum_r": 0.0,
            "win_rate": np.nan,
            "positive_day_rate": np.nan,
        }
    work = sub.loc[vals.index].copy()
    work["_r"] = vals
    daily = work.groupby("date")["_r"].sum()
    return {
        "trades": int(len(vals)),
        "days": int(work["date"].nunique()),
        "avg_r": float(vals.mean()),
        "median_r": float(vals.median()),
        "sum_r": float(vals.sum()),
        "win_rate": float((vals > 0).mean()),
        "positive_day_rate": float((daily > 0).mean()) if len(daily) else np.nan,
    }


def _quick_train_pass(metrics: dict[str, float | int], *, min_trades: int) -> bool:
    return bool(
        int(metrics.get("trades", 0)) >= min_trades
        and float(metrics.get("avg_r", -999.0)) > 0.0
        and float(metrics.get("median_r", -999.0)) > 0.0
        and float(metrics.get("positive_day_rate", 0.0)) >= 0.50
    )


def _wfa_fixed(screen: pd.DataFrame, ledgers: dict[str, pd.DataFrame], *, train_days: int, oos_days: int, max_candidates: int = 350) -> pd.DataFrame:
    rows = []
    candidates = screen[screen["plateau_pass"].astype(bool)].copy()
    if candidates.empty:
        candidates = screen[screen["full_pass"].astype(bool)].copy()
    candidates = candidates.sort_values(["plateau_pass", "score"], ascending=[False, False]).head(max_candidates)
    all_dates = []
    for candidate_id in candidates["candidate_id"]:
        ledger = ledgers[candidate_id]
        if not ledger.empty:
            all_dates.extend(ledger["date_ts"].tolist())
    if not all_dates:
        return pd.DataFrame()
    min_date = min(all_dates)
    max_date = max(all_dates)
    anchors = pd.date_range(min_date + pd.Timedelta(days=train_days), max_date - pd.Timedelta(days=oos_days), freq="D")
    for _, cand in candidates.iterrows():
        ledger = ledgers[cand["candidate_id"]]
        window_rows = []
        for anchor in anchors:
            train = _quick_window_metrics(ledger, anchor - pd.Timedelta(days=train_days), anchor)
            if not _quick_train_pass(train, min_trades=max(12, min(30, train_days // 3))):
                continue
            oos = _quick_window_metrics(ledger, anchor, anchor + pd.Timedelta(days=oos_days))
            if int(oos.get("trades", 0)) <= 0:
                continue
            window_rows.append(
                {
                    "anchor": anchor.date().isoformat(),
                    "train_sum_r": train["sum_r"],
                    "train_avg_r": train["avg_r"],
                    "train_median_r": train["median_r"],
                    "train_trades": train["trades"],
                    "oos_sum_r": oos["sum_r"],
                    "oos_avg_r": oos["avg_r"],
                    "oos_median_r": oos["median_r"],
                    "oos_trades": oos["trades"],
                    "oos_positive": float(oos["sum_r"]) > 0,
                }
            )
        if not window_rows:
            continue
        wf = pd.DataFrame(window_rows)
        row = cand.to_dict()
        row.update(
            {
                "train_days": train_days,
                "oos_days": oos_days,
                "selected_windows": int(len(wf)),
                "oos_window_positive_rate": float(wf["oos_positive"].mean()),
                "oos_median_window_sum_r": float(wf["oos_sum_r"].median()),
                "oos_avg_window_sum_r": float(wf["oos_sum_r"].mean()),
                "oos_total_window_sum_r": float(wf["oos_sum_r"].sum()),
                "oos_avg_trades_per_window": float(wf["oos_trades"].mean()),
                "oos_median_trade_avg_r": float(wf["oos_avg_r"].median()),
                "oos_median_trade_median_r": float(wf["oos_median_r"].median()),
            }
        )
        row["wfa_pass"] = bool(
            row["selected_windows"] >= 20
            and row["oos_window_positive_rate"] >= 0.52
            and row["oos_median_window_sum_r"] > 0
            and row["oos_median_trade_avg_r"] > 0
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["wfa_pass", "oos_window_positive_rate", "oos_median_window_sum_r", "score"], ascending=[False, False, False, False]) if rows else pd.DataFrame()


def _wfa_matrix(screen: pd.DataFrame, ledgers: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for train_days, oos_days in WFA_MATRIX:
        wf = _wfa_fixed(screen, ledgers, train_days=train_days, oos_days=oos_days)
        if not wf.empty:
            frames.append(wf)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _write_report(out_dir: Path, screen: pd.DataFrame, wfa: pd.DataFrame) -> None:
    report_path = out_dir / "short_wfa_plateau_report.md"
    top_cols = [
        "family_group",
        "session_filter",
        "stop_model",
        "base_policy",
        "guard",
        "trades",
        "avg_r",
        "median_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "top_symbol_independence_pct",
        "plateau_neighbor_passes",
        "score",
    ]
    wfa_cols = top_cols[:5] + [
        "train_days",
        "oos_days",
        "selected_windows",
        "oos_window_positive_rate",
        "oos_median_window_sum_r",
        "oos_avg_trades_per_window",
        "oos_median_trade_avg_r",
        "wfa_pass",
    ]
    lines = [
        "# Short WFA Plateau Engine",
        "",
        "Bounded surface around the structural failed-retest short idea.",
        "Profit exits below 0.75R are not part of the candidate surface.",
        "",
        "Full-period plateau screen:",
        "",
        "```text",
        f"candidates={len(screen)}",
        f"full_passes={int(screen['full_pass'].sum()) if 'full_pass' in screen else 0}",
        f"plateau_passes={int(screen['plateau_pass'].sum()) if 'plateau_pass' in screen else 0}",
        "```",
        "",
        "Top plateau rows:",
        "",
        "```text",
        screen.loc[screen["plateau_pass"].astype(bool), [c for c in top_cols if c in screen.columns]].head(25).to_string(index=False) if not screen.empty and bool(screen["plateau_pass"].any()) else "EMPTY",
        "```",
        "",
        "Walk-forward matrix:",
        "",
        "```text",
        f"rows={len(wfa)}",
        f"wfa_passes={int(wfa['wfa_pass'].sum()) if not wfa.empty and 'wfa_pass' in wfa else 0}",
        "```",
        "",
        "Top WFA rows:",
        "",
        "```text",
        wfa[[c for c in wfa_cols if c in wfa.columns]].head(30).to_string(index=False) if not wfa.empty else "EMPTY",
        "```",
        "",
        "Interpretation guide:",
        "",
        "- `plateau_pass` means a row and nearby guard variants pass on the full-period surface.",
        "- `wfa_pass` means the fixed rule passed trailing train filters and then had positive next-window behavior.",
        "- Daily OOS windows overlap; use positive-window rates and median window sums, not raw duplicated trade totals.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(*, failed_dir: Path) -> dict[str, object]:
    path = failed_dir / "short_structural_075_path_replay_trades.csv"
    base = pd.read_csv(path)
    base = base[base["status"].eq("closed") & base["policy"].isin(BASE_POLICIES)].copy()
    base["date_ts"] = pd.to_datetime(base["date"], errors="coerce", utc=True).dt.tz_convert(None)
    base = base.dropna(subset=["date_ts", "net_r", "entry_price", "initial_risk_pct"])
    screen, ledgers = _build_full_screen(base)
    out_dir = failed_dir
    screen_path = out_dir / "short_wfa_plateau_candidates.csv"
    heatmap_path = out_dir / "short_wfa_plateau_heatmap.csv"
    wfa_path = out_dir / "short_wfa_plateau_matrix.csv"
    screen.to_csv(screen_path, index=False)
    heatmap_cols = [
        "family_group",
        "session_filter",
        "stop_model",
        "base_policy",
        "guard_minute",
        "guard_min_mfe_r",
        "guard_min_close_r",
        "guard",
        "trades",
        "avg_r",
        "median_r",
        "sum_r",
        "win_rate",
        "positive_day_rate",
        "top_trade_independence_pct",
        "plateau_pass",
    ]
    screen[[c for c in heatmap_cols if c in screen.columns]].to_csv(heatmap_path, index=False)
    wfa = _wfa_matrix(screen, ledgers)
    wfa.to_csv(wfa_path, index=False)
    _write_report(out_dir, screen, wfa)
    return {
        "base_rows": len(base),
        "candidate_rows": len(screen),
        "full_passes": int(screen["full_pass"].sum()) if not screen.empty else 0,
        "plateau_passes": int(screen["plateau_pass"].sum()) if not screen.empty else 0,
        "wfa_rows": len(wfa),
        "wfa_passes": int(wfa["wfa_pass"].sum()) if not wfa.empty and "wfa_pass" in wfa else 0,
        "screen_path": str(screen_path),
        "heatmap_path": str(heatmap_path),
        "wfa_path": str(wfa_path),
        "report_path": str(out_dir / "short_wfa_plateau_report.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    args = parser.parse_args()
    result = run(failed_dir=args.failed_dir)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
