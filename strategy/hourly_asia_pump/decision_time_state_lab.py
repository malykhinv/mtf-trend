from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.second_wave_execution_search import (
    ManagementSpec,
    _simulate_managed_exit,
)
from strategy.hourly_asia_pump.static_combo import _calendar_months_from_frames
from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _close_pos,
    _load_feature_db,
    _load_m1,
    _prepare_scope,
    _pressure_score,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "decision_time_state_lab"


@dataclass(frozen=True, slots=True)
class ExecTemplate:
    template_id: str
    stop_style: str
    management_spec: ManagementSpec


@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_id: str
    context_archetype: str
    family: str
    max_minute_offset: int
    min_pullback_frac: float
    max_pullback_frac: float
    max_seller_pressure: float
    min_buyer_score: float
    min_body_ratio: float
    min_volume_ratio: float
    min_close_pos: float
    require_close_above_trigger_high: bool
    require_structure_break: bool
    min_break_buffer_frac: float
    min_bars_since_low: int
    max_cluster_range_frac: float
    min_hold_above_high_share: float


def _wave_present(frame: pd.DataFrame) -> pd.Series:
    buyer = pd.to_numeric(frame.get("buyer_wave_match_score_30m"), errors="coerce")
    return buyer >= 0.80


def _exec_templates() -> tuple[ExecTemplate, ...]:
    return (
        ExecTemplate(
            "nextopen_state_low_fx20",
            "state_low",
            ManagementSpec("fx20", 2.0, None, 0.0, False, False, None, None, 8, 0.25, 120),
        ),
        ExecTemplate(
            "nextopen_state_low_be10trail",
            "state_low",
            ManagementSpec("be10_trail", None, None, 0.0, False, False, 1.0, 1.0, 8, 0.25, 120),
        ),
        ExecTemplate(
            "nextopen_bar_low_fx20",
            "bar_low",
            ManagementSpec("fx20", 2.0, None, 0.0, False, False, None, None, 8, 0.25, 120),
        ),
        ExecTemplate(
            "nextopen_bar_low_be10trail",
            "bar_low",
            ManagementSpec("be10_trail", None, None, 0.0, False, False, 1.0, 1.0, 8, 0.25, 120),
        ),
    )


