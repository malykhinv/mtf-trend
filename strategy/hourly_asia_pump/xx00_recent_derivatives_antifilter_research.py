from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import _frame_to_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
TRADES_PATH = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_online_watchlist_backtest" / "entry_rule_trades.csv"
ENRICHED_PATH = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_recent_funding_oi_research" / "events_enriched.csv"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_recent_derivatives_antifilter_research"

MERGED_EVENTS_PATH = OUTPUT_DIR / "merged_events.csv"
BASELINE_PATH = OUTPUT_DIR / "baseline_summary.csv"
SEARCH_PATH = OUTPUT_DIR / "antifilter_search.csv"
SELECTED_PATH = OUTPUT_DIR / "selected_antifilters.csv"
FLAGGED_PATH = OUTPUT_DIR / "flagged_reversal_proxy.csv"
REPORT_PATH = OUTPUT_DIR / "report.md"

RULE_ID = "launch_r010_c65_v04_p0_rr30"
RECENT_DAYS = 31
MIN_FLAGGED = 12
MIN_RETAINED = 80


@dataclass(frozen=True, slots=True)
class FlagSpec:
    flag_id: str
    label: str
    column: str


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


def _flag_specs() -> tuple[FlagSpec, ...]:
    return (
        FlagSpec("exact_funding", "Exact funding window", "flag_exact_funding"),
        FlagSpec("high_abs_funding", "High abs funding q75", "flag_high_abs_funding"),
        FlagSpec("high_oi_15m", "High OI 15m q75", "flag_high_oi_15m"),
        FlagSpec("high_oi_60m", "High OI 60m q75", "flag_high_oi_60m"),
        FlagSpec("near_prev60", "Near prev60 high q25", "flag_near_prev60"),
        FlagSpec("break_prev60", "M0 close above prev60", "flag_break_prev60"),
        FlagSpec("high_pre_ret60", "High pre-return60 q75", "flag_high_pre_ret60"),
        FlagSpec("high_pre_ema200", "High pre-close vs ema200 q75", "flag_high_pre_ema200"),
    )


def _load_recent_base() -> pd.DataFrame:
    frame = pd.read_csv(TRADES_PATH, low_memory=False)
    frame = frame[
        (frame["dataset"].astype(str) == "current")
        & (frame["rule_id"].astype(str) == RULE_ID)
    ].copy()
    numeric_columns = [
        "timestamp_ms",
        "signal_bar_timestamp_ms",
        "entry_timestamp_ms",
        "exit_return_pct",
        "pre_dist_to_prev60_high_pct",
        "pre_return_60m_pct",
        "pre_close_vs_ema200_pct",
        "m0_return_pct",
        "m0_close_pos",
        "m0_volume_ratio",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True)
    cutoff = frame["timestamp_utc"].max() - pd.Timedelta(days=RECENT_DAYS)
    frame = frame[frame["timestamp_utc"] >= cutoff].copy()
    bool_columns = ["m0_close_above_prev60", "above_ema200"]
    for column in bool_columns:
        if column in frame.columns:
            frame[column] = (
                frame[column].astype(str).str.strip().str.lower().map({"true": True, "false": False, "1": True, "0": False}).fillna(False)
            )
    return frame.reset_index(drop=True)


