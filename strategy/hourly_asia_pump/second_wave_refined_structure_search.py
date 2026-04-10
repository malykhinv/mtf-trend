from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _close_pos,
    _load_feature_db,
    _load_m1,
    _net_long_return,
    _prepare_scope,
    _pressure_score,
)
from strategy.hourly_asia_pump.second_wave_execution_search import (
    ManagementSpec,
    _simulate_managed_exit,
)
from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _summarize_events,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_refined_structure_search"


@dataclass(frozen=True, slots=True)
class BreakSignalSpec:
    signal_id: str
    context_archetype: str
    max_wait_minutes: int
    min_pullback_frac: float
    max_pullback_frac: float
    min_stabilization_bars: int
    max_cluster_range_frac: float
    min_break_body_ratio: float
    min_break_volume_ratio: float
    min_close_pos: float
    min_break_buffer_frac: float
    max_seller_pressure: float
    min_buyer_break_score: float
    min_entry_level_frac: float


@dataclass(frozen=True, slots=True)
class EntrySpec:
    entry_id: str
    style: str
    valid_minutes: int
    pullback_frac: float | None = None


@dataclass(frozen=True, slots=True)
class StopSpec:
    stop_id: str
    style: str


@dataclass(frozen=True, slots=True)
class SearchModel:
    model_id: str
    signal_spec: BreakSignalSpec
    entry_spec: EntrySpec
    stop_spec: StopSpec
    management_spec: ManagementSpec


@dataclass(frozen=True, slots=True)
class BreakSignal:
    break_idx: int
    cluster_low: float
    structure_high: float
    pullback_frac: float
    cluster_range_frac: float
    bars_since_low: int
    break_body_ratio: float
    break_volume_ratio: float
    break_close_pos: float
    break_buffer_frac: float
    seller_pressure_score: float
    buyer_break_score: float
    entry_level_frac: float
    detail: str


def _scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = _prepare_scope(frame).copy()
    scoped = scoped[scoped["category_id"].astype(str).isin({"warm_continuation", "overheated_retest"})].copy()
    return scoped.reset_index(drop=True)


def _signal_specs() -> tuple[BreakSignalSpec, ...]:
    return (
        BreakSignalSpec("w_core_a", "context_warm", 6, 0.05, 0.25, 1, 0.15, 0.80, 0.80, 0.65, 0.20, 0.03, 0.30, 0.60),
        BreakSignalSpec("w_core_b", "context_warm", 6, 0.05, 0.20, 1, 0.15, 0.80, 0.80, 0.65, 0.20, 0.05, 0.30, 0.60),
        BreakSignalSpec("w_core_c", "context_warm", 6, 0.05, 0.25, 2, 0.22, 0.80, 0.80, 0.65, 0.20, 0.03, 0.24, 0.60),
        BreakSignalSpec("o_core_a", "context_overheated", 10, 0.10, 0.20, 1, 0.18, 0.80, 0.80, 0.60, 0.20, 0.03, 0.30, 0.50),
        BreakSignalSpec("o_core_b", "context_overheated", 10, 0.10, 0.25, 1, 0.18, 0.80, 0.80, 0.60, 0.20, 0.05, 0.30, 0.50),
        BreakSignalSpec("o_core_c", "context_overheated", 10, 0.10, 0.25, 2, 0.25, 0.80, 0.80, 0.60, 0.20, 0.03, 0.24, 0.50),
        BreakSignalSpec("o_core_d", "context_overheated", 10, 0.10, 0.35, 2, 0.25, 0.80, 0.80, 0.60, 0.20, 0.03, 0.30, 0.50),
    )


def _entry_specs() -> tuple[EntrySpec, ...]:
    return (
        EntrySpec("struct_close", "struct_close", 0),
        EntrySpec("next_open", "next_open", 1),
        EntrySpec("pull25", "pullback_limit", 2, 0.25),
    )


def _stop_specs() -> tuple[StopSpec, ...]:
    return (
        StopSpec("cluster_low", "cluster_low"),
        StopSpec("break_body_low", "break_body_low"),
    )