def _rule_specs() -> tuple[RuleSpec, ...]:
    rules: list[RuleSpec] = []

    for minute in (2, 3, 4):
        for seller in (0.03, 0.05, 0.08):
            for buyer in (0.20, 0.24, 0.30):
                for pullback in (0.10, 0.20, 0.25):
                    rules.append(
                        RuleSpec(
                            rule_id=f"warm_reaccel_m{minute}_s{int(seller*100):02d}_b{int(buyer*100):02d}_p{int(pullback*100):02d}",
                            context_archetype="context_warm",
                            family="warm_reaccel",
                            max_minute_offset=minute,
                            min_pullback_frac=0.0,
                            max_pullback_frac=pullback,
                            max_seller_pressure=seller,
                            min_buyer_score=buyer,
                            min_body_ratio=0.80,
                            min_volume_ratio=0.80,
                            min_close_pos=0.65,
                            require_close_above_trigger_high=True,
                            require_structure_break=False,
                            min_break_buffer_frac=0.0,
                            min_bars_since_low=0,
                            max_cluster_range_frac=1.0,
                            min_hold_above_high_share=0.0,
                        )
                    )

    for minute in (3, 4, 5):
        for seller in (0.03, 0.05, 0.08):
            for buyer in (0.20, 0.24, 0.30):
                for hold in (0.25, 0.50):
                    rules.append(
                        RuleSpec(
                            rule_id=f"warm_hold_m{minute}_s{int(seller*100):02d}_b{int(buyer*100):02d}_h{int(hold*100):02d}",
                            context_archetype="context_warm",
                            family="warm_hold_high",
                            max_minute_offset=minute,
                            min_pullback_frac=0.0,
                            max_pullback_frac=0.20,
                            max_seller_pressure=seller,
                            min_buyer_score=buyer,
                            min_body_ratio=0.50,
                            min_volume_ratio=0.50,
                            min_close_pos=0.55,
                            require_close_above_trigger_high=True,
                            require_structure_break=False,
                            min_break_buffer_frac=0.0,
                            min_bars_since_low=0,
                            max_cluster_range_frac=0.25,
                            min_hold_above_high_share=hold,
                        )
                    )

    for minute in (3, 4, 5):
        for seller in (0.03, 0.05, 0.08):
            for buyer in (0.20, 0.24, 0.30):
                for pullback in (0.20, 0.25, 0.35):
                    rules.append(
                        RuleSpec(
                            rule_id=f"over_reclaim_m{minute}_s{int(seller*100):02d}_b{int(buyer*100):02d}_p{int(pullback*100):02d}",
                            context_archetype="context_overheated",
                            family="overheated_shallow_reclaim",
                            max_minute_offset=minute,
                            min_pullback_frac=0.08,
                            max_pullback_frac=pullback,
                            max_seller_pressure=seller,
                            min_buyer_score=buyer,
                            min_body_ratio=0.70,
                            min_volume_ratio=0.70,
                            min_close_pos=0.60,
                            require_close_above_trigger_high=True,
                            require_structure_break=False,
                            min_break_buffer_frac=0.0,
                            min_bars_since_low=0,
                            max_cluster_range_frac=0.35,
                            min_hold_above_high_share=0.0,
                        )
                    )

    for minute in (4, 5, 6):
        for seller in (0.03, 0.05, 0.08):
            for buyer in (0.20, 0.24, 0.30):
                for buffer_frac in (0.05, 0.12, 0.20):
                    rules.append(
                        RuleSpec(
                            rule_id=f"struct_break_m{minute}_s{int(seller*100):02d}_b{int(buyer*100):02d}_k{int(buffer_frac*100):02d}",
                            context_archetype="context_overheated",
                            family="overheated_structure_break",
                            max_minute_offset=minute,
                            min_pullback_frac=0.08,
                            max_pullback_frac=0.35,
                            max_seller_pressure=seller,
                            min_buyer_score=buyer,
                            min_body_ratio=0.70,
                            min_volume_ratio=0.70,
                            min_close_pos=0.58,
                            require_close_above_trigger_high=False,
                            require_structure_break=True,
                            min_break_buffer_frac=buffer_frac,
                            min_bars_since_low=1,
                            max_cluster_range_frac=0.25,
                            min_hold_above_high_share=0.0,
                        )
                    )

    return tuple(rules)


def _scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = _prepare_scope(frame).copy()
    scoped = scoped[scoped["category_id"].astype(str).isin({"warm_continuation", "overheated_retest"})].copy()
    scoped["wave_present"] = _wave_present(scoped)
    return scoped.reset_index(drop=True)


