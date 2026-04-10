from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "decision_time_state_lab"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_5m_inner_bar_lab"

INPUT_PATH = INPUT_DIR / "decision_time_states.csv"


@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_id: str
    context_archetype: str
    max_minute: int
    max_pullback: float
    max_seller: float
    min_buyer: float
    min_body: float
    min_volume: float
    min_close_pos: float
    max_red_share: float
    max_new_low_count: int


TEMPLATE_IDS = (
    "nextopen_bar_low_fx20",
    "nextopen_state_low_fx20",
    "nextopen_bar_low_be10trail",
    "nextopen_state_low_be10trail",
)


def _load_first_break_events() -> pd.DataFrame:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Не найдена decision-time БД: {INPUT_PATH}")
    frame = pd.read_csv(INPUT_PATH, low_memory=False)
    numeric_columns = [
        "minute_offset",
        "pullback_so_far_frac",
        "seller_pressure_cum",
        "current_buyer_score",
        "current_body_ratio",
        "current_volume_ratio",
        "current_close_pos",
        "red_bar_share",
        "new_low_count",
        "wave_present",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    scoped = frame[
        frame["context_archetype"].astype(str).isin({"context_warm", "context_overheated"})
        & (pd.to_numeric(frame["minute_offset"], errors="coerce") <= 5)
        & frame["current_close_above_trigger_high"].astype(bool)
    ].copy()
    scoped = scoped.sort_values(["event_id", "minute_offset"]).drop_duplicates(["event_id"], keep="first").reset_index(drop=True)
    return scoped


def _rule_specs() -> tuple[RuleSpec, ...]:
    rules: list[RuleSpec] = []
    warm_minutes = (1, 2, 3, 4)
    over_minutes = (1, 2, 3, 4)
    for context, minutes in (("context_warm", warm_minutes), ("context_overheated", over_minutes)):
        for minute in minutes:
            for pullback in (0.10, 0.15, 0.20, 0.25):
                for seller in (0.03, 0.05, 0.08):
                    for buyer in (0.24, 0.30):
                        for body in (0.80, 1.00, 1.30):
                            for volume in (0.80, 1.00):
                                for close_pos in (0.65, 0.75):
                                    for red_share in (1.0, 0.67):
                                        for new_lows in (2, 1):
                                            rules.append(
                                                RuleSpec(
                                                    rule_id=(
                                                        f"{context.replace('context_', '')}"
                                                        f"_m{minute}"
                                                        f"_p{int(pullback * 100):02d}"
                                                        f"_s{int(seller * 100):02d}"
                                                        f"_b{int(buyer * 100):02d}"
                                                        f"_bd{int(body * 100):03d}"
                                                        f"_v{int(volume * 100):03d}"
                                                        f"_cp{int(close_pos * 100):02d}"
                                                        f"_r{int(red_share * 100):03d}"
                                                        f"_nl{new_lows}"
                                                    ),
                                                    context_archetype=context,
                                                    max_minute=minute,
                                                    max_pullback=pullback,
                                                    max_seller=seller,
                                                    min_buyer=buyer,
                                                    min_body=body,
                                                    min_volume=volume,
                                                    min_close_pos=close_pos,
                                                    max_red_share=red_share,
                                                    max_new_low_count=new_lows,
                                                )
                                            )
    return tuple(rules)


def _minute_profile(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for context in ("context_warm", "context_overheated"):
        scoped = frame[frame["context_archetype"].astype(str) == context].copy()
        if scoped.empty:
            continue
        for minute in sorted(pd.to_numeric(scoped["minute_offset"], errors="coerce").dropna().astype(int).unique()):
            part = scoped[pd.to_numeric(scoped["minute_offset"], errors="coerce") == int(minute)].copy()
            row: dict[str, object] = {
                "context_archetype": context,
                "first_break_minute": int(minute),
                "events": int(len(part)),
                "wave_present_rate": float(pd.to_numeric(part["wave_present"], errors="coerce").fillna(0.0).mean()),
                "mean_pullback_frac": float(pd.to_numeric(part["pullback_so_far_frac"], errors="coerce").mean()),
                "mean_seller_pressure": float(pd.to_numeric(part["seller_pressure_cum"], errors="coerce").mean()),
                "mean_buyer_score": float(pd.to_numeric(part["current_buyer_score"], errors="coerce").mean()),
                "mean_body_ratio": float(pd.to_numeric(part["current_body_ratio"], errors="coerce").mean()),
                "mean_volume_ratio": float(pd.to_numeric(part["current_volume_ratio"], errors="coerce").mean()),
                "mean_close_pos": float(pd.to_numeric(part["current_close_pos"], errors="coerce").mean()),
            }
            for template_id in TEMPLATE_IDS:
                ret_col = f"{template_id}_ret"
                if ret_col in part.columns:
                    returns = pd.to_numeric(part[ret_col], errors="coerce")
                    row[f"{template_id}_mean_ret"] = float(returns.mean())
                    row[f"{template_id}_win_rate"] = float((returns > 0.0).mean())
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["context_archetype", "first_break_minute"]).reset_index(drop=True)


def _rule_mask(frame: pd.DataFrame, rule: RuleSpec) -> pd.Series:
    return (
        (frame["context_archetype"].astype(str) == rule.context_archetype)
        & (pd.to_numeric(frame["minute_offset"], errors="coerce") <= rule.max_minute)
        & (pd.to_numeric(frame["pullback_so_far_frac"], errors="coerce") <= rule.max_pullback)
        & (pd.to_numeric(frame["seller_pressure_cum"], errors="coerce") <= rule.max_seller)
        & (pd.to_numeric(frame["current_buyer_score"], errors="coerce") >= rule.min_buyer)
        & (pd.to_numeric(frame["current_body_ratio"], errors="coerce") >= rule.min_body)
        & (pd.to_numeric(frame["current_volume_ratio"], errors="coerce") >= rule.min_volume)
        & (pd.to_numeric(frame["current_close_pos"], errors="coerce") >= rule.min_close_pos)
        & (pd.to_numeric(frame["red_bar_share"], errors="coerce") <= rule.max_red_share)
        & (pd.to_numeric(frame["new_low_count"], errors="coerce") <= rule.max_new_low_count)
    )


def _equity_summary(frame: pd.DataFrame, template_id: str) -> tuple[float | None, float | None]:
    if frame.empty:
        return None, None
    events = frame.copy()
    events["exit_return_pct"] = pd.to_numeric(events[f"{template_id}_ret"], errors="coerce")
    events["entry_timestamp_ms"] = pd.to_numeric(events[f"{template_id}_entry_timestamp_ms"], errors="coerce")
    events["exit_timestamp_ms"] = pd.to_numeric(events[f"{template_id}_exit_timestamp_ms"], errors="coerce")
    events["entry_price"] = pd.to_numeric(events[f"{template_id}_entry_price"], errors="coerce")
    events["stop_price"] = pd.to_numeric(events[f"{template_id}_stop"], errors="coerce")
    events["initial_risk_pct"] = (events["entry_price"] - events["stop_price"]) / events["entry_price"]
    eq, _ = _simulate_equity_risk_metrics(
        events,
        calendar_months=_calendar_months_from_frames(events),
        risk_fraction=0.05,
    )
    return (
        float(eq.get("equity_annualized_return_pct", 0.0)),
        float(eq.get("equity_max_drawdown_pct", 0.0)),
    )


def _top_share(frame: pd.DataFrame, template_id: str, group_col: str) -> float | None:
    returns = pd.to_numeric(frame[f"{template_id}_ret"], errors="coerce")
    total = float(returns.sum())
    if total <= 0.0:
        return None
    grouped = frame.assign(_ret=returns).groupby(group_col, as_index=False)["_ret"].sum()
    return float(grouped["_ret"].max() / total)


def _summarize_rule(frame: pd.DataFrame, rule: RuleSpec, template_id: str) -> dict[str, object] | None:
    valid_col = f"{template_id}_valid"
    ret_col = f"{template_id}_ret"
    if valid_col not in frame.columns or ret_col not in frame.columns:
        return None
    scoped = frame[_rule_mask(frame, rule) & frame[valid_col].astype(bool)].copy()
    if scoped.empty:
        return None

    current = scoped[scoped["dataset"].astype(str) == "current"].copy()
    old = scoped[scoped["dataset"].astype(str) == "old"].copy()
    current_mean = float(pd.to_numeric(current[ret_col], errors="coerce").mean()) if not current.empty else None
    old_mean = float(pd.to_numeric(old[ret_col], errors="coerce").mean()) if not old.empty else None
    current_win = float((pd.to_numeric(current[ret_col], errors="coerce") > 0.0).mean()) if not current.empty else None
    old_win = float((pd.to_numeric(old[ret_col], errors="coerce") > 0.0).mean()) if not old.empty else None
    top1_symbol_share = _top_share(scoped, template_id, "symbol")
    best_month_share = _top_share(scoped, template_id, "month_utc")

    min_mean_candidates = [value for value in (current_mean, old_mean) if value is not None]
    score = (
        (min(min_mean_candidates) * 260.0 if min_mean_candidates else 0.0)
        + min(len(current), len(old)) * 0.35
        - ((top1_symbol_share or 0.0) * 18.0)
        - ((best_month_share or 0.0) * 10.0)
    )
    status = "not_ready"
    if (
        len(current) >= 20
        and len(old) >= 10
        and current_mean is not None
        and old_mean is not None
        and current_mean > 0.0
        and old_mean > 0.0
    ):
        status = "surviving"

    return {
        "rule_id": rule.rule_id,
        "context_archetype": rule.context_archetype,
        "template_id": template_id,
        "current_events": int(len(current)),
        "old_events": int(len(old)),
        "current_wave_present_rate": float(pd.to_numeric(current["wave_present"], errors="coerce").mean()) if not current.empty else None,
        "old_wave_present_rate": float(pd.to_numeric(old["wave_present"], errors="coerce").mean()) if not old.empty else None,
        "current_mean_return_pct": current_mean,
        "old_mean_return_pct": old_mean,
        "current_win_rate": current_win,
        "old_win_rate": old_win,
        "current_equity_annualized_return_pct_5": None,
        "old_equity_annualized_return_pct_5": None,
        "current_equity_max_drawdown_pct_5": None,
        "old_equity_max_drawdown_pct_5": None,
        "top1_symbol_share": top1_symbol_share,
        "best_month_share": best_month_share,
        "score": float(score),
        "selection_status": status,
    }


def _search_rules(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    rules = _rule_specs()
    rule_map = {rule.rule_id: rule for rule in rules}
    for rule in rules:
        for template_id in TEMPLATE_IDS:
            row = _summarize_rule(frame, rule, template_id)
            if row is not None:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    summary = pd.DataFrame(rows).sort_values(["selection_status", "score"], ascending=[True, False]).reset_index(drop=True)

    top_n = min(200, len(summary))
    for idx in range(top_n):
        row = summary.iloc[idx]
        rule = rule_map[str(row["rule_id"])]
        template_id = str(row["template_id"])
        scoped = frame[_rule_mask(frame, rule) & frame[f"{template_id}_valid"].astype(bool)].copy()
        if scoped.empty:
            continue
        current = scoped[scoped["dataset"].astype(str) == "current"].copy()
        old = scoped[scoped["dataset"].astype(str) == "old"].copy()
        current_eq, current_dd = _equity_summary(current, template_id)
        old_eq, old_dd = _equity_summary(old, template_id)
        summary.at[idx, "current_equity_annualized_return_pct_5"] = current_eq
        summary.at[idx, "old_equity_annualized_return_pct_5"] = old_eq
        summary.at[idx, "current_equity_max_drawdown_pct_5"] = current_dd
        summary.at[idx, "old_equity_max_drawdown_pct_5"] = old_dd
        score = float(summary.at[idx, "score"])
        if current_eq is not None and old_eq is not None:
            score += min(float(current_eq), float(old_eq)) * 30.0
        elif current_eq is not None:
            score += float(current_eq) * 15.0
        elif old_eq is not None:
            score += float(old_eq) * 15.0
        dd_values = [value for value in (current_dd, old_dd) if value is not None]
        if dd_values:
            score -= max(float(value) for value in dd_values) * 20.0
        summary.at[idx, "score"] = score

    return summary.sort_values(["selection_status", "score"], ascending=[True, False]).reset_index(drop=True)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    first_break = _load_first_break_events()
    first_break.to_csv(OUTPUT_DIR / "first_break_events.csv", index=False)

    minute_profile = _minute_profile(first_break)
    minute_profile.to_csv(OUTPUT_DIR / "minute_profile.csv", index=False)

    rule_summary = _search_rules(first_break)
    rule_summary.to_csv(OUTPUT_DIR / "rule_summary.csv", index=False)

    selected = rule_summary[rule_summary["selection_status"].astype(str) == "surviving"].copy() if not rule_summary.empty else pd.DataFrame()
    selected.to_csv(OUTPUT_DIR / "selected_rules.csv", index=False)

    report_lines = [
        "# Second 5m Inner-Bar Lab",
        "",
        "Одна строка = первая `1m` внутри следующей `5m`, где цена впервые закрылась выше high аномалии.",
        "Все признаки известны к моменту этой минуты; вход считается от следующего `1m` open.",
        "",
        "## Minute Profile",
        "",
        (
            _frame_to_markdown(
                minute_profile,
                columns=minute_profile.columns.tolist(),
                limit=20,
            )
            if not minute_profile.empty
            else "_Пусто_"
        ),
        "",
        "## Top Surviving Rules",
        "",
        (
            _frame_to_markdown(
                selected,
                columns=selected.columns.tolist(),
                limit=20,
            )
            if not selected.empty
            else "_Нет surviving-правил_"
        ),
        "",
        "## Top Overall Rules",
        "",
        (
            _frame_to_markdown(
                rule_summary,
                columns=rule_summary.columns.tolist(),
                limit=30,
            )
            if not rule_summary.empty
            else "_Пусто_"
        ),
    ]
    (OUTPUT_DIR / "report.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Saved: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
