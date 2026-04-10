from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _load_feature_db,
    _load_m1,
    _net_long_return,
    _prepare_scope,
    _pressure_score,
    _simulate_equity_risk_metrics,
    _simulate_exit_1m,
    _summarize_events,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "tradeable_asia_long_regimes"


@dataclass(frozen=True, slots=True)
class RegimeModel:
    regime_id: str
    model_id: str
    max_wait_minutes: int
    min_break_body_ratio: float
    min_break_volume_ratio: float
    min_close_pos: float
    min_buyer_seller_ratio: float
    max_entry_extension_frac: float
    rr_target: float
    fast_fail_minutes: int
    fast_fail_r: float
    max_hold_minutes: int
    max_seller_pressure: float = 999.0
    max_pullback_frac: float = 999.0
    min_hold_bars: int = 0
    max_hold_cluster_pullback_frac: float = 999.0
    min_retest_depth_frac: float = 0.0
    max_retest_depth_frac: float = 999.0
    stabilization_bars: int = 0
    stop_style: str = "signal_low"


def _category_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = _prepare_scope(frame).copy()
    scoped = scoped[scoped["category_id"].astype(str).isin({"warm_continuation", "overheated_retest"})].copy()
    scoped["regime_group"] = scoped["context_archetype"].astype(str).map(
        {
            "context_warm": "warm",
            "context_overheated": "overheated",
        }
    )
    return scoped.dropna(subset=["regime_group"]).reset_index(drop=True)