def _state_rows_for_event(event_row: pd.Series, cache: dict[tuple[str, str], pd.DataFrame]) -> list[dict[str, object]]:
    cache_scope = str(event_row["cache_scope"])
    symbol = str(event_row["symbol"])
    m1 = _load_m1(cache_scope, symbol, cache)
    if m1.empty:
        return []

    timestamps = m1["timestamp"].astype("int64").tolist()
    opens = m1["open"].astype(float).tolist()
    highs = m1["high"].astype(float).tolist()
    lows = m1["low"].astype(float).tolist()
    closes = m1["close"].astype(float).tolist()
    volumes = m1["volume"].astype(float).tolist()

    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return []

    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_open = float(event_row["trigger_open"])
    trigger_close = float(event_row["trigger_close"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    trigger_body = max(1e-12, abs(trigger_close - trigger_open))
    avg_trigger_body_1m = trigger_body / 5.0
    avg_trigger_vol_1m = max(1e-12, float(event_row["trigger_volume"]) / 5.0)

    rows: list[dict[str, object]] = []
    rolling_low = float("inf")
    rolling_high = trigger_high
    seller_pressure_cum = 0.0
    buyer_pressure_cum = 0.0
    new_low_count = 0

    end_idx = min(len(closes), start_idx + 10)
    for idx in range(start_idx, end_idx):
        if lows[idx] < rolling_low:
            rolling_low = lows[idx]
            new_low_count += 1
        rolling_high = max(rolling_high, highs[idx])
        if closes[idx] < opens[idx]:
            seller_pressure_cum += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        elif closes[idx] > opens[idx]:
            buyer_pressure_cum += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)

        minute_offset = idx - start_idx + 1
        current_body_ratio = max(0.0, closes[idx] - opens[idx]) / avg_trigger_body_1m if avg_trigger_body_1m > 0.0 else 0.0
        current_volume_ratio = volumes[idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0.0 else 0.0
        current_buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m) if closes[idx] > opens[idx] else 0.0
        current_seller_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m) if closes[idx] < opens[idx] else 0.0

        lows_so_far = lows[start_idx : idx + 1]
        highs_so_far = highs[start_idx : idx + 1]
        closes_so_far = closes[start_idx : idx + 1]
        opens_so_far = opens[start_idx : idx + 1]
        low_idx = start_idx + lows_so_far.index(min(lows_so_far))
        bars_since_low = idx - low_idx
        after_low_highs = highs[low_idx + 1 : idx] if idx > low_idx + 1 else []
        structure_high = max(after_low_highs) if after_low_highs else trigger_high
        structure_break_now = closes[idx] > structure_high and idx > low_idx + 1
        break_buffer_frac = max(0.0, (closes[idx] - structure_high) / trigger_range)
        cluster_range_frac = max(0.0, (max(highs[low_idx : idx + 1]) - min(lows[low_idx : idx + 1])) / trigger_range)
        hold_above_high_share = (
            sum(1 for value in closes_so_far if value > trigger_high) / len(closes_so_far)
            if closes_so_far
            else 0.0
        )
        red_share = sum(1 for i in range(start_idx, idx + 1) if closes[i] < opens[i]) / (idx - start_idx + 1)
        green_share = sum(1 for i in range(start_idx, idx + 1) if closes[i] > opens[i]) / (idx - start_idx + 1)

        row: dict[str, object] = {
            "dataset": event_row["dataset"],
            "symbol": symbol,
            "timestamp_ms": event_row["timestamp_ms"],
            "event_id": f"{event_row['dataset']}|{symbol}|{int(event_row['timestamp_ms'])}",
            "month_utc": event_row["month_utc"],
            "date_utc": event_row["date_utc"],
            "context_archetype": event_row["context_archetype"],
            "pre_accumulation_type": event_row["pre_accumulation_type"],
            "wave_present": bool(event_row["wave_present"]),
            "minute_offset": int(minute_offset),
            "trigger_return_pct": float(event_row["trigger_return_pct"]),
            "range_atr": float(event_row["range_atr"]),
            "body_atr": float(event_row["body_atr"]),
            "volume_mult": float(event_row["volume_mult"]),
            "pre_base_range_pct_60m": float(event_row["pre_base_range_pct_60m"]),
            "pre_base_drift_pct_60m": float(event_row["pre_base_drift_pct_60m"]),
            "pullback_so_far_frac": float(max(0.0, (trigger_high - min(lows_so_far)) / trigger_range)),
            "extension_so_far_frac": float(max(0.0, (max(highs_so_far) - trigger_high) / trigger_range)),
            "seller_pressure_cum": float(seller_pressure_cum),
            "buyer_pressure_cum": float(buyer_pressure_cum),
            "current_buyer_score": float(current_buyer_score),
            "current_seller_score": float(current_seller_score),
            "current_body_ratio": float(current_body_ratio),
            "current_volume_ratio": float(current_volume_ratio),
            "current_close_pos": float(_close_pos(highs[idx], lows[idx], closes[idx])),
            "current_close_above_trigger_high": bool(closes[idx] > trigger_high),
            "current_close_above_trigger_open": bool(closes[idx] > trigger_open),
            "current_close_vs_trigger_high_frac": float((closes[idx] - trigger_high) / trigger_range),
            "entry_level_frac": float((closes[idx] - trigger_low) / trigger_range),
            "bars_since_low": int(bars_since_low),
            "new_low_count": int(new_low_count),
            "cluster_range_frac": float(cluster_range_frac),
            "hold_above_high_share": float(hold_above_high_share),
            "red_bar_share": float(red_share),
            "green_bar_share": float(green_share),
            "structure_high_frac": float((structure_high - trigger_low) / trigger_range),
            "structure_break_now": bool(structure_break_now),
            "break_buffer_frac": float(break_buffer_frac),
            "state_bar_low_frac": float((lows[idx] - trigger_low) / trigger_range),
            "state_bar_high_frac": float((highs[idx] - trigger_low) / trigger_range),
        }

        if idx + 1 >= len(opens):
            rows.append(row)
            continue

        entry_idx = idx + 1
        entry_price = float(opens[entry_idx])
        state_low = float(min(lows_so_far))
        bar_low = float(lows[idx])
        for template in _exec_templates():
            stop_price = state_low if template.stop_style == "state_low" else bar_low
            if stop_price >= entry_price:
                row[f"{template.template_id}_ret"] = float("nan")
                row[f"{template.template_id}_valid"] = False
                row[f"{template.template_id}_entry_price"] = float("nan")
                row[f"{template.template_id}_stop"] = float("nan")
                continue
            exit_idx, exit_return_pct, exit_reason, partial_taken = _simulate_managed_exit(
                highs=highs,
                lows=lows,
                closes=closes,
                entry_idx=entry_idx,
                entry_price=entry_price,
                stop_price=stop_price,
                management_spec=template.management_spec,
            )
            row[f"{template.template_id}_ret"] = float(exit_return_pct)
            row[f"{template.template_id}_valid"] = True
            row[f"{template.template_id}_entry_price"] = float(entry_price)
            row[f"{template.template_id}_stop"] = float(stop_price)
            row[f"{template.template_id}_exit_reason"] = str(exit_reason)
            row[f"{template.template_id}_partial"] = int(partial_taken)
            row[f"{template.template_id}_entry_timestamp_ms"] = int(timestamps[entry_idx])
            row[f"{template.template_id}_exit_timestamp_ms"] = int(timestamps[exit_idx] + ONE_MINUTE_MS)

        rows.append(row)
    return rows