def _management_specs() -> tuple[ManagementSpec, ...]:
    return (
        ManagementSpec("fx20", 2.0, None, 0.0, False, False, None, None, 8, 0.25, 120),
        ManagementSpec("be10_trail", None, None, 0.0, False, False, 1.0, 1.0, 8, 0.25, 120),
    )


def _build_models() -> tuple[SearchModel, ...]:
    models: list[SearchModel] = []
    for signal_spec in _signal_specs():
        for entry_spec in _entry_specs():
            for stop_spec in _stop_specs():
                for management_spec in _management_specs():
                    models.append(
                        SearchModel(
                            model_id=f"{signal_spec.signal_id}__{entry_spec.entry_id}__{stop_spec.stop_id}__{management_spec.management_id}",
                            signal_spec=signal_spec,
                            entry_spec=entry_spec,
                            stop_spec=stop_spec,
                            management_spec=management_spec,
                        )
                    )
    return tuple(models)


def _detect_break_signal(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    spec: BreakSignalSpec,
) -> BreakSignal | None:
    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return None

    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    trigger_body = max(1e-12, abs(float(event_row["trigger_close"]) - float(event_row["trigger_open"])))
    avg_trigger_body_1m = trigger_body / 5.0
    avg_trigger_vol_1m = max(1e-12, float(event_row["trigger_volume"]) / 5.0)
    end_idx = min(len(closes), start_idx + spec.max_wait_minutes)

    for idx in range(start_idx + spec.min_stabilization_bars + 1, end_idx):
        lows_slice = lows[start_idx:idx]
        if not lows_slice:
            continue
        lowest_rel = lows_slice.index(min(lows_slice))
        lowest_idx = start_idx + lowest_rel
        if idx - lowest_idx < spec.min_stabilization_bars + 1:
            continue
        after_low_highs = highs[lowest_idx + 1 : idx]
        if not after_low_highs:
            continue

        cluster_low = min(lows_slice)
        pullback_frac = max(0.0, (trigger_high - cluster_low) / trigger_range)
        if pullback_frac < spec.min_pullback_frac or pullback_frac > spec.max_pullback_frac:
            continue

        structure_high = max(after_low_highs)
        cluster_range_frac = max(0.0, (structure_high - cluster_low) / trigger_range)
        if cluster_range_frac > spec.max_cluster_range_frac:
            continue

        if closes[idx] <= opens[idx]:
            continue
        body_ratio = max(0.0, closes[idx] - opens[idx]) / avg_trigger_body_1m if avg_trigger_body_1m > 0.0 else 0.0
        volume_ratio = volumes[idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0.0 else 0.0
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        if body_ratio < spec.min_break_body_ratio:
            continue
        if volume_ratio < spec.min_break_volume_ratio:
            continue
        if close_pos < spec.min_close_pos:
            continue

        break_buffer_frac = max(0.0, (closes[idx] - structure_high) / trigger_range)
        if break_buffer_frac < spec.min_break_buffer_frac:
            continue

        seller_pressure = 0.0
        for j in range(lowest_idx, idx):
            if closes[j] < opens[j]:
                seller_pressure += _pressure_score(opens[j], closes[j], volumes[j], trigger_range, avg_trigger_vol_1m)
        if seller_pressure > spec.max_seller_pressure:
            continue

        buyer_break_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        if buyer_break_score < spec.min_buyer_break_score:
            continue

        entry_level_frac = (closes[idx] - trigger_low) / trigger_range
        if entry_level_frac < spec.min_entry_level_frac:
            continue

        return BreakSignal(
            break_idx=idx,
            cluster_low=float(cluster_low),
            structure_high=float(structure_high),
            pullback_frac=float(pullback_frac),
            cluster_range_frac=float(cluster_range_frac),
            bars_since_low=int(idx - lowest_idx),
            break_body_ratio=float(body_ratio),
            break_volume_ratio=float(volume_ratio),
            break_close_pos=float(close_pos),
            break_buffer_frac=float(break_buffer_frac),
            seller_pressure_score=float(seller_pressure),
            buyer_break_score=float(buyer_break_score),
            entry_level_frac=float(entry_level_frac),
            detail="refined_structure_break",
        )
    return None


def _find_entry(
    signal: BreakSignal,
    entry_spec: EntrySpec,
    opens: list[float],
    lows: list[float],
    highs: list[float],
    closes: list[float],
) -> tuple[int, float, str] | None:
    break_idx = signal.break_idx
    if entry_spec.style == "struct_close":
        return break_idx, float(highs[break_idx] - (highs[break_idx] - signal.structure_high) * 0.0) if False else float("nan"), ""
    if entry_spec.style == "next_open":
        next_idx = break_idx + 1
        if next_idx >= len(opens):
            return None
        return next_idx, float(opens[next_idx]), "next_open"
    if entry_spec.style == "pullback_limit" and entry_spec.pullback_frac is not None:
        break_bar_range = max(1e-12, highs[break_idx] - lows[break_idx])
        limit_price = float(closes[break_idx] - break_bar_range * entry_spec.pullback_frac)
        for idx in range(break_idx + 1, min(len(lows), break_idx + 1 + entry_spec.valid_minutes)):
            if lows[idx] <= limit_price:
                return idx, limit_price, f"pullback_{int(entry_spec.pullback_frac*100):02d}"
        return None
    return None


def _struct_close_entry(signal: BreakSignal) -> tuple[int, float, str]:
    return signal.break_idx, float("nan"), "struct_close"


def _stop_price(signal: BreakSignal, stop_spec: StopSpec, opens: list[float], closes: list[float]) -> float:
    if stop_spec.style == "cluster_low":
        return float(signal.cluster_low)
    if stop_spec.style == "break_body_low":
        idx = signal.break_idx
        return float(min(opens[idx], closes[idx]))
    raise ValueError(stop_spec.style)


def _entry_tuple(signal: BreakSignal, entry_spec: EntrySpec, opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> tuple[int, float, str] | None:
    if entry_spec.style == "struct_close":
        return signal.break_idx, float(closes[signal.break_idx]), "struct_close"
    return _find_entry(signal, entry_spec, opens, lows, highs, closes)


def _simulate_event(
    *,
    event_row: pd.Series,
    model: SearchModel,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> dict[str, object] | None:
    if str(event_row["context_archetype"]) != model.signal_spec.context_archetype:
        return None
    signal = _detect_break_signal(
        event_row=event_row,
        timestamps=timestamps,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        spec=model.signal_spec,
    )
    if signal is None:
        return None
    entry = _entry_tuple(signal, model.entry_spec, opens, highs, lows, closes)
    if entry is None:
        return None
    entry_idx, entry_price, entry_detail = entry
    stop_price = _stop_price(signal, model.stop_spec, opens, closes)
    if stop_price >= entry_price:
        return None
    exit_idx, exit_return_pct, exit_reason, partial_taken = _simulate_managed_exit(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_idx=entry_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        management_spec=model.management_spec,
    )
    return {
        "entry_timestamp_ms": int(timestamps[entry_idx] + ONE_MINUTE_MS),
        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_price": float(closes[exit_idx]),
        "exit_reason": exit_reason,
        "exit_return_pct": float(exit_return_pct),
        "entry_detail": entry_detail,
        "partial_taken": int(partial_taken),
        "signal_pullback_frac": float(signal.pullback_frac),
        "signal_cluster_range_frac": float(signal.cluster_range_frac),
        "signal_bars_since_low": int(signal.bars_since_low),
        "signal_break_buffer_frac": float(signal.break_buffer_frac),
        "seller_pressure_score": float(signal.seller_pressure_score),
        "buyer_break_score": float(signal.buyer_break_score),
        "entry_level_frac": float(signal.entry_level_frac),
    }


def _build_events(frame: pd.DataFrame) -> pd.DataFrame:
    models = _build_models()
    model_map: dict[str, list[SearchModel]] = {}
    for model in models:
        model_map.setdefault(model.signal_spec.context_archetype, []).append(model)

    cache: dict[tuple[str, str], pd.DataFrame] = {}
    events: list[dict[str, object]] = []
    for row in frame.to_dict("records"):
        event_row = pd.Series(row)
        m1 = _load_m1(str(row["cache_scope"]), str(row["symbol"]), cache)
        if m1.empty:
            continue
        timestamps = m1["timestamp"].astype("int64").tolist()
        opens = m1["open"].astype(float).tolist()
        highs = m1["high"].astype(float).tolist()
        lows = m1["low"].astype(float).tolist()
        closes = m1["close"].astype(float).tolist()
        volumes = m1["volume"].astype(float).tolist()
        for model in model_map.get(str(row["context_archetype"]), []):
            simulated = _simulate_event(
                event_row=event_row,
                model=model,
                timestamps=timestamps,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
            )
            if simulated is None:
                continue
            events.append({**row, **simulated, "model_id": model.model_id, "context_group": model.signal_spec.context_archetype})
    return pd.DataFrame(events)


def _monthly(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month_utc", "month_return_pct", "trades"])
    monthly = (
        frame.groupby("month_utc", as_index=False)
        .agg(month_return_pct=("exit_return_pct", "sum"), trades=("exit_return_pct", "size"))
        .sort_values("month_utc")
        .reset_index(drop=True)
    )
    return monthly


def _summarize_model(frame: pd.DataFrame, model_id: str) -> dict[str, object] | None:
    scoped = frame[frame["model_id"].astype(str) == str(model_id)].copy()
    if scoped.empty:
        return None

    datasets: dict[str, dict[str, float | int]] = {}
    for dataset in ("current", "old"):
        part = scoped[scoped["dataset"].astype(str) == dataset].copy()
        if part.empty:
            datasets[dataset] = {
                "trades": 0,
                "mean": float("nan"),
                "equity": float("nan"),
                "dd": float("nan"),
            }
            continue
        eq, _ = _simulate_equity_risk_metrics(
            part,
            calendar_months=_calendar_months_from_frames(part),
            risk_fraction=0.05,
        )
        datasets[dataset] = {
            "trades": int(len(part)),
            "mean": float(pd.to_numeric(part["exit_return_pct"], errors="coerce").mean()),
            "equity": float(eq.get("annualized_return_pct", 0.0)),
            "dd": float(eq.get("max_drawdown_pct", 0.0)),
        }

    combined_eq, _ = _simulate_equity_risk_metrics(
        scoped,
        calendar_months=_calendar_months_from_frames(scoped),
        risk_fraction=0.05,
    )
    total_return = float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").sum())
    top_symbol_share = 0.0
    if total_return != 0.0:
        by_symbol = scoped.groupby("symbol", as_index=False)["exit_return_pct"].sum()
        top_symbol_share = float(by_symbol["exit_return_pct"].max() / total_return)
    monthly = _monthly(scoped)
    best_month_share = 0.0
    if total_return != 0.0 and not monthly.empty:
        best_month_share = float(monthly["month_return_pct"].max() / total_return)

    curr_mean = datasets["current"]["mean"]
    old_mean = datasets["old"]["mean"]
    curr_eq = datasets["current"]["equity"]
    old_eq = datasets["old"]["equity"]
    curr_dd = datasets["current"]["dd"]
    old_dd = datasets["old"]["dd"]

    min_mean = min(value for value in [curr_mean, old_mean] if pd.notna(value)) if pd.notna(curr_mean) or pd.notna(old_mean) else float("nan")
    min_eq = min(value for value in [curr_eq, old_eq] if pd.notna(value)) if pd.notna(curr_eq) or pd.notna(old_eq) else float("nan")
    max_dd = max(value for value in [curr_dd, old_dd] if pd.notna(value)) if pd.notna(curr_dd) or pd.notna(old_dd) else float("nan")
    min_trades = min(int(datasets["current"]["trades"]), int(datasets["old"]["trades"]))
    score = (
        (0.0 if pd.isna(min_mean) else min_mean * 220.0)
        + (0.0 if pd.isna(min_eq) else min_eq * 35.0)
        - (0.0 if pd.isna(max_dd) else max_dd * 18.0)
        + min_trades * 0.35
        - abs(top_symbol_share) * 20.0
        - abs(best_month_share) * 10.0
    )
    status = "not_ready"
    if (
        int(datasets["current"]["trades"]) >= 8
        and int(datasets["old"]["trades"]) >= 5
        and pd.notna(curr_mean)
        and pd.notna(old_mean)
        and curr_mean > 0.0
        and old_mean > 0.0
        and pd.notna(curr_eq)
        and pd.notna(old_eq)
        and curr_eq > 0.0
        and old_eq > 0.0
    ):
        status = "surviving"

    return {
        "model_id": model_id,
        "current_trades": int(datasets["current"]["trades"]),
        "old_trades": int(datasets["old"]["trades"]),
        "combined_trades_per_year": float(len(scoped) / (len(_calendar_months_from_frames(scoped)) / 12.0)) if len(_calendar_months_from_frames(scoped)) > 0 else 0.0,
        "combined_mean_return_pct": float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").mean()),
        "combined_median_return_pct": float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").median()),
        "combined_win_rate": float((pd.to_numeric(scoped["exit_return_pct"], errors="coerce") > 0.0).mean()),
        "combined_annualized_unit_pnl_pct": float(_summarize_events(scoped).get("annualized_unit_pnl_pct", 0.0)),
        "equity_annualized_return_pct_5": float(combined_eq.get("annualized_return_pct", 0.0)),
        "equity_max_drawdown_pct_5": float(combined_eq.get("max_drawdown_pct", 0.0)),
        "current_mean_return_pct": curr_mean,
        "old_mean_return_pct": old_mean,
        "current_equity_annualized_return_pct_5": curr_eq,
        "old_equity_annualized_return_pct_5": old_eq,
        "current_equity_max_drawdown_pct_5": curr_dd,
        "old_equity_max_drawdown_pct_5": old_dd,
        "top1_symbol_share": top_symbol_share,
        "best_month_share": best_month_share,
        "score": float(score),
        "selection_status": status,
    }


def _report(summary: pd.DataFrame) -> str:
    def table(df: pd.DataFrame) -> str:
        if df.empty:
            return "_empty_"
        return "```\n" + df.to_string(index=False) + "\n```"

    lines: list[str] = []
    lines.append("# Refined Structure Break Search")
    lines.append("")
    lines.append("Что поменяли:")
    lines.append("- больше не требуем reclaim high аномальной `5m` как условие старта;")
    lines.append("- ищем именно локальный `1m` structure break после pullback;")
    lines.append("- требуем минимальный pullback, паузу без новых low, компактную базу и break вверх с запасом;")
    lines.append("- фильтруем по seller pressure и buyer break score, известным в момент входа.")
    lines.append("")
    lines.append("## Top Surviving")
    lines.append("")
    lines.append(table(summary[summary["selection_status"].astype(str) == "surviving"].head(20)))
    lines.append("")
    lines.append("## Top Overall")
    lines.append("")
    lines.append(table(summary.head(20)))
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped = _scope(_load_feature_db())
    events = _build_events(scoped)
    events_path = OUTPUT_DIR / "events.csv"
    events.to_csv(events_path, index=False)

    rows = []
    for model_id in sorted(events["model_id"].astype(str).unique()) if not events.empty else []:
        row = _summarize_model(events, model_id)
        if row is not None:
            rows.append(row)
    summary = pd.DataFrame(rows).sort_values(["selection_status", "score"], ascending=[True, False]).reset_index(drop=True) if rows else pd.DataFrame()
    summary_path = OUTPUT_DIR / "summary.csv"
    summary.to_csv(summary_path, index=False)

    selected = summary[summary["selection_status"].astype(str) == "surviving"].copy() if not summary.empty else pd.DataFrame()
    selected_path = OUTPUT_DIR / "selected_models.csv"
    selected.to_csv(selected_path, index=False)

    selected_events = events[events["model_id"].astype(str).isin(selected["model_id"].astype(str).tolist())].copy() if not selected.empty else pd.DataFrame()
    selected_events_path = OUTPUT_DIR / "selected_events.csv"
    selected_events.to_csv(selected_events_path, index=False)

    monthly_frames = []
    for model_id in selected["model_id"].astype(str).tolist() if not selected.empty else []:
        scoped_model = selected_events[selected_events["model_id"].astype(str) == model_id].copy()
        monthly = _monthly(scoped_model)
        if monthly.empty:
            continue
        monthly["model_id"] = model_id
        monthly_frames.append(monthly)
    selected_monthly = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    selected_monthly.to_csv(OUTPUT_DIR / "selected_monthly.csv", index=False)

    (OUTPUT_DIR / "report.md").write_text(_report(summary), encoding="utf-8")
    print(f"Saved: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