def _close_pos(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _body_ratio(open_price: float, close_price: float, avg_trigger_body_1m: float) -> float:
    if avg_trigger_body_1m <= 0.0:
        return 0.0
    return float(max(0.0, close_price - open_price) / avg_trigger_body_1m)


def _volume_ratio(volume: float, avg_trigger_vol_1m: float) -> float:
    if avg_trigger_vol_1m <= 0.0:
        return 0.0
    return float(volume / avg_trigger_vol_1m)


def _build_models() -> tuple[RegimeModel, ...]:
    models: list[RegimeModel] = []

    for rr_target in (1.5, 2.0):
        for max_seller_pressure in (0.18, 0.28):
            for stop_style in ("signal_low", "prebreak_low"):
                models.append(
                    RegimeModel(
                        regime_id="warm_direct_push",
                        model_id=(
                            f"wdp_sp{int(max_seller_pressure*100):02d}_"
                            f"{stop_style}_rr{int(rr_target*10):02d}"
                        ),
                        max_wait_minutes=4,
                        min_break_body_ratio=0.65,
                        min_break_volume_ratio=0.60,
                        min_close_pos=0.62,
                        min_buyer_seller_ratio=1.10,
                        max_entry_extension_frac=0.18,
                        rr_target=rr_target,
                        fast_fail_minutes=8,
                        fast_fail_r=0.25,
                        max_hold_minutes=90,
                        max_seller_pressure=max_seller_pressure,
                        max_pullback_frac=0.15,
                        stop_style=stop_style,
                    )
                )

    for rr_target in (1.5, 2.0):
        for min_hold_bars in (2, 3):
            for max_cluster_pullback in (0.10, 0.15):
                models.append(
                    RegimeModel(
                        regime_id="warm_grind_hold",
                        model_id=(
                            f"wgh_h{min_hold_bars}_pb{int(max_cluster_pullback*100):02d}_"
                            f"rr{int(rr_target*10):02d}"
                        ),
                        max_wait_minutes=8,
                        min_break_body_ratio=0.45,
                        min_break_volume_ratio=0.45,
                        min_close_pos=0.55,
                        min_buyer_seller_ratio=1.00,
                        max_entry_extension_frac=0.20,
                        rr_target=rr_target,
                        fast_fail_minutes=10,
                        fast_fail_r=0.20,
                        max_hold_minutes=100,
                        max_seller_pressure=0.35,
                        min_hold_bars=min_hold_bars,
                        max_hold_cluster_pullback_frac=max_cluster_pullback,
                        stop_style="cluster_low",
                    )
                )

    for rr_target in (1.5, 2.0):
        for stabilization_bars in (1, 2):
            for ratio in (1.00, 1.35):
                models.append(
                    RegimeModel(
                        regime_id="overheated_deep_retest",
                        model_id=(
                            f"odr_s{stabilization_bars}_bs{int(ratio*100):03d}_"
                            f"rr{int(rr_target*10):02d}"
                        ),
                        max_wait_minutes=15,
                        min_break_body_ratio=0.55,
                        min_break_volume_ratio=0.50,
                        min_close_pos=0.58,
                        min_buyer_seller_ratio=ratio,
                        max_entry_extension_frac=0.18,
                        rr_target=rr_target,
                        fast_fail_minutes=14,
                        fast_fail_r=0.20,
                        max_hold_minutes=120,
                        min_retest_depth_frac=0.15,
                        max_retest_depth_frac=0.42,
                        stabilization_bars=stabilization_bars,
                        stop_style="cluster_low",
                    )
                )

    for rr_target in (1.5, 2.0):
        for body_ratio in (0.75, 0.95):
            for volume_ratio in (0.70, 0.90):
                models.append(
                    RegimeModel(
                        regime_id="overheated_fast_break",
                        model_id=(
                            f"ofb_b{int(body_ratio*100):02d}_v{int(volume_ratio*100):02d}_"
                            f"rr{int(rr_target*10):02d}"
                        ),
                        max_wait_minutes=3,
                        min_break_body_ratio=body_ratio,
                        min_break_volume_ratio=volume_ratio,
                        min_close_pos=0.65,
                        min_buyer_seller_ratio=1.20,
                        max_entry_extension_frac=0.14,
                        rr_target=rr_target,
                        fast_fail_minutes=8,
                        fast_fail_r=0.25,
                        max_hold_minutes=90,
                        max_seller_pressure=0.18,
                        max_pullback_frac=0.14,
                        stop_style="signal_low",
                    )
                )

    return tuple(models)


def _simulate_warm_direct_push(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: RegimeModel,
) -> dict[str, object] | None:
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
    prebreak_low = float("inf")
    seller_pressure = 0.0
    end_idx = min(len(closes), start_idx + model.max_wait_minutes)

    for idx in range(start_idx, end_idx):
        prebreak_low = min(prebreak_low, lows[idx])
        if closes[idx] <= trigger_high:
            if closes[idx] < opens[idx]:
                seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
            continue

        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        pullback_frac = max(0.0, (trigger_high - prebreak_low) / trigger_range)
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)

        if _body_ratio(opens[idx], closes[idx], avg_trigger_body_1m) < model.min_break_body_ratio:
            continue
        if _volume_ratio(volumes[idx], avg_trigger_vol_1m) < model.min_break_volume_ratio:
            continue
        if _close_pos(highs[idx], lows[idx], closes[idx]) < model.min_close_pos:
            continue
        if pullback_frac > model.max_pullback_frac:
            continue
        if seller_pressure > model.max_seller_pressure:
            continue
        if buyer_score < seller_pressure * model.min_buyer_seller_ratio:
            continue
        if extension_frac > model.max_entry_extension_frac:
            continue

        entry_price = float(closes[idx])
        stop_price = float(lows[idx] if model.stop_style == "signal_low" else prebreak_low)
        if stop_price >= entry_price:
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, float(exit_price)),
            "entry_detail": "warm_direct_push",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "pullback_frac": float(pullback_frac),
        }
    return None


