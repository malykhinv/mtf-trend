from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.config import build_hourly_asia_pump_profile
from strategy.hourly_asia_pump.research import _build_timeframe_candidates
from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from strategy.hourly_asia_pump.xx00_edge_candidates import XX00_ASIA_1M_LAUNCH_CORE
from strategy.hourly_asia_pump.xx00_regime_long_short_research import (
    _calendar_months_from_frame,
    _load_m1,
    _prepare_event_state,
    _simulate_long_launch_rule,
)
from vectorbt_runner.data_preparer import DataPreparer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_edge_bias_and_cross_session_scan"
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"
ASIA_RESULTS_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_regime_long_short_research"

LOGGER = logging.getLogger("xx00-edge-bias-scan")


@dataclass(frozen=True, slots=True)
class SessionSpec:
    session_id: str
    start_hour_utc: int
    end_hour_utc: int


@dataclass(frozen=True, slots=True)
class CandidateVariant:
    variant_id: str
    label: str
    min_m0_return_pct: float
    min_m0_close_pos: float
    min_m0_volume_ratio: float
    require_break_prev240: bool
    rr_target: float


@dataclass(frozen=True, slots=True)
class ProxyFilter:
    proxy_id: str
    label: str
    require_above_ema200: bool
    min_pre_range_60m: float | None = None
    min_pre_return_60m: float | None = None
    min_pre_green_ratio_30m: float | None = None


def _session_specs() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec("europe", 8, 16),
        SessionSpec("america", 16, 0),
    )


def _candidate_variants() -> tuple[CandidateVariant, ...]:
    core = XX00_ASIA_1M_LAUNCH_CORE
    return (
        CandidateVariant(
            variant_id=core.rule_id,
            label=core.label,
            min_m0_return_pct=core.min_m0_return_pct,
            min_m0_close_pos=core.min_m0_close_pos,
            min_m0_volume_ratio=core.min_m0_volume_ratio,
            require_break_prev240=core.require_break_prev240,
            rr_target=core.rr_target,
        ),
        CandidateVariant(
            variant_id="long_launch_r010_c65_v04_p1_rr20",
            label="Neighbor: require prev240 close",
            min_m0_return_pct=0.01,
            min_m0_close_pos=0.65,
            min_m0_volume_ratio=4.0,
            require_break_prev240=True,
            rr_target=2.0,
        ),
        CandidateVariant(
            variant_id="long_launch_r015_c65_v04_p0_rr20",
            label="Neighbor: stronger minute-1 return",
            min_m0_return_pct=0.015,
            min_m0_close_pos=0.65,
            min_m0_volume_ratio=4.0,
            require_break_prev240=False,
            rr_target=2.0,
        ),
        CandidateVariant(
            variant_id="long_launch_r010_c80_v04_p0_rr20",
            label="Neighbor: stronger minute-1 close",
            min_m0_return_pct=0.01,
            min_m0_close_pos=0.80,
            min_m0_volume_ratio=4.0,
            require_break_prev240=False,
            rr_target=2.0,
        ),
    )


def _proxy_filters() -> tuple[ProxyFilter, ...]:
    return (
        ProxyFilter("all_loose_xx00", "All loose XX:00 anomalies", False),
        ProxyFilter("ema200_only", "Above EMA200 only", True),
        ProxyFilter("ema200_hotrange", "Above EMA200 + pre-range >= 5%", True, min_pre_range_60m=0.05),
        ProxyFilter("ema200_hotdrift", "Above EMA200 + pre-return60 >= 2%", True, min_pre_return_60m=0.02),
        ProxyFilter(
            "ema200_hot_union",
            "Above EMA200 + hot-range-or-drift",
            True,
            min_pre_range_60m=0.05,
            min_pre_return_60m=0.02,
        ),
        ProxyFilter(
            "ema200_hot_union_green",
            "Above EMA200 + hot-range-or-drift + green ratio",
            True,
            min_pre_range_60m=0.05,
            min_pre_return_60m=0.02,
            min_pre_green_ratio_30m=0.55,
        ),
    )