def _build_state_database(scoped: pd.DataFrame) -> pd.DataFrame:
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    for row in scoped.to_dict("records"):
        rows.extend(_state_rows_for_event(pd.Series(row), cache))
    return pd.DataFrame(rows)


def _template_summary(states: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for context in sorted(states["context_archetype"].astype(str).unique()):
        scoped = states[states["context_archetype"].astype(str) == context].copy()
        for minute in sorted(pd.to_numeric(scoped["minute_offset"], errors="coerce").dropna().astype(int).unique()):
            part = scoped[pd.to_numeric(scoped["minute_offset"], errors="coerce") == int(minute)].copy()
            for template in _exec_templates():
                valid_col = f"{template.template_id}_valid"
                ret_col = f"{template.template_id}_ret"
                valid = part[part[valid_col].astype(bool)].copy() if valid_col in part.columns else pd.DataFrame()
                if valid.empty:
                    continue
                rows.append(
                    {
                        "context_archetype": context,
                        "minute_offset": int(minute),
                        "template_id": template.template_id,
                        "states": int(len(valid)),
                        "wave_present_rate": float(valid["wave_present"].astype(bool).mean()),
                        "mean_return_pct": float(pd.to_numeric(valid[ret_col], errors="coerce").mean()),
                        "win_rate": float((pd.to_numeric(valid[ret_col], errors="coerce") > 0.0).mean()),
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["context_archetype", "template_id", "minute_offset"]
    ).reset_index(drop=True)


def _rule_mask(frame: pd.DataFrame, rule: RuleSpec) -> pd.Series:
    mask = (
        (frame["context_archetype"].astype(str) == str(rule.context_archetype))
        & (pd.to_numeric(frame["minute_offset"], errors="coerce") <= rule.max_minute_offset)
        & (pd.to_numeric(frame["pullback_so_far_frac"], errors="coerce") >= rule.min_pullback_frac)
        & (pd.to_numeric(frame["pullback_so_far_frac"], errors="coerce") <= rule.max_pullback_frac)
        & (pd.to_numeric(frame["seller_pressure_cum"], errors="coerce") <= rule.max_seller_pressure)
        & (pd.to_numeric(frame["current_buyer_score"], errors="coerce") >= rule.min_buyer_score)
        & (pd.to_numeric(frame["current_body_ratio"], errors="coerce") >= rule.min_body_ratio)
        & (pd.to_numeric(frame["current_volume_ratio"], errors="coerce") >= rule.min_volume_ratio)
        & (pd.to_numeric(frame["current_close_pos"], errors="coerce") >= rule.min_close_pos)
        & (pd.to_numeric(frame["bars_since_low"], errors="coerce") >= rule.min_bars_since_low)
        & (pd.to_numeric(frame["cluster_range_frac"], errors="coerce") <= rule.max_cluster_range_frac)
        & (pd.to_numeric(frame["hold_above_high_share"], errors="coerce") >= rule.min_hold_above_high_share)
    )
    if rule.require_close_above_trigger_high:
        mask &= frame["current_close_above_trigger_high"].astype(bool)
    if rule.require_structure_break:
        mask &= frame["structure_break_now"].astype(bool)
        mask &= pd.to_numeric(frame["break_buffer_frac"], errors="coerce") >= rule.min_break_buffer_frac
    return mask


def _summarize_rule_for_template(states: pd.DataFrame, rule: RuleSpec, template: ExecTemplate) -> dict[str, object] | None:
    valid_col = f"{template.template_id}_valid"
    ret_col = f"{template.template_id}_ret"
    if valid_col not in states.columns or ret_col not in states.columns:
        return None
    mask = _rule_mask(states, rule) & states[valid_col].astype(bool)
    scoped = states[mask].copy()
    if scoped.empty:
        return None
    scoped = scoped.sort_values(["dataset", "symbol", "timestamp_ms", "minute_offset"]).copy()
    if "event_id" in scoped.columns:
        scoped = scoped.drop_duplicates(subset=["event_id"], keep="first").copy()

    current = scoped[scoped["dataset"].astype(str) == "current"].copy()
    old = scoped[scoped["dataset"].astype(str) == "old"].copy()

    def _equity(frame: pd.DataFrame) -> tuple[float | None, float | None]:
        if frame.empty:
            return None, None
        events = frame.copy()
        events["exit_return_pct"] = pd.to_numeric(events[ret_col], errors="coerce")
        events["entry_timestamp_ms"] = pd.to_numeric(events[f"{template.template_id}_entry_timestamp_ms"], errors="coerce")
        events["exit_timestamp_ms"] = pd.to_numeric(events[f"{template.template_id}_exit_timestamp_ms"], errors="coerce")
        events["entry_price"] = pd.to_numeric(events[f"{template.template_id}_entry_price"], errors="coerce")
        events["stop_price"] = pd.to_numeric(events[f"{template.template_id}_stop"], errors="coerce")
        events["initial_risk_pct"] = (
            (events["entry_price"] - events["stop_price"]) / events["entry_price"]
        )
        eq, _ = _simulate_equity_risk_metrics(
            events,
            calendar_months=_calendar_months_from_frames(events),
            risk_fraction=0.05,
        )
        return (
            float(eq.get("equity_annualized_return_pct", 0.0)),
            float(eq.get("equity_max_drawdown_pct", 0.0)),
        )

    current_eq, current_dd = _equity(current)
    old_eq, old_dd = _equity(old)

    total_return = float(pd.to_numeric(scoped[ret_col], errors="coerce").sum())
    top_symbol_share = None
    best_month_share = None
    if total_return > 0.0:
        by_symbol = scoped.groupby("symbol", as_index=False)[ret_col].sum()
        top_symbol_share = float(by_symbol[ret_col].max() / total_return)
        by_month = scoped.groupby("month_utc", as_index=False)[ret_col].sum()
        best_month_share = float(by_month[ret_col].max() / total_return)

    current_mean = float(pd.to_numeric(current[ret_col], errors="coerce").mean()) if not current.empty else None
    old_mean = float(pd.to_numeric(old[ret_col], errors="coerce").mean()) if not old.empty else None

    positives = [v for v in [current_mean, old_mean] if v is not None]
    min_mean = min(positives) if positives else None
    positives_eq = [v for v in [current_eq, old_eq] if v is not None]
    min_eq = min(positives_eq) if positives_eq else None
    dds = [v for v in [current_dd, old_dd] if v is not None]
    max_dd = max(dds) if dds else None
    min_trades = min(len(current), len(old))

    score = (
        (0.0 if min_mean is None else min_mean * 260.0)
        + (0.0 if min_eq is None else min_eq * 30.0)
        - (0.0 if max_dd is None else max_dd * 20.0)
        + min_trades * 0.35
        - (0.0 if top_symbol_share is None else float(top_symbol_share) * 18.0)
        - (0.0 if best_month_share is None else float(best_month_share) * 10.0)
    )
    status = "not_ready"
    if (
        len(current) >= 14
        and len(old) >= 8
        and current_mean is not None
        and old_mean is not None
        and current_mean > 0.0
        and old_mean > 0.0
    ):
        status = "surviving"

    return {
        "rule_id": rule.rule_id,
        "family": rule.family,
        "context_archetype": rule.context_archetype,
        "template_id": template.template_id,
        "current_states": int(len(current)),
        "old_states": int(len(old)),
        "current_wave_present_rate": float(current["wave_present"].astype(bool).mean()) if not current.empty else None,
        "old_wave_present_rate": float(old["wave_present"].astype(bool).mean()) if not old.empty else None,
        "current_mean_return_pct": current_mean,
        "old_mean_return_pct": old_mean,
        "current_win_rate": float((pd.to_numeric(current[ret_col], errors="coerce") > 0.0).mean()) if not current.empty else None,
        "old_win_rate": float((pd.to_numeric(old[ret_col], errors="coerce") > 0.0).mean()) if not old.empty else None,
        "current_equity_annualized_return_pct_5": current_eq,
        "old_equity_annualized_return_pct_5": old_eq,
        "current_equity_max_drawdown_pct_5": current_dd,
        "old_equity_max_drawdown_pct_5": old_dd,
        "top1_symbol_share": top_symbol_share,
        "best_month_share": best_month_share,
        "score": float(score),
        "selection_status": status,
    }


def _search_rules(states: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for rule in _rule_specs():
        for template in _exec_templates():
            row = _summarize_rule_for_template(states, rule, template)
            if row is not None:
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["selection_status", "score"], ascending=[True, False]
    ).reset_index(drop=True)


def _build_report(template_summary: pd.DataFrame, rule_summary: pd.DataFrame) -> str:
    def table(df: pd.DataFrame) -> str:
        if df.empty:
            return "_empty_"
        return "```\n" + df.to_string(index=False) + "\n```"

    lines: list[str] = []
    lines.append("# Decision-Time State Lab")
    lines.append("")
    lines.append("Одна строка в БД = одно состояние пампа в конкретную минуту после закрытия аномальной `5m`.")
    lines.append("Все признаки доступны только к этой минуте; вход считается от следующего открытия `1m`.")
    lines.append("")
    lines.append("## Template Summary")
    lines.append("")
    lines.append(table(template_summary.head(40)))
    lines.append("")
    lines.append("## Top Surviving Rules")
    lines.append("")
    lines.append(table(rule_summary[rule_summary["selection_status"].astype(str) == "surviving"].head(20)))
    lines.append("")
    lines.append("## Top Overall Rules")
    lines.append("")
    lines.append(table(rule_summary.head(30)))
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped = _scope(_load_feature_db())
    states = _build_state_database(scoped)
    states_path = OUTPUT_DIR / "decision_time_states.csv"
    states.to_csv(states_path, index=False)

    template_summary = _template_summary(states)
    template_summary_path = OUTPUT_DIR / "template_summary.csv"
    template_summary.to_csv(template_summary_path, index=False)

    rule_summary = _search_rules(states)
    rule_summary_path = OUTPUT_DIR / "rule_summary.csv"
    rule_summary.to_csv(rule_summary_path, index=False)

    selected = rule_summary[rule_summary["selection_status"].astype(str) == "surviving"].copy() if not rule_summary.empty else pd.DataFrame()
    selected_path = OUTPUT_DIR / "selected_rules.csv"
    selected.to_csv(selected_path, index=False)

    report_path = OUTPUT_DIR / "report.md"
    report_path.write_text(_build_report(template_summary, rule_summary), encoding="utf-8")
    print(f"Saved: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