def _simulate_warm_grind_hold(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: RegimeModel,
) -> dict[str, object] | None:
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
    hold_lows: list[float] = []
    hold_highs: list[float] = []
    seller_pressure = 0.0
    end_idx = min(len(closes), start_idx + model.max_wait_minutes)

    for idx in range(start_idx, end_idx):
        if closes[idx] < opens[idx]:
            seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        hold_lows.append(float(lows[idx]))
        hold_highs.append(float(highs[idx]))
        cluster_low = min(hold_lows)
        cluster_pullback = max(0.0, (trigger_high - cluster_low) / trigger_range)
        if cluster_pullback > model.max_hold_cluster_pullback_frac:
            return None
        if idx - start_idx + 1 < model.min_hold_bars:
            continue

        prior_high = max(hold_highs[:-1]) if len(hold_highs) > 1 else trigger_high
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        if closes[idx] <= max(trigger_high, prior_high):
            continue
        if _body_ratio(opens[idx], closes[idx], avg_trigger_body_1m) < model.min_break_body_ratio:
            continue
        if _volume_ratio(volumes[idx], avg_trigger_vol_1m) < model.min_break_volume_ratio:
            continue
        if _close_pos(highs[idx], lows[idx], closes[idx]) < model.min_close_pos:
            continue
        if seller_pressure > model.max_seller_pressure:
            continue
        if buyer_score < seller_pressure * model.min_buyer_seller_ratio:
            continue

        entry_price = float(closes[idx])
        stop_price = float(cluster_low)
        if stop_price >= entry_price:
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, float(exit_price)),
            "entry_detail": "warm_grind_hold",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "cluster_pullback_frac": float(cluster_pullback),
        }
    return None


def _simulate_overheated_deep_retest(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: RegimeModel,
) -> dict[str, object] | None:
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
    end_idx = min(len(closes), start_idx + model.max_wait_minutes)

    for idx in range(start_idx + model.stabilization_bars + 2, end_idx):
        lookback_start = max(start_idx, idx - (model.stabilization_bars + 4))
        cluster_lows = lows[lookback_start:idx]
        cluster_highs = highs[max(start_idx, idx - model.stabilization_bars - 1):idx]
        if not cluster_lows or not cluster_highs:
            continue
        cluster_low = min(cluster_lows)
        retest_depth = max(0.0, (trigger_high - cluster_low) / trigger_range)
        if retest_depth < model.min_retest_depth_frac or retest_depth > model.max_retest_depth_frac:
            continue
        lowest_rel = cluster_lows.index(cluster_low)
        lowest_idx = lookback_start + lowest_rel
        if idx - lowest_idx < model.stabilization_bars + 1:
            continue

        if closes[idx] <= opens[idx]:
            continue
        if _body_ratio(opens[idx], closes[idx], avg_trigger_body_1m) < model.min_break_body_ratio:
            continue
        if _volume_ratio(volumes[idx], avg_trigger_vol_1m) < model.min_break_volume_ratio:
            continue
        if _close_pos(highs[idx], lows[idx], closes[idx]) < model.min_close_pos:
            continue

        structure_high = max(cluster_highs)
        if closes[idx] <= structure_high:
            continue
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if extension_frac > model.max_entry_extension_frac:
            continue

        seller_pressure = 0.0
        for j in range(lowest_idx, idx):
            if closes[j] < opens[j]:
                seller_pressure += _pressure_score(opens[j], closes[j], volumes[j], trigger_range, avg_trigger_vol_1m)
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        if buyer_score < seller_pressure * model.min_buyer_seller_ratio:
            continue

        entry_price = float(closes[idx])
        stop_price = float(cluster_low)
        if stop_price >= entry_price:
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, float(exit_price)),
            "entry_detail": "overheated_deep_retest",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "retest_depth_frac": float(retest_depth),
        }
    return None