def _safe_float(value: object, *, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return default
    return float(numeric)


def _safe_label(value: object) -> str:
    text = str(value)
    return text.encode("ascii", errors="ignore").decode("ascii") or "<non-ascii>"


def _summary_row(scope_slice: pd.DataFrame, event_slice: pd.DataFrame, prefix: str) -> dict[str, object]:
    calendar_months = _calendar_months_from_frame(scope_slice)
    summary = _summarize_events(event_slice, calendar_months=calendar_months) or {}
    equity, _ = _simulate_equity_risk_metrics(event_slice, calendar_months=calendar_months, risk_fraction=0.05)
    return {
        f"{prefix}_cases": int(len(scope_slice)),
        f"{prefix}_trades": int(summary.get("trades_count", 0)),
        f"{prefix}_trade_rate": float(summary.get("trades_count", 0) / len(scope_slice)) if len(scope_slice) > 0 else 0.0,
        f"{prefix}_mean_return_pct": float(summary.get("mean_return_pct", 0.0)),
        f"{prefix}_win_rate": float(summary.get("win_rate", 0.0)),
        f"{prefix}_annualized_unit_pnl_pct": float(summary.get("annualized_unit_pnl_pct", 0.0)),
        f"{prefix}_max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0) or 0.0),
        f"{prefix}_equity_total_return_pct_5": float(equity.get("equity_total_return_pct", 0.0)),
        f"{prefix}_equity_annualized_return_pct_5": float(equity.get("equity_annualized_return_pct", 0.0)),
        f"{prefix}_equity_max_drawdown_pct_5": float(equity.get("equity_max_drawdown_pct", 0.0)),
        f"{prefix}_mean_entry_delay_minutes": float(pd.to_numeric(event_slice.get("entry_delay_minutes"), errors="coerce").mean())
        if not event_slice.empty
        else None,
    }


def _concentration_frame(event_slice: pd.DataFrame) -> pd.DataFrame:
    if event_slice.empty:
        return pd.DataFrame()
    total = float(pd.to_numeric(event_slice["exit_return_pct"], errors="coerce").sum())
    by_symbol = (
        event_slice.groupby("symbol", as_index=False)["exit_return_pct"]
        .sum()
        .sort_values("exit_return_pct", ascending=False)
        .reset_index(drop=True)
    )
    by_symbol["share_of_total"] = (
        by_symbol["exit_return_pct"] / total if abs(total) > 1e-12 else 0.0
    )
    return by_symbol


def _monthly_totals(event_slice: pd.DataFrame) -> pd.DataFrame:
    if event_slice.empty:
        return pd.DataFrame(columns=["month_utc", "month_return_pct"])
    monthly = (
        event_slice.groupby("month_utc", as_index=False)["exit_return_pct"]
        .sum()
        .rename(columns={"exit_return_pct": "month_return_pct"})
        .sort_values("month_utc")
        .reset_index(drop=True)
    )
    return monthly


def _walkforward_choice(events: pd.DataFrame, *, cohort_id: str, family: str | None = None) -> pd.DataFrame:
    scoped = events[(events["cohort_id"].astype(str) == cohort_id) & (events["dataset"].astype(str) == "current")].copy()
    if family is not None:
        scoped = scoped[scoped["family"].astype(str) == family].copy()
    months = sorted(scoped["month_utc"].dropna().astype(str).unique().tolist())
    if len(months) < 4:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for index in range(3, len(months)):
        train_months = months[:index]
        test_month = months[index]
        train = scoped[scoped["month_utc"].astype(str).isin(train_months)].copy()
        test = scoped[scoped["month_utc"].astype(str) == test_month].copy()
        scored = (
            train.groupby(["family", "rule_id"], as_index=False)["exit_return_pct"]
            .agg(["count", "mean", "sum"])
            .reset_index()
        )
        if scored.empty:
            continue
        scored = scored.sort_values(["sum", "mean", "count"], ascending=[False, False, False]).reset_index(drop=True)
        best_family = str(scored.iloc[0]["family"])
        best_rule = str(scored.iloc[0]["rule_id"])
        best_test = test[test["rule_id"].astype(str) == best_rule].copy()
        candidate_test = test[test["rule_id"].astype(str) == XX00_ASIA_1M_LAUNCH_CORE.rule_id].copy()
        rows.append(
            {
                "cohort_id": cohort_id,
                "family_scope": family or "all",
                "test_month": test_month,
                "selected_family": best_family,
                "selected_rule_id": best_rule,
                "selected_is_candidate": bool(best_rule == XX00_ASIA_1M_LAUNCH_CORE.rule_id),
                "selected_test_trades": int(len(best_test)),
                "selected_test_sum_return_pct": float(pd.to_numeric(best_test.get("exit_return_pct"), errors="coerce").sum()),
                "candidate_test_trades": int(len(candidate_test)),
                "candidate_test_sum_return_pct": float(pd.to_numeric(candidate_test.get("exit_return_pct"), errors="coerce").sum()),
            }
        )
    return pd.DataFrame(rows)


def _load_asia_research() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    coverage = pd.read_csv(ASIA_RESULTS_DIR / "coverage.csv")
    events = pd.read_csv(ASIA_RESULTS_DIR / "events.csv", low_memory=False)
    rule_summary = pd.read_csv(ASIA_RESULTS_DIR / "rule_summary.csv")
    return coverage, events, rule_summary


def _bias_validation() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, bool]:
    _, events, rule_summary = _load_asia_research()
    candidate_rule = XX00_ASIA_1M_LAUNCH_CORE.rule_id
    summary_rows: list[dict[str, object]] = []
    for cohort_id in ("long_union", "long_hot_range"):
        cohort_scope = rule_summary[(rule_summary["cohort_id"].astype(str) == cohort_id)].copy()
        candidate_row = cohort_scope[cohort_scope["rule_id"].astype(str) == candidate_rule].copy()
        if candidate_row.empty:
            continue
        candidate_row = candidate_row.iloc[0]
        current_events = events[
            (events["cohort_id"].astype(str) == cohort_id)
            & (events["rule_id"].astype(str) == candidate_rule)
            & (events["dataset"].astype(str) == "current")
        ].copy()
        old_events = events[
            (events["cohort_id"].astype(str) == cohort_id)
            & (events["rule_id"].astype(str) == candidate_rule)
            & (events["dataset"].astype(str) == "old")
        ].copy()
        monthly = _monthly_totals(current_events)
        concentration = _concentration_frame(current_events)
        total_current = float(pd.to_numeric(current_events.get("exit_return_pct"), errors="coerce").sum())
        top1_share = float(concentration["share_of_total"].iloc[0]) if not concentration.empty else 0.0
        top3_share = float(concentration["share_of_total"].head(3).sum()) if not concentration.empty else 0.0
        summary_rows.append(
            {
                "cohort_id": cohort_id,
                "rule_id": candidate_rule,
                "rank_all_families": int(
                    cohort_scope.sort_values("robust_score", ascending=False).reset_index(drop=True).index[
                        cohort_scope.sort_values("robust_score", ascending=False).reset_index(drop=True)["rule_id"].astype(str) == candidate_rule
                    ][0]
                    + 1
                ),
                "current_cases": int(candidate_row["current_cases"]),
                "current_trades": int(candidate_row["current_trades"]),
                "current_mean_return_pct": float(candidate_row["current_mean_return_pct"]),
                "current_equity_total_return_pct_5": float(candidate_row["current_equity_total_return_pct_5"]),
                "current_equity_max_drawdown_pct_5": float(candidate_row["current_equity_max_drawdown_pct_5"]),
                "old_trades": int(candidate_row["old_trades"]),
                "old_mean_return_pct": float(candidate_row["old_mean_return_pct"]),
                "active_months_current": int(len(monthly)),
                "positive_months_current": int((pd.to_numeric(monthly.get("month_return_pct"), errors="coerce") > 0.0).sum()),
                "top1_symbol_share_current": top1_share,
                "top3_symbol_share_current": top3_share,
                "current_total_return_sum_pct": total_current,
                "old_total_return_sum_pct": float(pd.to_numeric(old_events.get("exit_return_pct"), errors="coerce").sum()),
            }
        )
    validation_summary = pd.DataFrame(summary_rows)
    neighbor_plateau = rule_summary[
        rule_summary["cohort_id"].astype(str).isin({"long_union", "long_hot_range"})
        & (rule_summary["family"].astype(str) == "long_launch")
    ].copy()
    neighbor_plateau = neighbor_plateau.sort_values(["cohort_id", "robust_score"], ascending=[True, False]).reset_index(drop=True)
    walkforward = pd.concat(
        [
            _walkforward_choice(events, cohort_id="long_union", family=None),
            _walkforward_choice(events, cohort_id="long_union", family="long_launch"),
            _walkforward_choice(events, cohort_id="long_hot_range", family=None),
            _walkforward_choice(events, cohort_id="long_hot_range", family="long_launch"),
        ],
        ignore_index=True,
    )
    passes = bool(
        not validation_summary.empty
        and (validation_summary["rank_all_families"].astype(int) == 1).all()
        and (validation_summary["current_trades"].astype(int) >= 12).any()
        and (validation_summary["current_mean_return_pct"].astype(float) > 0.04).any()
        and (validation_summary["top3_symbol_share_current"].astype(float) <= 0.50).all()
        and (validation_summary["positive_months_current"].astype(int) == validation_summary["active_months_current"].astype(int)).all()
        and not walkforward.empty
        and walkforward["selected_is_candidate"].astype(bool).all()
    )
    return validation_summary, neighbor_plateau, walkforward, passes