def _load_merged_events() -> pd.DataFrame:
    if MERGED_EVENTS_PATH.exists():
        return pd.read_csv(MERGED_EVENTS_PATH, low_memory=False)

    base = _load_recent_base()
    enriched = pd.read_csv(ENRICHED_PATH, low_memory=False)
    merge_keys = ["symbol", "timestamp_ms", "session_id"]
    merged = base.merge(
        enriched[
            merge_keys
            + [
                "raw_symbol",
                "exit_reason",
                "exit_return_pct",
                "is_stop",
                "is_tp",
                "is_positive",
                "abs_last_funding_rate",
                "since_last_funding_min",
                "is_exact_funding_window",
                "oi_chg_15m_pct",
                "oi_chg_60m_pct",
            ]
        ],
        on=merge_keys,
        how="inner",
        suffixes=("", "_enriched"),
    )
    if merged.empty:
        _write_csv_atomic(MERGED_EVENTS_PATH, merged)
        return merged

    merged["abs_last_funding_rate"] = pd.to_numeric(merged["abs_last_funding_rate"], errors="coerce")
    merged["oi_chg_15m_pct"] = pd.to_numeric(merged["oi_chg_15m_pct"], errors="coerce")
    merged["oi_chg_60m_pct"] = pd.to_numeric(merged["oi_chg_60m_pct"], errors="coerce")
    merged["pre_dist_to_prev60_high_pct"] = pd.to_numeric(merged["pre_dist_to_prev60_high_pct"], errors="coerce")
    merged["pre_return_60m_pct"] = pd.to_numeric(merged["pre_return_60m_pct"], errors="coerce")
    merged["pre_close_vs_ema200_pct"] = pd.to_numeric(merged["pre_close_vs_ema200_pct"], errors="coerce")

    q_abs_funding = merged["abs_last_funding_rate"].quantile(0.75)
    q_oi_15 = merged["oi_chg_15m_pct"].quantile(0.75)
    q_oi_60 = merged["oi_chg_60m_pct"].quantile(0.75)
    q_near_prev60 = merged["pre_dist_to_prev60_high_pct"].quantile(0.25)
    q_pre_ret60 = merged["pre_return_60m_pct"].quantile(0.75)
    q_pre_ema200 = merged["pre_close_vs_ema200_pct"].quantile(0.75)

    merged["flag_exact_funding"] = merged["is_exact_funding_window"].astype(bool)
    merged["flag_high_abs_funding"] = merged["abs_last_funding_rate"] >= q_abs_funding
    merged["flag_high_oi_15m"] = merged["oi_chg_15m_pct"] >= q_oi_15
    merged["flag_high_oi_60m"] = merged["oi_chg_60m_pct"] >= q_oi_60
    merged["flag_near_prev60"] = merged["pre_dist_to_prev60_high_pct"] <= q_near_prev60
    merged["flag_break_prev60"] = merged["m0_close_above_prev60"].astype(bool)
    merged["flag_high_pre_ret60"] = merged["pre_return_60m_pct"] >= q_pre_ret60
    merged["flag_high_pre_ema200"] = merged["pre_close_vs_ema200_pct"] >= q_pre_ema200

    _write_csv_atomic(MERGED_EVENTS_PATH, merged)
    return merged


def _summarize(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {
            "events": 0,
            "stop_rate": None,
            "tp_rate": None,
            "positive_rate": None,
            "mean_exit_return_pct": None,
            "median_exit_return_pct": None,
        }
    ret = pd.to_numeric(frame["exit_return_pct"], errors="coerce")
    return {
        "events": int(len(frame)),
        "stop_rate": float(frame["is_stop"].astype(bool).mean()),
        "tp_rate": float(frame["is_tp"].astype(bool).mean()),
        "positive_rate": float(frame["is_positive"].astype(bool).mean()),
        "mean_exit_return_pct": float(ret.mean()),
        "median_exit_return_pct": float(ret.median()),
    }


def _baseline_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"scope": "all_recent", **_summarize(frame)},
    ]
    for session_id, scoped in frame.groupby("session_id", sort=True):
        rows.append({"scope": f"session_{session_id}", **_summarize(scoped)})
    result = pd.DataFrame(rows)
    _write_csv_atomic(BASELINE_PATH, result)
    return result