def _simulate_overheated_fast_break(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: RegimeModel,
) -> dict[str, object] | None:
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
    prebreak_low = float("inf")
    seller_pressure = 0.0
    end_idx = min(len(closes), start_idx + model.max_wait_minutes)

    for idx in range(start_idx, end_idx):
        prebreak_low = min(prebreak_low, lows[idx])
        if closes[idx] <= trigger_high:
            if closes[idx] < opens[idx]:
                seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
            continue
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        pullback_frac = max(0.0, (trigger_high - prebreak_low) / trigger_range)
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if _body_ratio(opens[idx], closes[idx], avg_trigger_body_1m) < model.min_break_body_ratio:
            continue
        if _volume_ratio(volumes[idx], avg_trigger_vol_1m) < model.min_break_volume_ratio:
            continue
        if _close_pos(highs[idx], lows[idx], closes[idx]) < model.min_close_pos:
            continue
        if pullback_frac > model.max_pullback_frac:
            continue
        if seller_pressure > model.max_seller_pressure:
            continue
        if buyer_score < seller_pressure * model.min_buyer_seller_ratio:
            continue
        if extension_frac > model.max_entry_extension_frac:
            continue

        entry_price = float(closes[idx])
        stop_price = float(lows[idx])
        if stop_price >= entry_price:
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, float(exit_price)),
            "entry_detail": "overheated_fast_break",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "pullback_frac": float(pullback_frac),
        }
    return None


def _simulate_regime(
    *,
    regime_id: str,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: RegimeModel,
) -> dict[str, object] | None:
    if regime_id == "warm_direct_push":
        return _simulate_warm_direct_push(
            event_row=event_row,
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            model=model,
        )
    if regime_id == "warm_grind_hold":
        return _simulate_warm_grind_hold(
            event_row=event_row,
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            model=model,
        )
    if regime_id == "overheated_deep_retest":
        return _simulate_overheated_deep_retest(
            event_row=event_row,
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            model=model,
        )
    if regime_id == "overheated_fast_break":
        return _simulate_overheated_fast_break(
            event_row=event_row,
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            model=model,
        )
    raise ValueError(f"Unknown regime_id: {regime_id}")


def _regime_models_for_row(event_row: pd.Series, models: tuple[RegimeModel, ...]) -> tuple[RegimeModel, ...]:
    group = str(event_row["regime_group"])
    if group == "warm":
        return tuple(model for model in models if model.regime_id in {"warm_direct_push", "warm_grind_hold"})
    if group == "overheated":
        return tuple(model for model in models if model.regime_id in {"overheated_deep_retest", "overheated_fast_break"})
    return tuple()