def _build_session_universe() -> pd.DataFrame:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    frames: list[pd.DataFrame] = []
    for session in _session_specs():
        for dataset, cache_dir in (("current", CURRENT_CACHE_DIR), ("old", PREV_CACHE_DIR)):
            preparer = DataPreparer(cache_dir)
            symbols = preparer.list_symbols(Timeframe.M5)
            params = build_hourly_asia_pump_profile(
                timeframe=Timeframe.M5,
                profile_id="loose",
                asia_start_hour_utc=session.start_hour_utc,
                asia_end_hour_utc=session.end_hour_utc,
                trigger_minute=0,
                max_follow_minutes=120,
            )
            LOGGER.info(
                "xx00-edge-bias-scan: session=%s dataset=%s symbols=%s start",
                session.session_id,
                dataset,
                len(symbols),
            )
            scope = _build_timeframe_candidates(
                preparer=preparer,
                timeframe=Timeframe.M5,
                symbols=symbols,
                base_params=params,
                logger=LOGGER,
            )
            if scope.empty:
                continue
            scope["session_id"] = session.session_id
            scope["dataset"] = dataset
            scope["cache_scope"] = dataset
            frames.append(scope)
    universe = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if universe.empty:
        return universe
    universe["timestamp_utc"] = pd.to_datetime(pd.to_numeric(universe["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    universe["month_utc"] = universe["timestamp_utc"].dt.strftime("%Y-%m")
    universe["date_utc"] = universe["timestamp_utc"].dt.strftime("%Y-%m-%d")
    current_mask = universe["dataset"].astype(str) == "current"
    last_timestamp = universe.loc[current_mask, "timestamp_utc"].max()
    if pd.notna(last_timestamp):
        cutoff = last_timestamp - pd.Timedelta(days=365)
        universe = universe[(~current_mask) | (universe["timestamp_utc"] >= cutoff)].copy()
    return universe.sort_values(["session_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _window_return(closes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(closes) or (end - start) < 2:
        return None
    first = float(closes[start])
    last = float(closes[end - 1])
    if first <= 0.0:
        return None
    return float((last / first) - 1.0)


def _window_green_ratio(opens: list[float], closes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(opens) or end <= start:
        return None
    flags = [float(closes[idx]) > float(opens[idx]) for idx in range(start, end)]
    if not flags:
        return None
    return float(sum(flags) / len(flags))


def _derive_session_features(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return universe
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    grouped = universe.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        m1 = _load_m1(str(cache_scope), str(symbol), cache)
        if m1.empty:
            continue
        timestamps = pd.to_numeric(m1["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(m1["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(m1["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(m1["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(m1["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(m1["volume"], errors="coerce").astype(float).tolist()
        ema200 = pd.Series(closes, dtype="float64").ewm(span=200, adjust=False).mean().tolist()
        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-edge-bias-scan: feature-progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={_safe_label(symbol)} events={len(scoped)}"
            )
        for _, row in scoped.iterrows():
            state = _prepare_event_state(
                event_row=row,
                timestamp_to_idx=timestamp_to_idx,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
            )
            if state is None:
                continue
            pre_idx = state.start_idx - 1
            pre_close = float(closes[pre_idx])
            ema200_value = float(ema200[pre_idx]) if pre_idx < len(ema200) else 0.0
            rows.append(
                {
                    **row.to_dict(),
                    "event_id": f"{row['dataset']}|{row['session_id']}|{row['symbol']}|{int(row['timestamp_ms'])}",
                    "pre_close_vs_ema200_pct": ((pre_close / ema200_value) - 1.0) if ema200_value > 0.0 else None,
                    "pre_return_30m_pct": _window_return(closes, state.start_idx - 30, state.start_idx),
                    "pre_return_60m_pct": _window_return(closes, state.start_idx - 60, state.start_idx),
                    "pre_green_ratio_30m": _window_green_ratio(opens, closes, state.start_idx - 30, state.start_idx),
                    "m0_return_pct_online": float(state.m0_return_pct),
                    "m0_close_pos_online": float(state.m0_close_pos),
                    "m0_volume_ratio_online": float(state.m0_volume_ratio),
                }
            )
    return pd.DataFrame(rows).sort_values(["session_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _simulate_variant_events(scope: pd.DataFrame, variants: tuple[CandidateVariant, ...]) -> pd.DataFrame:
    if scope.empty:
        return pd.DataFrame()
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        m1 = _load_m1(str(cache_scope), str(symbol), cache)
        if m1.empty:
            continue
        timestamps = pd.to_numeric(m1["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(m1["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(m1["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(m1["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(m1["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(m1["volume"], errors="coerce").astype(float).tolist()
        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-edge-bias-scan: simulate-progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={_safe_label(symbol)} events={len(scoped)}"
            )
        for _, row in scoped.iterrows():
            state = _prepare_event_state(
                event_row=row,
                timestamp_to_idx=timestamp_to_idx,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
            )
            if state is None:
                continue
            base = {
                "event_id": str(row["event_id"]),
                "session_id": str(row["session_id"]),
                "dataset": str(row["dataset"]),
                "cache_scope": str(row["cache_scope"]),
                "symbol": str(row["symbol"]),
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": str(row["month_utc"]),
                "date_utc": str(row["date_utc"]),
            }
            for variant in variants:
                result = _simulate_long_launch_rule(
                    event_state=state,
                    timestamps=timestamps,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    rule=variant,
                )
                if result is None:
                    continue
                records.append(
                    {
                        **base,
                        **result,
                        "variant_id": variant.variant_id,
                        "variant_label": variant.label,
                    }
                )
    events = pd.DataFrame(records)
    if events.empty:
        return events
    for column in ("timestamp_ms", "signal_bar_timestamp_ms", "entry_timestamp_ms", "exit_timestamp_ms"):
        events[column.replace("_ms", "_utc")] = pd.to_datetime(
            pd.to_numeric(events[column], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        )
    return events.sort_values(["session_id", "variant_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _passes_proxy(frame: pd.DataFrame, proxy: ProxyFilter) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    if proxy.require_above_ema200:
        mask &= pd.to_numeric(frame.get("pre_close_vs_ema200_pct"), errors="coerce") > 0.0
    if proxy.min_pre_green_ratio_30m is not None:
        mask &= pd.to_numeric(frame.get("pre_green_ratio_30m"), errors="coerce") >= proxy.min_pre_green_ratio_30m
    if proxy.min_pre_range_60m is not None and proxy.min_pre_return_60m is not None:
        hot_range = pd.to_numeric(frame.get("pre_base_range_pct_60m"), errors="coerce") >= proxy.min_pre_range_60m
        hot_drift = pd.to_numeric(frame.get("pre_return_60m_pct"), errors="coerce") >= proxy.min_pre_return_60m
        mask &= hot_range | hot_drift
    elif proxy.min_pre_range_60m is not None:
        mask &= pd.to_numeric(frame.get("pre_base_range_pct_60m"), errors="coerce") >= proxy.min_pre_range_60m
    elif proxy.min_pre_return_60m is not None:
        mask &= pd.to_numeric(frame.get("pre_return_60m_pct"), errors="coerce") >= proxy.min_pre_return_60m
    return mask.fillna(False)


def _cross_session_summary(features: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if features.empty or events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for session_id in sorted(features["session_id"].astype(str).unique()):
        feature_session = features[features["session_id"].astype(str) == session_id].copy()
        event_session = events[events["session_id"].astype(str) == session_id].copy()
        for proxy in _proxy_filters():
            proxy_scope = feature_session[_passes_proxy(feature_session, proxy)].copy()
            proxy_event_ids = set(proxy_scope["event_id"].astype(str))
            proxy_events = event_session[event_session["event_id"].astype(str).isin(proxy_event_ids)].copy()
            for variant_id, variant_events in proxy_events.groupby("variant_id", sort=True):
                current_scope = proxy_scope[proxy_scope["dataset"].astype(str) == "current"].copy()
                old_scope = proxy_scope[proxy_scope["dataset"].astype(str) == "old"].copy()
                current_events = variant_events[variant_events["dataset"].astype(str) == "current"].copy()
                old_events = variant_events[variant_events["dataset"].astype(str) == "old"].copy()
                row = {
                    "session_id": session_id,
                    "proxy_id": proxy.proxy_id,
                    "proxy_label": proxy.label,
                    "variant_id": str(variant_id),
                    "variant_label": str(variant_events["variant_label"].iloc[0]),
                }
                row.update(_summary_row(current_scope, current_events, "current"))
                row.update(_summary_row(old_scope, old_events, "old"))
                row["robust_score"] = (
                    float(row["current_equity_total_return_pct_5"])
                    + (0.50 * float(row["old_equity_total_return_pct_5"]))
                    + float(row["current_mean_return_pct"])
                    + (0.50 * float(row["old_mean_return_pct"]))
                    + (6.0 * float(row["current_trade_rate"]))
                    + (3.0 * float(row["old_trade_rate"]))
                    - (0.75 * float(row["current_equity_max_drawdown_pct_5"]))
                    - (0.35 * float(row["old_equity_max_drawdown_pct_5"]))
                    - (0.10 * float(row["current_mean_entry_delay_minutes"] or 0.0))
                )
                rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        ["session_id", "robust_score", "current_equity_total_return_pct_5", "current_mean_return_pct"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)


def _select_cross_session(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    selected_rows: list[pd.Series] = []
    for session_id, scoped in summary.groupby("session_id", sort=True):
        ready = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= 6)
            & (pd.to_numeric(scoped["old_trades"], errors="coerce") >= 1)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["old_mean_return_pct"], errors="coerce") >= 0.0)
        ].copy()
        if not ready.empty:
            row = ready.iloc[0].copy()
            row["selection_type"] = "best_ready"
            selected_rows.append(row)
            continue
        positive = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= 6)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
        ].copy()
        if not positive.empty:
            row = positive.iloc[0].copy()
            row["selection_type"] = "current_only_positive"
            selected_rows.append(row)
            continue
        row = scoped.iloc[0].copy()
        row["selection_type"] = "no_positive_rule"
        selected_rows.append(row)
    return pd.DataFrame(selected_rows).sort_values(["session_id", "selection_type"]).reset_index(drop=True)


def _portfolio_summary(name: str, frame: pd.DataFrame) -> dict[str, object]:
    calendar_months = _calendar_months_from_frame(frame)
    summary = _summarize_events(frame, calendar_months=calendar_months) or {}
    equity, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    return {
        "portfolio_id": name,
        "trades": int(summary.get("trades_count", 0)),
        "trades_per_year": float(summary.get("trades_per_year", 0.0)),
        "mean_return_pct": float(summary.get("mean_return_pct", 0.0)),
        "win_rate": float(summary.get("win_rate", 0.0)),
        "annualized_unit_pnl_pct": float(summary.get("annualized_unit_pnl_pct", 0.0)),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0) or 0.0),
        "equity_total_return_pct_5": float(equity.get("equity_total_return_pct", 0.0)),
        "equity_annualized_return_pct_5": float(equity.get("equity_annualized_return_pct", 0.0)),
        "equity_max_drawdown_pct_5": float(equity.get("equity_max_drawdown_pct", 0.0)),
    }


def _build_portfolios(asia_events: pd.DataFrame, cross_events: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    frames: list[dict[str, object]] = []
    current_asia = asia_events[asia_events["dataset"].astype(str) == "current"].copy()
    frames.append(_portfolio_summary("asia_fixed_only", current_asia))
    selected_positive = selected[selected["selection_type"].astype(str) != "no_positive_rule"].copy()
    if selected_positive.empty:
        return pd.DataFrame(frames)
    for _, row in selected_positive.iterrows():
        session_events = cross_events[
            (cross_events["session_id"].astype(str) == str(row["session_id"]))
            & (cross_events["proxy_id"].astype(str) == str(row["proxy_id"]))
            & (cross_events["variant_id"].astype(str) == str(row["variant_id"]))
            & (cross_events["dataset"].astype(str) == "current")
        ].copy()
        combined = pd.concat([current_asia, session_events], ignore_index=True)
        frames.append(_portfolio_summary(f"asia_plus_{row['session_id']}", combined))
    all_selected_events = cross_events[
        cross_events["dataset"].astype(str) == "current"
    ].merge(
        selected_positive[["session_id", "proxy_id", "variant_id"]],
        on=["session_id", "proxy_id", "variant_id"],
        how="inner",
    )
    combined_all = pd.concat([current_asia, all_selected_events], ignore_index=True)
    frames.append(_portfolio_summary("asia_plus_all_selected", combined_all))
    return pd.DataFrame(frames)


def _build_report(
    validation_summary: pd.DataFrame,
    walkforward: pd.DataFrame,
    selected_sessions: pd.DataFrame,
    portfolio_summary: pd.DataFrame,
) -> str:
    walkforward_report = walkforward.copy()
    if not walkforward_report.empty and "selected_is_candidate" in walkforward_report.columns:
        walkforward_report["selected_is_candidate"] = walkforward_report["selected_is_candidate"].map(
            lambda value: "yes" if bool(value) else "no"
        )
    lines = [
        "# XX:00 Edge Bias And Cross-Session Scan",
        "",
        "Asia candidate under validation:",
        f"- `{XX00_ASIA_1M_LAUNCH_CORE.rule_id}`",
        "",
        "Bias validation summary:",
        _frame_to_markdown(
            validation_summary,
            columns=[
                "cohort_id",
                "rank_all_families",
                "current_cases",
                "current_trades",
                "current_mean_return_pct",
                "current_equity_total_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "old_trades",
                "old_mean_return_pct",
                "active_months_current",
                "positive_months_current",
                "top1_symbol_share_current",
                "top3_symbol_share_current",
            ],
        )
        if not validation_summary.empty
        else "_No validation data._",
        "",
        "Walk-forward selection check:",
        _frame_to_markdown(
            walkforward_report,
            columns=[
                "cohort_id",
                "family_scope",
                "test_month",
                "selected_family",
                "selected_rule_id",
                "selected_is_candidate",
                "selected_test_sum_return_pct",
                "candidate_test_sum_return_pct",
            ],
        )
        if not walkforward.empty
        else "_No walk-forward rows._",
        "",
        "Cross-session selected candidates:",
        _frame_to_markdown(
            selected_sessions,
            columns=[
                "session_id",
                "selection_type",
                "proxy_id",
                "variant_id",
                "current_cases",
                "current_trades",
                "current_trade_rate",
                "current_mean_entry_delay_minutes",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_total_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "old_trades",
                "old_mean_return_pct",
            ],
        )
        if not selected_sessions.empty
        else "_No session candidates._",
        "",
        "Portfolio combinations:",
        _frame_to_markdown(
            portfolio_summary,
            columns=[
                "portfolio_id",
                "trades",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_total_return_pct_5",
                "equity_max_drawdown_pct_5",
            ],
        )
        if not portfolio_summary.empty
        else "_No portfolio rows._",
        "",
        "Reading guide:",
        "- `best_ready` means the session candidate stayed non-negative on old data and positive on current.",
        "- `no_positive_rule` means the scanned XX:00 minute-1 family did not produce a positive current candidate.",
        "- Portfolio rows use current data only because Europe/America search is exploratory.",
    ]
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("xx00-edge-bias-scan: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    paths = {
        "bias_status": OUTPUT_DIR / "bias_status.csv",
        "validation_summary": OUTPUT_DIR / "validation_summary.csv",
        "neighbor_plateau": OUTPUT_DIR / "neighbor_plateau.csv",
        "walkforward": OUTPUT_DIR / "walkforward.csv",
        "session_universe": OUTPUT_DIR / "session_universe.csv",
        "feature_scope": OUTPUT_DIR / "feature_scope.csv",
        "variant_events": OUTPUT_DIR / "variant_events.csv",
        "cross_events": OUTPUT_DIR / "cross_events.csv",
        "cross_summary": OUTPUT_DIR / "cross_summary.csv",
        "selected_sessions": OUTPUT_DIR / "selected_sessions.csv",
        "portfolio_summary": OUTPUT_DIR / "portfolio_summary.csv",
        "report": OUTPUT_DIR / "report.md",
    }

    validation_summary, neighbor_plateau, walkforward, bias_passed = _bias_validation()
    if paths["session_universe"].exists():
        universe = pd.read_csv(paths["session_universe"], low_memory=False)
    else:
        universe = _build_session_universe()
        universe.to_csv(paths["session_universe"], index=False)
    if paths["feature_scope"].exists():
        feature_scope = pd.read_csv(paths["feature_scope"], low_memory=False)
    else:
        feature_scope = _derive_session_features(universe)
        feature_scope.to_csv(paths["feature_scope"], index=False)
    if paths["variant_events"].exists():
        variant_events = pd.read_csv(paths["variant_events"], low_memory=False)
    else:
        variant_events = _simulate_variant_events(feature_scope, _candidate_variants())
        variant_events.to_csv(paths["variant_events"], index=False)

    cross_event_frames: list[pd.DataFrame] = []
    for proxy in _proxy_filters():
        proxy_scope = feature_scope[_passes_proxy(feature_scope, proxy)].copy()
        proxy_event_ids = set(proxy_scope["event_id"].astype(str))
        proxy_events = variant_events[variant_events["event_id"].astype(str).isin(proxy_event_ids)].copy()
        if proxy_events.empty:
            continue
        proxy_events["proxy_id"] = proxy.proxy_id
        proxy_events["proxy_label"] = proxy.label
        cross_event_frames.append(proxy_events)
    cross_events = pd.concat(cross_event_frames, ignore_index=True) if cross_event_frames else pd.DataFrame()

    cross_summary = _cross_session_summary(feature_scope, cross_events)
    selected_sessions = _select_cross_session(cross_summary)

    asia_events = pd.read_csv(ASIA_RESULTS_DIR / "events.csv", low_memory=False)
    asia_fixed_events = asia_events[
        (asia_events["cohort_id"].astype(str) == XX00_ASIA_1M_LAUNCH_CORE.cohort_id)
        & (asia_events["rule_id"].astype(str) == XX00_ASIA_1M_LAUNCH_CORE.rule_id)
    ].copy()
    portfolio_summary = _build_portfolios(asia_fixed_events, cross_events, selected_sessions)

    report = _build_report(validation_summary, walkforward, selected_sessions, portfolio_summary)
    bias_status = pd.DataFrame(
        [
            {
                "candidate_id": XX00_ASIA_1M_LAUNCH_CORE.candidate_id,
                "rule_id": XX00_ASIA_1M_LAUNCH_CORE.rule_id,
                "bias_passed": bool(bias_passed),
            }
        ]
    )

    bias_status.to_csv(paths["bias_status"], index=False)
    validation_summary.to_csv(paths["validation_summary"], index=False)
    neighbor_plateau.to_csv(paths["neighbor_plateau"], index=False)
    walkforward.to_csv(paths["walkforward"], index=False)
    cross_events.to_csv(paths["cross_events"], index=False)
    cross_summary.to_csv(paths["cross_summary"], index=False)
    selected_sessions.to_csv(paths["selected_sessions"], index=False)
    portfolio_summary.to_csv(paths["portfolio_summary"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    print("xx00-edge-bias-scan: done")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
