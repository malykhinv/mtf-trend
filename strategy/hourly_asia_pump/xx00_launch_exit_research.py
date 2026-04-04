from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from strategy.hourly_asia_pump.xx00_regime_long_short_research import _load_m1

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_RESULTS_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_session_regime_long_research"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_launch_exit_research"
ONE_MINUTE_MS = 60_000
FEE_RATE = 0.0004

LOGGER = logging.getLogger("xx00-launch-exit-research")


@dataclass(frozen=True, slots=True)
class ExitTemplate:
    template_id: str
    label: str
    hold_minutes: int
    tp_rr: float | None = None
    second_tp_rr: float | None = None
    fast_fail_bars: int = 8
    fast_fail_r: float = 0.25
    breakeven_rr: float | None = None
    trail_activation_rr: float | None = None
    trail_style: str | None = None
    partial_rr: float | None = None
    partial_fraction: float = 0.0
    move_stop_to_be_after_partial: bool = False


def _exit_templates() -> tuple[ExitTemplate, ...]:
    return (
        ExitTemplate(
            template_id="fixed_rr20",
            label="Fixed 2R",
            hold_minutes=120,
            tp_rr=2.0,
        ),
        ExitTemplate(
            template_id="fixed_rr30",
            label="Fixed 3R",
            hold_minutes=120,
            tp_rr=3.0,
        ),
        ExitTemplate(
            template_id="tp2_half_be_tp3_half",
            label="50% at 2R, BE, 50% at 3R",
            hold_minutes=120,
            second_tp_rr=3.0,
            fast_fail_bars=0,
            fast_fail_r=0.0,
            partial_rr=2.0,
            partial_fraction=0.50,
            move_stop_to_be_after_partial=True,
        ),
        ExitTemplate(
            template_id="hold120_stop_only",
            label="Hold 120m, stop only",
            hold_minutes=120,
            fast_fail_bars=0,
            fast_fail_r=0.0,
        ),
        ExitTemplate(
            template_id="be10_prevbar",
            label="BE after 1R, trail prev bar low",
            hold_minutes=120,
            breakeven_rr=1.0,
            trail_activation_rr=1.0,
            trail_style="prev_bar_low",
        ),
        ExitTemplate(
            template_id="be10_lastred",
            label="BE after 1R, trail last red low",
            hold_minutes=120,
            breakeven_rr=1.0,
            trail_activation_rr=1.0,
            trail_style="last_red_low",
        ),
        ExitTemplate(
            template_id="p15_be_prevbar",
            label="Take 50% at 1.5R, BE, trail prev bar low",
            hold_minutes=120,
            partial_rr=1.5,
            partial_fraction=0.50,
            move_stop_to_be_after_partial=True,
            breakeven_rr=1.0,
            trail_activation_rr=1.0,
            trail_style="prev_bar_low",
        ),
        ExitTemplate(
            template_id="p20_be_lastred",
            label="Take 35% at 2R, BE, trail last red low",
            hold_minutes=120,
            partial_rr=2.0,
            partial_fraction=0.35,
            move_stop_to_be_after_partial=True,
            breakeven_rr=1.0,
            trail_activation_rr=1.0,
            trail_style="last_red_low",
        ),
        ExitTemplate(
            template_id="late_be15_lastred",
            label="BE after 1.5R, trail last red low",
            hold_minutes=120,
            breakeven_rr=1.5,
            trail_activation_rr=1.5,
            trail_style="last_red_low",
        ),
    )