def _build_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    models = _build_models()
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    grouped = frame.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups

    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles_1m = _load_m1(str(cache_scope), str(symbol), cache)
        if candles_1m.empty:
            continue
        timestamps = pd.to_numeric(candles_1m["timestamp"], errors="coerce").astype("int64").tolist()
        opens = pd.to_numeric(candles_1m["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles_1m["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles_1m["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles_1m["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(candles_1m["volume"], errors="coerce").astype(float).tolist()

        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"asia-long-regimes: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(scoped)}"
            )

        for _, row in scoped.iterrows():
            candidate_models = _regime_models_for_row(row, models)
            for regime_id in sorted({model.regime_id for model in candidate_models}):
                coverage_rows.append(
                    {
                        "regime_id": regime_id,
                        "dataset": row["dataset"],
                        "symbol": row["symbol"],
                        "timestamp_ms": int(row["timestamp_ms"]),
                    }
                )
            base = {
                "regime_group": row["regime_group"],
                "dataset": row["dataset"],
                "symbol": row["symbol"],
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": row["month_utc"],
                "trigger_return_pct": float(row["trigger_return_pct"]),
                "range_atr": float(row["range_atr"]),
                "body_atr": float(row["body_atr"]),
                "volume_mult": float(row["volume_mult"]),
                "pre_base_range_pct_60m": float(row["pre_base_range_pct_60m"]),
                "pre_base_drift_pct_60m": float(row["pre_base_drift_pct_60m"]),
                "buyer_wave_match_score_30m": float(row["buyer_wave_match_score_30m"]),
                "pre_accumulation_type": row["pre_accumulation_type"],
                "context_archetype": row["context_archetype"],
            }
            for model in candidate_models:
                simulated = _simulate_regime(
                    regime_id=model.regime_id,
                    event_row=row,
                    timestamps=timestamps,
                    opens=opens,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    volumes=volumes,
                    model=model,
                )
                if simulated is None:
                    continue
                records.append({**base, **simulated, "regime_id": model.regime_id, "model_id": model.model_id})

    coverage = pd.DataFrame(coverage_rows).drop_duplicates()
    events = (
        pd.DataFrame(records)
        .sort_values(["regime_id", "model_id", "dataset", "timestamp_ms", "symbol"])
        .reset_index(drop=True)
        if records
        else pd.DataFrame()
    )
    return events, coverage


def _summarize_models(events: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    coverage_counts = (
        coverage.groupby(["regime_id", "dataset"]).size().unstack(fill_value=0).rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    rows: list[dict[str, object]] = []

    for (regime_id, model_id), scoped in events.groupby(["regime_id", "model_id"], sort=True):
        combined = scoped.copy()
        current = combined[combined["dataset"] == "current"].copy()
        old = combined[combined["dataset"] == "old"].copy()
        calendar_months = _calendar_months_from_frames(combined)
        summary = _summarize_events(combined, calendar_months=calendar_months)
        equity, _ = _simulate_equity_risk_metrics(combined, calendar_months=calendar_months, risk_fraction=0.05)
        rows.append(
            {
                "regime_id": regime_id,
                "model_id": model_id,
                "current_trades": int(len(current)),
                "old_trades": int(len(old)),
                "coverage_current": int(coverage_counts.loc[regime_id, "coverage_current"]) if regime_id in coverage_counts.index else 0,
                "coverage_old": int(coverage_counts.loc[regime_id, "coverage_old"]) if regime_id in coverage_counts.index else 0,
                "combined_trades_per_year": float(summary["trades_per_year"]),
                "combined_mean_return_pct": float(summary["mean_return_pct"]),
                "combined_median_return_pct": float(summary["median_return_pct"]),
                "combined_win_rate": float(summary["win_rate"]),
                "combined_annualized_unit_pnl_pct": float(summary["annualized_unit_pnl_pct"]),
                "equity_annualized_return_pct_5": float(equity["equity_annualized_return_pct"]),
                "equity_max_drawdown_pct_5": float(equity["equity_max_drawdown_pct"]),
                "equity_positive_months_count": int(equity["equity_positive_months_count"]),
                "equity_stable_positive_months_count": int(equity["equity_stable_positive_months_count"]),
            }
        )

    summary_frame = pd.DataFrame(rows)
    summary_frame["score"] = (
        summary_frame["equity_annualized_return_pct_5"]
        - 0.45 * summary_frame["equity_max_drawdown_pct_5"]
        + 3.0 * summary_frame["combined_mean_return_pct"]
        + 0.30 * summary_frame["equity_stable_positive_months_count"]
    )
    return summary_frame.sort_values(
        ["regime_id", "score", "equity_annualized_return_pct_5", "combined_mean_return_pct", "combined_trades_per_year"],
        ascending=[True, False, False, False, False],
    ).reset_index(drop=True)


def _select_models(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    chosen: list[pd.Series] = []
    for regime_id, scoped in summary.groupby("regime_id", sort=True):
        ready = scoped[
            (scoped["current_trades"] >= 12)
            & (scoped["old_trades"] >= 6)
            & (scoped["combined_mean_return_pct"] > 0.0)
            & (scoped["equity_annualized_return_pct_5"] > 0.0)
        ].copy()
        if not ready.empty:
            row = ready.iloc[0].copy()
            row["selection_status"] = "candidate_ready"
            chosen.append(row)
            continue
        thin = scoped[
            (scoped["combined_mean_return_pct"] > 0.0)
            & (scoped["equity_annualized_return_pct_5"] > 0.0)
        ].copy()
        if not thin.empty:
            row = thin.iloc[0].copy()
            row["selection_status"] = "thin_positive"
            chosen.append(row)
            continue
        row = scoped.iloc[0].copy()
        row["selection_status"] = "not_ready"
        chosen.append(row)
    return pd.DataFrame(chosen).reset_index(drop=True)


def _build_selected_outputs(events: pd.DataFrame, selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty or selected.empty:
        return pd.DataFrame(), pd.DataFrame()
    chosen = events.merge(selected[["regime_id", "model_id", "selection_status"]], on=["regime_id", "model_id"], how="inner").copy()
    chosen = chosen[chosen["selection_status"].astype(str) != "not_ready"].copy()
    monthly = _build_monthly_returns_frame(chosen)
    return chosen, monthly


def _build_report(summary: pd.DataFrame, selected: pd.DataFrame) -> str:
    lines = [
        "# Онлайн-модели азиатского long по режимам",
        "",
        "Long разбит не по часу, а по природе развития после аномальной `5m` свечи.",
        "",
        "Режимы:",
        "- `warm_direct_push`: тёплый контекст, быстрый пробой high аномалии.",
        "- `warm_grind_hold`: тёплый контекст, удержание у high и потом micro-break вверх.",
        "- `overheated_deep_retest`: перегретый контекст, откат, стабилизация и возврат инициативы.",
        "- `overheated_fast_break`: перегретый контекст, но continuation идёт сразу и очень мощно.",
        "",
    ]
    if summary.empty:
        lines.append("Подходящих моделей не найдено.")
        return "\n".join(lines)
    if not selected.empty:
        lines.extend(
            [
                "## Выбранные модели",
                _frame_to_markdown(
                    selected,
                    columns=[
                        "regime_id",
                        "selection_status",
                        "model_id",
                        "current_trades",
                        "old_trades",
                        "combined_trades_per_year",
                        "combined_mean_return_pct",
                        "combined_win_rate",
                        "equity_annualized_return_pct_5",
                        "equity_max_drawdown_pct_5",
                        "equity_stable_positive_months_count",
                    ],
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Топ моделей",
            _frame_to_markdown(
                summary.head(24),
                columns=[
                    "regime_id",
                    "model_id",
                    "current_trades",
                    "old_trades",
                    "combined_trades_per_year",
                    "combined_mean_return_pct",
                    "combined_win_rate",
                    "equity_annualized_return_pct_5",
                    "equity_max_drawdown_pct_5",
                    "equity_stable_positive_months_count",
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("asia-long-regimes: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped = _category_scope(_load_feature_db())
    print(f"asia-long-regimes: scoped_events={len(scoped)}")
    events, coverage = _build_events(scoped)
    print(f"asia-long-regimes: built_events={len(events)}")
    summary = _summarize_models(events, coverage)
    selected = _select_models(summary)
    selected_events, selected_monthly = _build_selected_outputs(events, selected)

    paths = {
        "coverage": OUTPUT_DIR / "coverage.csv",
        "events": OUTPUT_DIR / "events.csv",
        "summary": OUTPUT_DIR / "summary.csv",
        "selected": OUTPUT_DIR / "selected_models.csv",
        "selected_events": OUTPUT_DIR / "selected_events.csv",
        "selected_monthly": OUTPUT_DIR / "selected_monthly.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    coverage.to_csv(paths["coverage"], index=False)
    events.to_csv(paths["events"], index=False)
    summary.to_csv(paths["summary"], index=False)
    selected.to_csv(paths["selected"], index=False)
    selected_events.to_csv(paths["selected_events"], index=False)
    selected_monthly.to_csv(paths["selected_monthly"], index=False)
    paths["report"].write_text(_build_report(summary, selected), encoding="utf-8")
    print("asia-long-regimes: done")
    return paths


if __name__ == "__main__":
    run()
