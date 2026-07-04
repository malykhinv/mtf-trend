"""Bee-bite spring detection, outcomes, and spring-success metrics.

Per qualified pump (from the runner lattice), find its high, a tight
consolidation range beneath it, then the first SWEEP below the range low that
RECLAIMS within a few bars. Enter the reclaim; stop either under the sweep WICK
low or under the sweep candle BODY low (tighter); target the range high
("перехай") or a fixed R-multiple, or trail. We record the character of the
grab AND the range geometry (swing counts, ascending/descending slopes) so the
online question - will the sweep reclaim into a new high? - is predictable from
candles alone. Reuses the Core simulator and the shared metrics.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import (
    EXIT_REASON_TAKE_PROFIT,
    STATUS_FILLED,
    causal_atr,
    simulate_long_path,
)
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.research.metrics import dissect_wins_losses, summarize
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

# Frozen detection constants (registered before inspecting outcomes).
CONSOL_BARS = 30
SWEEP_WINDOW = 90
RECLAIM_LIMIT = 6
MICRO_OFFSET = 0.001
MAX_RANGE_FRAC = 0.20
HOLD_RATIO = 0.5
NEW_HIGH_EPS = 0.002
DORMANT_BARS = 240
ACT_WIN = 15  # bars each side of the sweep for before/after activity
STRUCTURE_WIN = 10

# --- validity gates (reject non-tradeable setups a trader would skip) ---
RANGE_MIN_BOUNDARY_TESTS = 2   # a real range tests BOTH boundaries (not peak+drift)
RANGE_MIN_MID_CROSSINGS = 2    # price oscillates through the middle (two-sided trade)
PRE_SLEEP_BARS = 120           # the ~2h right before ignition = the "sleep" to inspect
SLEEP_MAX_RANGE = 0.12         # pre-ignition high-low span / price: must be calm (no pre-pump move)
DUMP_MAX_DROP = 0.08           # base must not be a dump-low: recent pre-pump high-to-base drop cap
MIN_PUMP_EFFICIENCY = 0.45     # ignition path efficiency: reject choppy, ill-defined pumps
MIN_HELD_RATIO = 0.60          # consolidation must hug the HIGH (range low in the upper 40% of the pump)

TOUCH = PumpLongExecutionSpec(stop_trigger_close_beyond=False)  # wick/body: intrabar touch
CLOSE = PumpLongExecutionSpec()                                 # range: exit on CLOSE below box
EARLY_K = 5           # bars: cut fast-failers that close back below the box
EARLY_COST = 0.003    # honest ~30bps round trip for the research early-exit sim
FLOW_CHECK = 3        # bar to judge post-entry taker-buy aggression (causal)


def _sim_flow(h, low, c, vol, tk, atr, ei, entry, sweep_low, tbr_base, target, trail):
    """Causal flow-managed long: ride to target/trail with the sweep-low stop,
    but at bar ei+FLOW_CHECK judge the taker-buy share SO FAR; if buyers did not
    show up (share < the consolidation baseline) cut at that close. Uses only
    bars up to the decision bar - no look-ahead."""
    n = len(c); stop = sweep_low; pl = np.inf; pa = np.nan
    chk = min(ei + FLOW_CHECK, n - 1)
    for j in range(ei, n):
        if float(low[j]) <= stop:
            return (stop / entry - 1.0) - EARLY_COST
        if not trail and target is not None and float(h[j]) >= target:
            return (target / entry - 1.0) - EARLY_COST
        if j == chk:
            v = float(vol[ei:chk + 1].sum())
            share = float(tk[ei:chk + 1].sum()) / v if v > 0 else 0.0
            if np.isfinite(tbr_base) and share < tbr_base:
                return (float(c[j]) / entry - 1.0) - EARLY_COST
        if trail:
            if float(low[j]) < pl:
                pl = float(low[j]); pa = float(atr[j])
            if np.isfinite(pa) and pa > 0 and (float(h[j]) - pl) >= 2.5 * pa:
                if stop < pl < float(c[j]):
                    stop = pl
                pl = float(low[j]); pa = float(atr[j])
    return (float(c[-1]) / entry - 1.0) - EARLY_COST


def _sim_early(h, low, c, atr, ei, entry, sweep_low, range_low, target, trail):
    """Early-cut long: if a close falls back below the range low within the
    first EARLY_K bars, exit there (cheap, near breakeven). Otherwise ride to
    `target` (or a structural swing-low trail) with the sweep-low stop."""
    n = len(c); stop = sweep_low; pl = np.inf; pa = np.nan
    for j in range(ei, n):
        if j < ei + EARLY_K and float(c[j]) < range_low:
            return (float(c[j]) / entry - 1.0) - EARLY_COST
        if float(low[j]) <= stop:
            return (stop / entry - 1.0) - EARLY_COST
        if not trail and target is not None and float(h[j]) >= target:
            return (target / entry - 1.0) - EARLY_COST
        if trail:
            if float(low[j]) < pl:
                pl = float(low[j]); pa = float(atr[j])
            if np.isfinite(pa) and pa > 0 and (float(h[j]) - pl) >= 2.5 * pa:
                if stop < pl < float(c[j]):
                    stop = pl
                pl = float(low[j]); pa = float(atr[j])
    return (float(c[-1]) / entry - 1.0) - EARLY_COST
# stop variants x target variants -> net_<stop>_<target>
STOPS = ("wick", "body", "range")
TARGETS = ("perehai", "2R", "3R", "trail")
CAUSAL_METRICS = [
    "pump_size", "range_tightness", "consol_len", "n_swing_lows", "n_swing_highs",
    "highs_slope", "lows_slope", "reclaim_bars", "sweep_wick_frac", "sweep_depth_frac",
    "sweep_vol_ratio", "reclaim_vol_ratio", "reclaim_close_loc", "vol_pump_vs_dormant",
    "held_ratio", "n_prior_sweeps", "support_test_count", "bars_since_support_test",
    "last_pivot_low_delta", "range_compression", "sell_share_change",
    "avg_trade_size_sweep_ratio", "avg_trade_size_reclaim_ratio",
    "reclaim_range_ratio", "reclaim_body", "taker_sweep", "taker_reclaim",
    "upper_test_count", "lower_test_count", "upper_pivot_dispersion",
    "lower_pivot_dispersion", "swing_interval_mean", "swing_interval_cv",
    "swing_alternation_ratio", "slope_convergence", "range_mid_crossings",
    "range_close_dispersion", "pump_duration", "pump_path_efficiency",
    "pump_green_fraction", "pump_max_drawdown_frac", "pump_back_half_return_share",
    "pump_back_half_volume_ratio", "pump_largest_bar_share", "pump_wick_fraction",
    "pullback_wick_frac", "pullback_close_frac", "range_low_location",
]
# Backwards-compatible public name used by ad-hoc notebooks. Every field is
# observable by the reclaim close; post-entry diagnostics are intentionally out.
METRICS = CAUSAL_METRICS


def _slope(idx, vals, norm):
    if len(vals) < 2 or norm <= 0:
        return 0.0
    a = np.polyfit(np.asarray(idx, float), np.asarray(vals, float), 1)[0]
    return float(a / norm)


def _swings(low, high, s, e):
    """1-bar pivots between s and e; returns (n_lows, n_highs, low_slope, high_slope)."""
    li, lv, hi, hv = [], [], [], []
    for p in range(s + 1, e):
        if low[p] <= low[p - 1] and low[p] <= low[p + 1]:
            li.append(p); lv.append(float(low[p]))
        if high[p] >= high[p - 1] and high[p] >= high[p + 1]:
            hi.append(p); hv.append(float(high[p]))
    norm = float(high[s:e + 1].max()) if e > s else 1.0
    return len(lv), len(hv), _slope(li, lv, norm), _slope(hi, hv, norm)


def _support_tests(low, start, end, range_low, range_height):
    """Count distinct pre-sweep visits to the lower 10% of the range."""
    threshold = range_low + 0.10 * range_height
    tests = 0
    last_test = None
    inside = False
    for i in range(start, end):
        touches = float(low[i]) <= threshold
        if touches and not inside:
            tests += 1
            last_test = i
        inside = touches
    bars_since = end - last_test if last_test is not None else np.nan
    return tests, bars_since


def _boundary_tests(values, start, end, threshold, *, upper):
    tests = 0
    inside = False
    for i in range(start, end):
        touches = float(values[i]) >= threshold if upper else float(values[i]) <= threshold
        if touches and not inside:
            tests += 1
        inside = touches
    return tests


def _swing_geometry(low, high, close, start, end, range_low, range_high):
    """Pre-sweep geometry on ``start..end-1``; bar ``end`` confirms the last pivot."""
    height = range_high - range_low
    lows = [(p, float(low[p])) for p in range(start + 1, end) if low[p] <= low[p - 1] and low[p] <= low[p + 1]]
    highs = [(p, float(high[p])) for p in range(start + 1, end) if high[p] >= high[p - 1] and high[p] >= high[p + 1]]
    pivots = sorted([(p, "L") for p, _ in lows] + [(p, "H") for p, _ in highs])
    intervals = np.diff([p for p, _ in pivots]).astype(float) if len(pivots) >= 2 else np.array([])
    alternations = sum(a[1] != b[1] for a, b in zip(pivots, pivots[1:]))
    mid = range_low + 0.5 * height
    mid_crossings = sum(
        (float(close[i - 1]) - mid) * (float(close[i]) - mid) < 0
        for i in range(start + 1, end)
    )
    close_segment = np.asarray(close[start:end], dtype=float)
    return {
        "upper_test_count": _boundary_tests(high, start, end, range_high - 0.10 * height, upper=True),
        "lower_test_count": _boundary_tests(low, start, end, range_low + 0.10 * height, upper=False),
        "upper_pivot_dispersion": float(np.std([v for _, v in highs]) / height) if highs else np.nan,
        "lower_pivot_dispersion": float(np.std([v for _, v in lows]) / height) if lows else np.nan,
        "swing_interval_mean": float(intervals.mean()) if len(intervals) else np.nan,
        "swing_interval_cv": float(intervals.std() / intervals.mean()) if len(intervals) and intervals.mean() > 0 else np.nan,
        "swing_alternation_ratio": alternations / (len(pivots) - 1) if len(pivots) >= 2 else np.nan,
        "range_mid_crossings": mid_crossings,
        "range_close_dispersion": float(close_segment.std() / height) if len(close_segment) else np.nan,
    }


def _pump_shape(o, h, low, c, vol, start, end, base, high_level):
    """Causal shape of the ignition-to-confirmed-high path."""
    if end <= start or high_level <= base:
        return {}
    span = high_level - base
    closes = np.asarray(c[start:end + 1], dtype=float)
    opens = np.asarray(o[start:end + 1], dtype=float)
    highs = np.asarray(h[start:end + 1], dtype=float)
    lows = np.asarray(low[start:end + 1], dtype=float)
    volumes = np.asarray(vol[start:end + 1], dtype=float)
    changes = np.diff(np.concatenate(([base], closes)))
    half = max(1, len(changes) // 2)
    ranges = highs - lows
    wick = (highs - np.maximum(opens, closes)) + (np.minimum(opens, closes) - lows)
    running_high = np.maximum.accumulate(highs)
    return {
        "pump_duration": end - start + 1,
        "pump_path_efficiency": span / (float(np.abs(changes).sum()) + 1e-12),
        "pump_green_fraction": float(np.mean(closes > opens)),
        "pump_max_drawdown_frac": float(np.max(running_high - lows) / span),
        "pump_back_half_return_share": float(changes[half:].sum() / span),
        "pump_back_half_volume_ratio": float(volumes[half:].mean() / (volumes[:half].mean() + 1e-12)),
        "pump_largest_bar_share": float(np.max(np.maximum(changes, 0.0)) / span),
        "pump_wick_fraction": float(wick.sum() / (ranges.sum() + 1e-12)),
    }


def _mean_true_range(high, low, close, start, end):
    if end <= start:
        return np.nan
    values = []
    for i in range(start, end):
        previous_close = float(close[i - 1]) if i > 0 else float(close[i])
        values.append(max(
            float(high[i]) - float(low[i]),
            abs(float(high[i]) - previous_close),
            abs(float(low[i]) - previous_close),
        ))
    return float(np.mean(values)) if values else np.nan


def _detect(o, h, low, c, vol, tc, tk, hi_idx, H, B, pump_elapsed):
    hold = B + HOLD_RATIO * (H - B)
    n = len(c)
    consol_end = hi_idx + CONSOL_BARS
    if consol_end + 5 >= n:
        return None
    seg_lo = low[hi_idx + 1:consol_end + 1]; seg_hi = h[hi_idx + 1:consol_end + 1]
    if len(seg_lo) < CONSOL_BARS // 2 or c[hi_idx + 1:consol_end + 1].min() < hold:
        return None
    range_low = float(seg_lo.min())
    range_high = max(float(seg_hi.max()), float(H))
    range_height = range_high - range_low
    if range_height <= 0 or range_height / range_high > MAX_RANGE_FRAC:
        return None
    # consolidation must sit near the HIGH (reference: a box hugging the top)
    if (range_low - B) / (H - B + 1e-12) < MIN_HELD_RATIO:
        return None
    consol_vol = float(vol[hi_idx + 1:consol_end + 1].mean()) + 1e-9
    consol_tc = float(tc[hi_idx + 1:consol_end + 1].mean()) + 1e-9

    span = int(pump_elapsed) if np.isfinite(pump_elapsed) and pump_elapsed >= 2 else CONSOL_BARS
    p0 = max(0, hi_idx - span)
    dorm = vol[max(0, p0 - DORMANT_BARS):p0]; dorm_m = float(dorm.mean()) if len(dorm) else np.nan
    vpd = (float(vol[p0:hi_idx + 1].mean()) / dorm_m) if np.isfinite(dorm_m) and dorm_m > 0 else np.nan

    # --- validity gates: skip setups a trader would never take ---
    # (1) real traded range, not a peak + descending drift (EPIC/RARE): BOTH
    #     boundaries tested and price oscillates through the middle.
    upper_tests = _boundary_tests(h, hi_idx + 1, consol_end + 1, range_high - 0.10 * range_height, upper=True)
    lower_tests = _boundary_tests(low, hi_idx + 1, consol_end + 1, range_low + 0.10 * range_height, upper=False)
    if upper_tests < RANGE_MIN_BOUNDARY_TESTS or lower_tests < RANGE_MIN_BOUNDARY_TESTS:
        return None
    mid = range_low + 0.5 * range_height
    mid_crossings = sum(
        (float(c[i - 1]) - mid) * (float(c[i]) - mid) < 0
        for i in range(hi_idx + 2, consol_end + 1)
    )
    if mid_crossings < RANGE_MIN_MID_CROSSINGS:
        return None
    # (2) real sleep before the pump, not a pre-pump dump/recovery (METIS, TUT):
    #     the ~2h right before ignition must be calm AND the base must not be a
    #     dump-low (price must not have crashed into it from a much higher level).
    swin_lo = max(0, p0 - PRE_SLEEP_BARS)
    if p0 - swin_lo >= 30:
        pre_hi = float(h[swin_lo:p0].max()); pre_lo = float(low[swin_lo:p0].min())
        pre_mean = float(c[swin_lo:p0].mean()) + 1e-12
        if (pre_hi - pre_lo) / pre_mean > SLEEP_MAX_RANGE:
            return None
        if (pre_hi - B) / (pre_hi + 1e-12) > DUMP_MAX_DROP:
            return None
    # (3) clean ignition, not a choppy ill-defined pump (MAVIA).
    pump_shape = _pump_shape(o, h, low, c, vol, p0, hi_idx, B, H)
    if pump_shape.get("pump_path_efficiency", 1.0) < MIN_PUMP_EFFICIENCY:
        return None

    n_prior_sweeps = 0
    for k in range(consol_end + 1, min(consol_end + SWEEP_WINDOW, n - 1) + 1):
        if float(c[k]) < hold:
            return None
        if float(low[k]) < range_low:
            sweep_low = float(low[k]); depth = range_low - sweep_low
            if depth >= range_height:
                return None
            for m in range(k, min(k + RECLAIM_LIMIT, n - 1) + 1):
                if float(c[m]) > range_low * (1 + MICRO_OFFSET):
                    ei = m + 1
                    if ei >= n:
                        return None
                    entry = float(o[ei])
                    body_low = min(float(o[k]), float(c[k]))
                    if not (sweep_low < entry < range_high) or not (body_low < entry):
                        return None
                    # post-entry path (for the winner/loser behavior study &
                    # early-exit rule): excursions and how fast it fails.
                    e = entry
                    def _exc(kk):
                        sh = h[ei:min(ei + kk, n)]; slw = low[ei:min(ei + kk, n)]
                        if len(sh) == 0:
                            return 0.0, 0.0
                        return (float(sh.max()) - e) / e, (e - float(slw.min())) / e
                    mfe5, mae5 = _exc(5); mfe10, mae10 = _exc(10)
                    ret5 = float(c[min(ei + 4, n - 1)]) / e - 1.0
                    fail_bar = np.nan
                    for q in range(ei, min(ei + 30, n)):
                        if float(c[q]) < range_low:
                            fail_bar = q - ei; break
                    # long-aggression (taker BUY share; liquidations unavailable
                    # on free data) at the grab, the reclaim, and right after entry.
                    def _tbr(a, b):
                        v = float(vol[a:b].sum())
                        return float(tk[a:b].sum() / v) if v > 0 else np.nan
                    tbr_base = _tbr(hi_idx + 1, consol_end + 1)
                    tbr_after = _tbr(ei, min(ei + 5, n))
                    sweep_close_pos = (float(c[k]) - float(low[k])) / (float(h[k]) - float(low[k]) + 1e-12)
                    reclaim_body = (float(c[m]) - float(o[m])) / (float(h[m]) - float(low[m]) + 1e-12)
                    nl, nh, sl_slope, sh_slope = _swings(low, h, hi_idx + 1, k)
                    geometry = _swing_geometry(
                        low, h, c, hi_idx + 1, k, range_low, range_high
                    )
                    pump_shape = _pump_shape(o, h, low, c, vol, p0, hi_idx, B, H)
                    pivot_lows = [
                        float(low[p]) for p in range(hi_idx + 2, k)
                        if low[p] <= low[p - 1] and low[p] <= low[p + 1]
                    ]
                    last_pivot_low_delta = (
                        (pivot_lows[-1] - pivot_lows[-2]) / range_height
                        if len(pivot_lows) >= 2 else np.nan
                    )
                    support_test_count, bars_since_support_test = _support_tests(
                        low, hi_idx + 1, k, range_low, range_height
                    )
                    early_end = min(hi_idx + 1 + STRUCTURE_WIN, k)
                    late_start = max(hi_idx + 1, k - STRUCTURE_WIN)
                    early_tr = _mean_true_range(h, low, c, hi_idx + 1, early_end)
                    late_tr = _mean_true_range(h, low, c, late_start, k)
                    range_compression = late_tr / early_tr if np.isfinite(early_tr) and early_tr > 0 else np.nan
                    prior_start = max(hi_idx + 1, late_start - STRUCTURE_WIN)
                    def _sell_share(a, b):
                        total = float(vol[a:b].sum())
                        return float((vol[a:b] - tk[a:b]).sum() / total) if total > 0 else np.nan
                    prior_sell = _sell_share(prior_start, late_start)
                    late_sell = _sell_share(late_start, k)
                    sell_share_change = late_sell - prior_sell if np.isfinite(prior_sell) else np.nan
                    avg_trade_base = float(vol[hi_idx + 1:consol_end + 1].sum()) / float(
                        tc[hi_idx + 1:consol_end + 1].sum() + 1e-9
                    )
                    avg_trade_sweep = float(vol[k]) / float(tc[k] + 1e-9)
                    avg_trade_reclaim = float(vol[m]) / float(tc[m] + 1e-9)
                    consol_range = float(np.mean(h[hi_idx + 1:consol_end + 1] - low[hi_idx + 1:consol_end + 1]))
                    a_before = float(vol[max(hi_idx, k - ACT_WIN):k].mean()) + 1e-9
                    a_after = float(vol[k:min(k + ACT_WIN, n)].mean())
                    t_before = float(tc[max(hi_idx, k - ACT_WIN):k].mean()) + 1e-9
                    t_after = float(tc[k:min(k + ACT_WIN, n)].mean())
                    cw = float(h[k]) - float(low[k]) + 1e-12
                    result = {
                        "entry_index": ei, "wick_stop": sweep_low, "body_stop": body_low,
                        "range_stop": range_low, "range_high": range_high,
                        "pump_size": (H - B) / B, "range_tightness": range_height / range_high,
                        "consol_len": k - hi_idx, "n_swing_lows": nl, "n_swing_highs": nh,
                        "highs_slope": sh_slope, "lows_slope": sl_slope, "reclaim_bars": m - k,
                        "slope_convergence": sl_slope - sh_slope,
                        "sweep_wick_frac": (min(float(o[k]), float(c[k])) - float(low[k])) / cw,
                        "sweep_depth_frac": depth / range_height,
                        "sweep_vol_ratio": float(vol[k]) / consol_vol,
                        "reclaim_vol_ratio": float(vol[m]) / consol_vol,
                        "reclaim_close_loc": (float(c[m]) - range_low) / range_height,
                        "vol_after_vs_before": a_after / a_before,
                        "trade_after_vs_before": t_after / t_before,
                        "trade_flow_after": t_after / consol_tc,
                        "vol_pump_vs_dormant": vpd, "held_ratio": (range_low - B) / (H - B + 1e-12),
                        "n_prior_sweeps": n_prior_sweeps,
                        "support_test_count": support_test_count,
                        "bars_since_support_test": bars_since_support_test,
                        "last_pivot_low_delta": last_pivot_low_delta,
                        "range_compression": range_compression,
                        "sell_share_change": sell_share_change,
                        "avg_trade_size_sweep_ratio": avg_trade_sweep / avg_trade_base,
                        "avg_trade_size_reclaim_ratio": avg_trade_reclaim / avg_trade_base,
                        "reclaim_range_ratio": (float(h[m]) - float(low[m])) / (consol_range + 1e-12),
                        "pullback_wick_frac": (H - range_low) / (H - B + 1e-12),
                        "pullback_close_frac": (H - float(c[hi_idx + 1:consol_end + 1].min())) / (H - B + 1e-12),
                        "range_low_location": float(np.argmin(seg_lo)) / max(len(seg_lo) - 1, 1),
                        "mfe5": mfe5, "mae5": mae5, "mfe10": mfe10, "mae10": mae10,
                        "ret5": ret5, "fail_bar": fail_bar,
                        "taker_sweep": float(tk[k] / (vol[k] + 1e-9)),
                        "taker_reclaim": float(tk[m] / (vol[m] + 1e-9)),
                        "taker_after": tbr_after, "taker_delta": tbr_after - tbr_base,
                        "sweep_close_pos": sweep_close_pos, "reclaim_body": reclaim_body,
                        "tbr_base": tbr_base,
                    }
                    result.update(geometry)
                    result.update(pump_shape)
                    return result
            n_prior_sweeps += 1
    return None


def build_spring_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                          end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lattice_columns = [
        "group", "symbol", "snapshot_time_ms", "anchor_high", "base_level",
        "pump_elapsed_min", "runner_label_available",
    ]
    lat = pd.read_parquet(lattice_path, columns=lattice_columns)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    idx = lat.groupby("group")["anchor_high"].idxmax()
    ev = lat.loc[idx, ["group", "symbol", "snapshot_time_ms", "anchor_high", "base_level",
                       "pump_elapsed_min"]].reset_index(drop=True)
    stamp = pd.to_datetime(ev["snapshot_time_ms"], unit="ms", utc=True)
    ev["mo"] = stamp.dt.strftime("%Y-%m"); ev["week"] = stamp.dt.strftime("%G-W%V")
    print(f"pump events: {len(ev)} across {ev['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    nsym = ev["symbol"].nunique()
    for gi, (symbol, g) in enumerate(ev.groupby("symbol"), 1):
        if gi % 60 == 0 or gi == nsym:
            print(f"  {gi}/{nsym} symbols, springs {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        vol = frame["quote_volume"].to_numpy(float); tc = frame["trade_count"].to_numpy(float)
        tk = (frame["taker_buy_quote_volume"].to_numpy(float)
              if "taker_buy_quote_volume" in frame.columns else np.zeros(len(c)))
        atr = causal_atr(high=h, low=low, close=c, window=30)
        for row in g.itertuples():
            hi_idx = int(np.searchsorted(ts, int(row.snapshot_time_ms)))
            if hi_idx >= len(ts) or int(ts[hi_idx]) != int(row.snapshot_time_ms):
                continue
            s = _detect(o, h, low, c, vol, tc, tk, hi_idx, float(row.anchor_high),
                        float(row.base_level), float(row.pump_elapsed_min))
            if s is None:
                continue
            ei = s["entry_index"]; entry = float(o[ei])
            perehai = s["range_high"] * (1 + NEW_HIGH_EPS)
            rec = {
                "symbol": symbol,
                "mo": row.mo,
                "week": row.week,
                "pump_time_ms": int(ts[hi_idx]),
                "sweep_time_ms": int(ts[s["entry_index"] - 1 - int(s["reclaim_bars"])]),
                "reclaim_time_ms": int(ts[s["entry_index"] - 1]),
                "entry_time_ms": int(ts[s["entry_index"]]),
                "entry_hour_utc": int(pd.Timestamp(int(ts[s["entry_index"]]), unit="ms", tz="UTC").hour),
                "entry_date_utc": pd.Timestamp(
                    int(ts[s["entry_index"]]), unit="ms", tz="UTC"
                ).strftime("%Y-%m-%d"),
            }
            rec.update({k: s[k] for k in METRICS})
            rec.update({k: s[k] for k in ("mfe5", "mae5", "mfe10", "mae10", "ret5", "fail_bar",
                                          "taker_sweep", "taker_reclaim", "taker_after",
                                          "taker_delta", "sweep_close_pos", "reclaim_body")})
            for stop_name, stop, spec in (("wick", s["wick_stop"], TOUCH),
                                          ("body", s["body_stop"], TOUCH),
                                          ("range", s["range_stop"], CLOSE)):
                if not (stop < entry):
                    for tname in TARGETS:
                        rec[f"net_{stop_name}_{tname}"] = np.nan
                    continue
                r_dist = entry - stop
                tps = {"perehai": perehai, "2R": entry + 2 * r_dist, "3R": entry + 3 * r_dist}
                for tname, tp in tps.items():
                    res = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                             initial_stop_price=stop, spec=spec, take_profit_price=tp)
                    rec[f"net_{stop_name}_{tname}"] = res.net_return if res.status == STATUS_FILLED else np.nan
                    rec[f"holding_min_{stop_name}_{tname}"] = (
                        res.holding_minutes if res.status == STATUS_FILLED else np.nan
                    )
                    rec[f"exit_reason_{stop_name}_{tname}"] = res.exit_reason
                    rec[f"exit_time_ms_{stop_name}_{tname}"] = (
                        int(ts[res.exit_index]) if res.status == STATUS_FILLED else np.nan
                    )
                res_tr = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                            initial_stop_price=stop, spec=spec, swing_reversal_atr=2.5,
                                            stage_switch_r=2.0, atr=atr)
                rec[f"net_{stop_name}_trail"] = res_tr.net_return if res_tr.status == STATUS_FILLED else np.nan
                rec[f"holding_min_{stop_name}_trail"] = (
                    res_tr.holding_minutes if res_tr.status == STATUS_FILLED else np.nan
                )
                rec[f"exit_reason_{stop_name}_trail"] = res_tr.exit_reason
                rec[f"exit_time_ms_{stop_name}_trail"] = (
                    int(ts[res_tr.exit_index]) if res_tr.status == STATUS_FILLED else np.nan
                )
            # early-cut variants (target new-high, or trailing) - reads the
            # loser signature (fast close back below the box) to exit cheap
            rec["net_early_perehai"] = _sim_early(h, low, c, atr, ei, entry, s["wick_stop"],
                                                  s["range_stop"], perehai, trail=False)
            rec["net_early_trail"] = _sim_early(h, low, c, atr, ei, entry, s["wick_stop"],
                                                s["range_stop"], None, trail=True)
            # causal flow-managed: cut at ei+FLOW_CHECK if buyers didn't show
            rec["net_flow_perehai"] = _sim_flow(h, low, c, vol, tk, atr, ei, entry, s["wick_stop"],
                                                s["tbr_base"], perehai, trail=False)
            rec["net_flow_trail"] = _sim_flow(h, low, c, vol, tk, atr, ei, entry, s["wick_stop"],
                                              s["tbr_base"], None, trail=True)
            rec["new_high"] = rec.get("exit_reason_wick_perehai") == EXIT_REASON_TAKE_PROFIT
            rows.append(rec)
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    print("\n=== EV by stop x target (all springs, honest costs) ===", flush=True)
    for st in STOPS:
        for tg in TARGETS:
            col = f"net_{st}_{tg}"
            net = df[col].dropna().to_numpy()
            s = summarize(net, months=df.loc[df[col].notna(), "mo"].to_numpy(),
                          weeks=df.loc[df[col].notna(), "week"].to_numpy())
            print(s.line(f"{st}/{tg}"), flush=True)
    print(f"\nbase rate P(new high) = {df['new_high'].mean()*100:.1f}%  n={len(df)}", flush=True)
    dissect_wins_losses(df, "net_body_2R", CAUSAL_METRICS)


def main() -> None:
    df = build_spring_outcomes()
    out = Path(".output/results/bee_bite_v1/spring_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    report(df)
    print(f"\nsaved {len(df)} springs x {len(df.columns)} cols -> {out}", flush=True)


if __name__ == "__main__":
    main()