def _calendar_months_from_frame(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "month_utc" not in frame.columns:
        return []
    months = sorted(frame["month_utc"].dropna().astype(str).unique().tolist())
    return months


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _safe_float(value: object, *, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return default
    return float(numeric)


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected_rules = pd.read_csv(SESSION_RESULTS_DIR / "selected_rules.csv", low_memory=False)
    selected_events = pd.read_csv(SESSION_RESULTS_DIR / "selected_events.csv", low_memory=False)
    feature_frame = pd.read_csv(SESSION_RESULTS_DIR / "feature_frame.csv", low_memory=False)
    return selected_rules, selected_events, feature_frame


def _candidate_rules(selected_rules: pd.DataFrame) -> pd.DataFrame:
    if selected_rules.empty:
        return selected_rules
    candidates = selected_rules[
        (selected_rules["family"].astype(str) == "long_launch")
        & (selected_rules["selection_type"].astype(str).isin({"best_ready", "earliest_viable"}))
    ].copy()
    candidates["candidate_id"] = (
        candidates["cohort_id"].astype(str)
        + "|"
        + candidates["rule_id"].astype(str)
        + "|"
        + candidates["selection_type"].astype(str)
    )
    return candidates.reset_index(drop=True)


def _extension_summary(events: pd.DataFrame, feature_frame: pd.DataFrame) -> pd.DataFrame:
    if events.empty or feature_frame.empty:
        return pd.DataFrame()
    merged = events.merge(
        feature_frame[
            [
                "event_id",
                "post_max_up_extension_pct_30m",
                "post_max_down_extension_pct_30m",
            ]
        ],
        on="event_id",
        how="left",
    )
    current = merged[merged["dataset"].astype(str) == "current"].copy()
    if current.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for candidate_id, scoped in current.groupby("candidate_id", sort=True):
        initial_risk = pd.to_numeric(scoped["initial_risk_pct"], errors="coerce")
        post_up = pd.to_numeric(scoped["post_max_up_extension_pct_30m"], errors="coerce")
        post_down = pd.to_numeric(scoped["post_max_down_extension_pct_30m"], errors="coerce")
        leftover_after_2r = (post_up - (2.0 * initial_risk)).clip(lower=0.0)
        rows.append(
            {
                "candidate_id": str(candidate_id),
                "trades": int(len(scoped)),
                "median_initial_risk_pct": float(initial_risk.median()) if not initial_risk.empty else 0.0,
                "median_post_up_30m_pct": float(post_up.median()) if not post_up.empty else 0.0,
                "p75_post_up_30m_pct": float(post_up.quantile(0.75)) if not post_up.empty else 0.0,
                "p90_post_up_30m_pct": float(post_up.quantile(0.90)) if not post_up.empty else 0.0,
                "median_post_down_30m_pct": float(post_down.median()) if not post_down.empty else 0.0,
                "share_reach_1r_30m": float((post_up >= initial_risk).mean()) if len(scoped) > 0 else 0.0,
                "share_reach_2r_30m": float((post_up >= (2.0 * initial_risk)).mean()) if len(scoped) > 0 else 0.0,
                "share_reach_3r_30m": float((post_up >= (3.0 * initial_risk)).mean()) if len(scoped) > 0 else 0.0,
                "share_reach_4r_30m": float((post_up >= (4.0 * initial_risk)).mean()) if len(scoped) > 0 else 0.0,
                "median_leftover_after_2r_pct": float(leftover_after_2r.median()) if not leftover_after_2r.empty else 0.0,
                "mean_leftover_after_2r_pct": float(leftover_after_2r.mean()) if not leftover_after_2r.empty else 0.0,
                "strong_share": float((scoped["continuation_label"].astype(str) == "strong").mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("candidate_id").reset_index(drop=True)


def _simulate_template(
    *,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    template: ExitTemplate,
) -> dict[str, object] | None:
    risk = entry_price - stop_price
    if risk <= 0.0:
        return None
    if entry_idx < 0 or entry_idx >= len(opens):
        return None

    last_idx = min(len(opens) - 1, entry_idx + template.hold_minutes - 1)
    current_stop = float(stop_price)
    remaining_fraction = 1.0
    realized_return_pct = 0.0
    highest_high = float(entry_price)
    trail_active = template.trail_activation_rr is None or template.trail_activation_rr <= 0.0
    last_red_low: float | None = None
    partial_taken = False
    partial_price = (entry_price + (risk * template.partial_rr)) if template.partial_rr is not None else None
    target_price = (entry_price + (risk * template.tp_rr)) if template.tp_rr is not None else None
    second_target_price = (entry_price + (risk * template.second_tp_rr)) if template.second_tp_rr is not None else None

    for idx in range(entry_idx, last_idx + 1):
        open_price = float(opens[idx])
        high_price = float(highs[idx])
        low_price = float(lows[idx])
        close_price = float(closes[idx])
        bars_in_trade = idx - entry_idx + 1

        if low_price <= current_stop:
            realized_return_pct += remaining_fraction * _net_long_return(entry_price, current_stop)
            return {
                "exit_idx": idx,
                "exit_price": float(current_stop),
                "exit_reason": "stop",
                "exit_return_pct": float(realized_return_pct),
                "max_return_after_entry_pct": float((highest_high / entry_price) - 1.0),
            }

        if (
            partial_price is not None
            and not partial_taken
            and template.partial_fraction > 0.0
            and high_price >= partial_price
        ):
            realized_return_pct += template.partial_fraction * _net_long_return(entry_price, partial_price)
            remaining_fraction = max(0.0, remaining_fraction - template.partial_fraction)
            partial_taken = True
            if template.move_stop_to_be_after_partial:
                current_stop = max(current_stop, entry_price)
            if remaining_fraction <= 0.0:
                return {
                    "exit_idx": idx,
                    "exit_price": float(partial_price),
                    "exit_reason": "partial_full",
                    "exit_return_pct": float(realized_return_pct),
                    "max_return_after_entry_pct": float(max(highest_high, high_price) / entry_price - 1.0),
                }

            if low_price <= current_stop:
                realized_return_pct += remaining_fraction * _net_long_return(entry_price, current_stop)
                return {
                    "exit_idx": idx,
                    "exit_price": float(current_stop),
                    "exit_reason": "partial_stop_same_bar",
                    "exit_return_pct": float(realized_return_pct),
                    "max_return_after_entry_pct": float(max(highest_high, high_price) / entry_price - 1.0),
                }

            if second_target_price is not None and high_price >= second_target_price:
                realized_return_pct += remaining_fraction * _net_long_return(entry_price, second_target_price)
                return {
                    "exit_idx": idx,
                    "exit_price": float(second_target_price),
                    "exit_reason": "tp2",
                    "exit_return_pct": float(realized_return_pct),
                    "max_return_after_entry_pct": float(max(highest_high, high_price) / entry_price - 1.0),
                }

        if partial_taken and second_target_price is not None and high_price >= second_target_price:
            realized_return_pct += remaining_fraction * _net_long_return(entry_price, second_target_price)
            return {
                "exit_idx": idx,
                "exit_price": float(second_target_price),
                "exit_reason": "tp2",
                "exit_return_pct": float(realized_return_pct),
                "max_return_after_entry_pct": float(max(highest_high, high_price) / entry_price - 1.0),
            }

        if target_price is not None and high_price >= target_price:
            realized_return_pct += remaining_fraction * _net_long_return(entry_price, target_price)
            return {
                "exit_idx": idx,
                "exit_price": float(target_price),
                "exit_reason": "tp",
                "exit_return_pct": float(realized_return_pct),
                "max_return_after_entry_pct": float(max(highest_high, high_price) / entry_price - 1.0),
            }

        if high_price > highest_high:
            highest_high = high_price
        if template.breakeven_rr is not None and highest_high >= (entry_price + risk * template.breakeven_rr):
            current_stop = max(current_stop, entry_price)
        if not trail_active and template.trail_activation_rr is not None:
            if highest_high >= (entry_price + risk * template.trail_activation_rr):
                trail_active = True
        if trail_active:
            if template.trail_style == "prev_bar_low" and idx - 1 >= entry_idx:
                current_stop = max(current_stop, float(lows[idx - 1]))
            elif template.trail_style == "last_red_low" and last_red_low is not None:
                current_stop = max(current_stop, float(last_red_low))

        if template.fast_fail_bars > 0 and bars_in_trade >= template.fast_fail_bars:
            if highest_high < (entry_price + risk * template.fast_fail_r):
                realized_return_pct += remaining_fraction * _net_long_return(entry_price, close_price)
                return {
                    "exit_idx": idx,
                    "exit_price": float(close_price),
                    "exit_reason": "fast_fail",
                    "exit_return_pct": float(realized_return_pct),
                    "max_return_after_entry_pct": float((highest_high / entry_price) - 1.0),
                }

        if close_price < open_price:
            last_red_low = low_price

    realized_return_pct += remaining_fraction * _net_long_return(entry_price, float(closes[last_idx]))
    return {
        "exit_idx": last_idx,
        "exit_price": float(closes[last_idx]),
        "exit_reason": "time_exit",
        "exit_return_pct": float(realized_return_pct),
        "max_return_after_entry_pct": float((highest_high / entry_price) - 1.0),
    }


def _simulate_events(candidates: pd.DataFrame, selected_events: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or selected_events.empty:
        return pd.DataFrame()

    events = selected_events.merge(
        candidates[
            [
                "candidate_id",
                "cohort_id",
                "rule_id",
                "selection_type",
                "current_trades_per_year",
            ]
        ],
        on=["cohort_id", "rule_id", "selection_type"],
        how="inner",
    )
    if events.empty:
        return pd.DataFrame()

    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    templates = _exit_templates()

    grouped = events.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles = _load_m1(str(cache_scope), str(symbol), cache)
        if candles.empty or "timestamp" not in candles.columns:
            continue
        timestamps = pd.to_numeric(candles["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(candles["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles["close"], errors="coerce").astype(float).tolist()

        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            LOGGER.info(
                "xx00-launch-exit-research: simulate-progress=%s/%s cache=%s symbol=%s events=%s",
                group_index,
                total_groups,
                cache_scope,
                str(symbol).encode("ascii", errors="ignore").decode("ascii") or "<non-ascii>",
                len(scoped),
            )

        for _, event_row in scoped.iterrows():
            entry_timestamp_ms = int(_safe_float(event_row.get("entry_timestamp_ms")))
            entry_idx = timestamp_to_idx.get(entry_timestamp_ms)
            if entry_idx is None:
                continue
            entry_price = _safe_float(event_row.get("entry_price"))
            stop_price = _safe_float(event_row.get("stop_price"))
            if stop_price >= entry_price:
                continue
            for template in templates:
                simulated = _simulate_template(
                    timestamps=timestamps,
                    opens=opens,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    entry_idx=entry_idx,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    template=template,
                )
                if simulated is None:
                    continue
                rows.append(
                    {
                        **event_row.to_dict(),
                        "template_id": template.template_id,
                        "template_label": template.label,
                        "exit_timestamp_ms": int(timestamps[int(simulated["exit_idx"])] + ONE_MINUTE_MS),
                        "exit_price": float(simulated["exit_price"]),
                        "exit_reason": str(simulated["exit_reason"]),
                        "exit_return_pct": float(simulated["exit_return_pct"]),
                        "max_return_after_entry_pct": float(simulated["max_return_after_entry_pct"]),
                    }
                )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["exit_timestamp_utc"] = pd.to_datetime(
        pd.to_numeric(frame["exit_timestamp_ms"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    )
    frame["entry_timestamp_utc"] = pd.to_datetime(
        pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    )
    return frame.sort_values(
        ["candidate_id", "template_id", "dataset", "entry_timestamp_ms", "symbol"]
    ).reset_index(drop=True)


def _summary_by_template(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (candidate_id, template_id), scoped in events.groupby(["candidate_id", "template_id"], sort=True):
        base = scoped.iloc[0]
        for dataset in ("current", "old", "combined"):
            if dataset == "combined":
                dataset_slice = scoped.copy()
            else:
                dataset_slice = scoped[scoped["dataset"].astype(str) == dataset].copy()
            if dataset_slice.empty:
                continue
            calendar_months = _calendar_months_from_frame(dataset_slice)
            summary = _summarize_events(dataset_slice, calendar_months=calendar_months) or {}
            equity_3, _ = _simulate_equity_risk_metrics(dataset_slice, calendar_months=calendar_months, risk_fraction=0.03)
            equity_5, _ = _simulate_equity_risk_metrics(dataset_slice, calendar_months=calendar_months, risk_fraction=0.05)
            rows.append(
                {
                    "candidate_id": str(candidate_id),
                    "cohort_id": str(base["cohort_id"]),
                    "cohort_title": str(base.get("cohort_title", "")),
                    "rule_id": str(base["rule_id"]),
                    "selection_type": str(base["selection_type"]),
                    "template_id": str(template_id),
                    "template_label": str(base["template_label"]),
                    "dataset": dataset,
                    "trades": int(summary.get("trades_count", 0)),
                    "trades_per_year": float(summary.get("trades_per_year", 0.0) or 0.0),
                    "mean_return_pct": float(summary.get("mean_return_pct", 0.0) or 0.0),
                    "median_return_pct": float(summary.get("median_return_pct", 0.0) or 0.0),
                    "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
                    "annualized_unit_pnl_pct": float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0),
                    "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0) or 0.0),
                    "equity_total_return_pct_3": float(equity_3.get("equity_total_return_pct", 0.0) or 0.0),
                    "equity_annualized_return_pct_3": float(equity_3.get("equity_annualized_return_pct", 0.0) or 0.0),
                    "equity_max_drawdown_pct_3": float(equity_3.get("equity_max_drawdown_pct", 0.0) or 0.0),
                    "equity_positive_months_count_3": int(equity_3.get("equity_positive_months_count", 0) or 0),
                    "equity_stable_positive_months_count_3": int(
                        equity_3.get("equity_stable_positive_months_count", 0) or 0
                    ),
                    "equity_total_return_pct_5": float(equity_5.get("equity_total_return_pct", 0.0) or 0.0),
                    "equity_annualized_return_pct_5": float(equity_5.get("equity_annualized_return_pct", 0.0) or 0.0),
                    "equity_max_drawdown_pct_5": float(equity_5.get("equity_max_drawdown_pct", 0.0) or 0.0),
                    "equity_positive_months_count_5": int(equity_5.get("equity_positive_months_count", 0) or 0),
                    "equity_stable_positive_months_count_5": int(
                        equity_5.get("equity_stable_positive_months_count", 0) or 0
                    ),
                    "mean_mfe_pct": float(pd.to_numeric(dataset_slice["max_return_after_entry_pct"], errors="coerce").mean()),
                    "median_mfe_pct": float(pd.to_numeric(dataset_slice["max_return_after_entry_pct"], errors="coerce").median()),
                }
            )
    return pd.DataFrame(rows)


def _best_templates(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    current = summary[summary["dataset"].astype(str) == "current"].copy()
    if current.empty:
        return pd.DataFrame()
    ranked = current.sort_values(
        [
            "equity_annualized_return_pct_3",
            "annualized_unit_pnl_pct",
            "win_rate",
            "mean_return_pct",
        ],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    winners = ranked.groupby("candidate_id", as_index=False).head(3).copy()
    return winners.reset_index(drop=True)


def _build_report(
    *,
    candidates: pd.DataFrame,
    extension: pd.DataFrame,
    summary: pd.DataFrame,
    best_templates: pd.DataFrame,
) -> str:
    current_summary = summary[summary["dataset"].astype(str) == "current"].copy()
    old_summary = summary[summary["dataset"].astype(str) == "old"].copy()
    current_wide = current_summary.copy()
    old_wide = old_summary.copy()
    lines = [
        "# XX:00 Launch Exit Research",
        "",
        "Goal:",
        "- measure how much movement remains after the current XX:00 long entries;",
        "- compare fixed-R exits against BE/trailing/partial management;",
        "- keep the simulation no-lookahead: entry at stored next-open fill, stop and trail updated only from already closed bars.",
        "",
        "Analyzed candidates:",
        _frame_to_markdown(
            candidates,
            columns=[
                "cohort_id",
                "selection_type",
                "rule_id",
                "current_trades_per_year",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_annualized_return_pct_3",
                "current_equity_max_drawdown_pct_3",
            ],
        )
        if not candidates.empty
        else "_No candidates._",
        "",
        "How much continuation remains after entry (current only):",
        _frame_to_markdown(
            extension,
            columns=[
                "candidate_id",
                "trades",
                "median_initial_risk_pct",
                "median_post_up_30m_pct",
                "p75_post_up_30m_pct",
                "p90_post_up_30m_pct",
                "share_reach_2r_30m",
                "share_reach_3r_30m",
                "share_reach_4r_30m",
                "median_leftover_after_2r_pct",
                "mean_leftover_after_2r_pct",
            ],
        )
        if not extension.empty
        else "_No extension data._",
        "",
        "Best exit templates by candidate (current):",
        _frame_to_markdown(
            best_templates,
            columns=[
                "candidate_id",
                "template_id",
                "trades",
                "trades_per_year",
                "win_rate",
                "mean_return_pct",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct_3",
                "equity_max_drawdown_pct_3",
                "equity_annualized_return_pct_5",
                "equity_max_drawdown_pct_5",
            ],
        )
        if not best_templates.empty
        else "_No template rankings._",
        "",
        "All template metrics on current:",
        _frame_to_markdown(
            current_wide,
            columns=[
                "candidate_id",
                "template_id",
                "trades",
                "trades_per_year",
                "win_rate",
                "mean_return_pct",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct_3",
                "equity_max_drawdown_pct_3",
                "equity_positive_months_count_3",
                "equity_stable_positive_months_count_3",
            ],
            limit=200,
        )
        if not current_wide.empty
        else "_No current metrics._",
        "",
        "All template metrics on old:",
        _frame_to_markdown(
            old_wide,
            columns=[
                "candidate_id",
                "template_id",
                "trades",
                "trades_per_year",
                "win_rate",
                "mean_return_pct",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct_3",
                "equity_max_drawdown_pct_3",
                "equity_positive_months_count_3",
                "equity_stable_positive_months_count_3",
            ],
            limit=200,
        )
        if not old_wide.empty
        else "_No old metrics._",
    ]
    return "\n".join(lines)


def run() -> dict[str, Path]:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    selected_rules, selected_events, feature_frame = _load_inputs()
    candidates = _candidate_rules(selected_rules)
    extension = _extension_summary(
        selected_events.merge(
            candidates[["candidate_id", "cohort_id", "rule_id", "selection_type"]],
            on=["cohort_id", "rule_id", "selection_type"],
            how="inner",
        ),
        feature_frame,
    )
    simulated_events = _simulate_events(candidates, selected_events)
    summary = _summary_by_template(simulated_events)
    best_templates = _best_templates(summary)

    paths = {
        "extension_summary": OUTPUT_DIR / "extension_summary.csv",
        "simulated_events": OUTPUT_DIR / "simulated_events.csv",
        "template_summary": OUTPUT_DIR / "template_summary.csv",
        "best_templates": OUTPUT_DIR / "best_templates.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    extension.to_csv(paths["extension_summary"], index=False)
    simulated_events.to_csv(paths["simulated_events"], index=False)
    summary.to_csv(paths["template_summary"], index=False)
    best_templates.to_csv(paths["best_templates"], index=False)
    paths["report"].write_text(
        _build_report(
            candidates=candidates,
            extension=extension,
            summary=summary,
            best_templates=best_templates,
        ),
        encoding="utf-8",
    )
    return paths


if __name__ == "__main__":
    run()