def _search_antifilters(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    baseline = _summarize(frame)
    specs = _flag_specs()
    for combo_size in (1, 2, 3):
        for combo in combinations(specs, combo_size):
            flag_id = "__and__".join(spec.flag_id for spec in combo)
            flag_label = " + ".join(spec.label for spec in combo)
            mask = pd.Series(True, index=frame.index)
            for spec in combo:
                mask &= frame[spec.column].astype(bool)
            flagged = frame[mask].copy()
            retained = frame[~mask].copy()
            if len(flagged) < MIN_FLAGGED or len(retained) < MIN_RETAINED:
                continue
            flagged_summary = _summarize(flagged)
            retained_summary = _summarize(retained)
            rows.append(
                {
                    "flag_id": flag_id,
                    "flag_label": flag_label,
                    "combo_size": combo_size,
                    "flagged_events": int(flagged_summary["events"]),
                    "flagged_stop_rate": flagged_summary["stop_rate"],
                    "flagged_tp_rate": flagged_summary["tp_rate"],
                    "flagged_positive_rate": flagged_summary["positive_rate"],
                    "flagged_mean_exit_return_pct": flagged_summary["mean_exit_return_pct"],
                    "flagged_short_proxy_mean_return_pct": (
                        -float(flagged_summary["mean_exit_return_pct"])
                        if flagged_summary["mean_exit_return_pct"] is not None
                        else None
                    ),
                    "flagged_short_proxy_positive_rate": float((pd.to_numeric(flagged["exit_return_pct"], errors="coerce") < 0.0).mean()),
                    "retained_events": int(retained_summary["events"]),
                    "retained_stop_rate": retained_summary["stop_rate"],
                    "retained_tp_rate": retained_summary["tp_rate"],
                    "retained_positive_rate": retained_summary["positive_rate"],
                    "retained_mean_exit_return_pct": retained_summary["mean_exit_return_pct"],
                    "retained_median_exit_return_pct": retained_summary["median_exit_return_pct"],
                    "mean_return_improvement_pct": float(retained_summary["mean_exit_return_pct"]) - float(baseline["mean_exit_return_pct"]),
                    "stop_rate_improvement": float(baseline["stop_rate"]) - float(retained_summary["stop_rate"]),
                    "tp_rate_improvement": float(retained_summary["tp_rate"]) - float(baseline["tp_rate"]),
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            [
                "retained_mean_exit_return_pct",
                "stop_rate_improvement",
                "retained_positive_rate",
                "flagged_short_proxy_mean_return_pct",
            ],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)
    _write_csv_atomic(SEARCH_PATH, result)
    return result


def _selected_antifilters(search: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if search.empty:
        empty = pd.DataFrame()
        _write_csv_atomic(SELECTED_PATH, empty)
        _write_csv_atomic(FLAGGED_PATH, empty)
        return empty, empty

    selected = search.head(12).copy()
    flagged = search.sort_values(
        ["flagged_short_proxy_mean_return_pct", "flagged_short_proxy_positive_rate", "flagged_events"],
        ascending=[False, False, False],
    ).head(12).copy()
    _write_csv_atomic(SELECTED_PATH, selected)
    _write_csv_atomic(FLAGGED_PATH, flagged)
    return selected, flagged


def _build_report(
    *,
    baseline: pd.DataFrame,
    selected: pd.DataFrame,
    flagged: pd.DataFrame,
) -> str:
    lines = [
        "# XX:00 Recent Derivatives Anti-Filter Research",
        "",
        "Goal:",
        "- test whether derivatives red flags can improve the recent honest XX:00 long pool;",
        "- keep features decision-time safe;",
        "- inspect whether excluded red-flag cohorts at least look like reversal candidates.",
        "",
        "Method:",
        "- base sample: recent 31d, current, broad base XX:00 launch rule;",
        "- merge recent funding/OI features known by HH:01 with pre-hour near-high / trend context;",
        "- search 1-3 way anti-filters built only from decision-time flags;",
        "- evaluate how excluding flagged events changes the remaining long pool;",
        "- report short numbers only as direction proxy on the flagged cohort, not as a tradable short backtest.",
        "",
        "Baseline:",
        _frame_to_markdown(
            baseline,
            columns=[
                "scope",
                "events",
                "stop_rate",
                "tp_rate",
                "positive_rate",
                "mean_exit_return_pct",
                "median_exit_return_pct",
            ],
        ),
        "",
        "Best anti-filters for long pool:",
        _frame_to_markdown(
            selected,
            columns=[
                "flag_id",
                "flagged_events",
                "retained_events",
                "retained_stop_rate",
                "retained_tp_rate",
                "retained_positive_rate",
                "retained_mean_exit_return_pct",
                "mean_return_improvement_pct",
                "stop_rate_improvement",
            ],
        ),
        "",
        "Best flagged cohorts by short proxy:",
        _frame_to_markdown(
            flagged,
            columns=[
                "flag_id",
                "flagged_events",
                "flagged_stop_rate",
                "flagged_tp_rate",
                "flagged_positive_rate",
                "flagged_mean_exit_return_pct",
                "flagged_short_proxy_mean_return_pct",
                "flagged_short_proxy_positive_rate",
            ],
        ),
        "",
        "Limits:",
        "- this search is recent-only and exploratory;",
        "- thresholds come from the same recent sample, so this is not OOS validation;",
        "- short proxy just flips the sign of long outcome and does not model real short execution, borrow, or stop logic.",
        "",
    ]
    return "\n".join(lines)


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _load_merged_events()
    baseline = _baseline_summary(frame)
    search = _search_antifilters(frame)
    selected, flagged = _selected_antifilters(search)
    report = _build_report(baseline=baseline, selected=selected, flagged=flagged)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report.encode("ascii", errors="ignore").decode("ascii"))


if __name__ == "__main__":
    run()
