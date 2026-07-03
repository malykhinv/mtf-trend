"""Pullback-breakout continuation entry (separate pump-long hypothesis).

Lesson from the near-term probe: the lever is the ENTRY LOCATION, not the exit.
So enter with better asymmetry: after a new high H, wait for a controlled
pullback (depth 10-60% of the pump, base intact), then enter on the breakout
back through the pullback midpoint, with the stop at the pullback low (a close,
meaningful, non-noise structural level) and a structural trail so winners run.

This builder records a rich, causal metric set per setup so the robust subset
can be searched on the saved parquet WITHOUT re-simulating (exclude October,
stratify, seek a rule positive across most non-October months). Metrics:
which new-high (state_ordinal), pullback depth, swing-high position in the
pullback, consolidation length vs pump age, level touches, low timing
(zigzag vs range), high upper wick, pullback volume dry-up, breakout volume
conviction, pullback velocity, ATR contraction. Trail roughness swept at
1.5 / 2.5 / 4.0 ATR. Reuses the Core simulator; only detection is new.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import STATUS_FILLED, causal_atr, simulate_long_path
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

WINDOW = 180
MIN_DEPTH = 0.10
MAX_DEPTH = 0.60
R_SWITCH = 2.0
TRAIL_ROUGHNESS = (1.5, 2.5, 4.0)
SPEC = PumpLongExecutionSpec()
CARRY_FEATURES = [
    "state_ordinal", "price_vs_ema_60", "price_vs_ema_240", "verticality",
    "pump_elapsed_min", "recent_return_3m", "turnover_top_candle_share",
    # prior pump-fade recurrence (leak-safe: resolved before t0)
    "n_prior_48h", "frac_prior_faded_48h", "min_since_last_prior", "last_prior_faded",
    "has_resolved_prior_48h",
]
DORMANT_BARS = 240  # 4h pre-ignition "sleep" window for the pump-vs-dormant ratio


def _cv(x):
    x = np.asarray(x, float)
    m = x.mean()
    return float(x.std() / m) if len(x) and m > 0 else np.nan


def _flow_metrics(vol, tc, d0, pump_elapsed):
    """Volume/trade steadiness during the pump and pump-vs-dormant prominence."""
    span = int(pump_elapsed) if np.isfinite(pump_elapsed) and pump_elapsed >= 2 else 0
    p0 = max(0, d0 - span)
    pv = vol[p0:d0 + 1]; pt = tc[p0:d0 + 1]
    dv = vol[max(0, p0 - DORMANT_BARS):p0]; dt = tc[max(0, p0 - DORMANT_BARS):p0]
    dv_m = dv.mean() if len(dv) else np.nan
    dt_m = dt.mean() if len(dt) else np.nan
    return {
        "vol_cv_pump": _cv(pv), "trade_cv_pump": _cv(pt),
        "vol_pump_vs_dormant": float(pv.mean() / dv_m) if np.isfinite(dv_m) and dv_m > 0 else np.nan,
        "trade_pump_vs_dormant": float(pt.mean() / dt_m) if np.isfinite(dt_m) and dt_m > 0 else np.nan,
        # how consistently elevated the flow stays (share of pump bars above the dormant mean)
        "vol_sustain_frac": float((pv > dv_m).mean()) if np.isfinite(dv_m) and len(pv) else np.nan,
    }


def _detect(o, h, low, c, vol, tc, d0, H, B, window, pump_elapsed):
    """Causal pullback + midpoint breakout. Returns setup metrics or None."""
    if H <= B:
        return None
    flow = _flow_metrics(vol, tc, d0, pump_elapsed)
    impulse = H - B
    L = np.inf; l_idx = d0; end = min(d0 + window, len(c) - 1)
    dir_changes = 0; prev = None
    for j in range(d0 + 1, end + 1):
        if float(c[j]) <= B:
            return None
        lo = float(low[j])
        if lo < L:
            L = lo; l_idx = j
        step = np.sign(float(c[j]) - float(c[j - 1]))
        if prev is not None and step != 0 and step != prev:
            dir_changes += 1
        if step != 0:
            prev = step
        depth = (H - L) / impulse
        if depth > MAX_DEPTH:
            return None
        if depth < MIN_DEPTH:
            continue
        mid = 0.5 * (H + L)
        if float(c[j]) > mid:  # breakout of pullback structure
            seg_h = h[d0 + 1:j + 1]; seg_v = vol[d0 + 1:j + 1]
            sh = float(seg_h[l_idx - d0:].max()) if j > l_idx else float(seg_h.max())
            touches = int((seg_h >= mid * 0.999).sum())
            return {
                **flow,
                "j": j, "L": L, "H": H, "depth": depth,
                "sh_position": (sh - L) / (H - L + 1e-12),
                "consol_bars": j - d0,
                "sh_touches": touches,
                "low_time_frac": (l_idx - d0) / (j - d0 + 1e-9),
                "choppiness": dir_changes / (j - d0 + 1e-9),
                "high_wick_frac": (float(h[d0]) - max(float(o[d0]), float(c[d0]))) / (float(h[d0]) - float(low[d0]) + 1e-12),
                "pullback_vol_ratio": float(np.mean(seg_v)) / (float(vol[d0]) + 1e-9),
                "breakout_vol_ratio": float(vol[j]) / (float(np.mean(seg_v)) + 1e-9),
                "pullback_velocity": depth / (l_idx - d0 + 1e-9),
            }
    return None


def build_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                   end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    stamp = pd.to_datetime(lat["snapshot_time_ms"], unit="ms", utc=True)
    lat["mo"] = stamp.dt.strftime("%Y-%m"); lat["week"] = stamp.dt.strftime("%G-W%V")
    lat["hour"] = stamp.dt.hour
    print(f"decisions: {len(lat)} across {lat['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    groups = list(lat.groupby("symbol"))
    for gi, (symbol, g) in enumerate(groups, 1):
        if gi % 60 == 0 or gi == len(groups):
            print(f"  {gi}/{len(groups)} symbols, setups {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        vol = frame["quote_volume"].to_numpy(float)
        tc = frame["trade_count"].to_numpy(float)
        atr = causal_atr(high=h, low=low, close=c, window=30)
        for row in g.itertuples():
            snap = int(row.snapshot_time_ms); d0 = int(np.searchsorted(ts, snap))
            if d0 >= len(ts) or int(ts[d0]) != snap:
                continue
            s = _detect(o, h, low, c, vol, tc, d0, float(row.anchor_high), float(row.base_level),
                        WINDOW, float(row.pump_elapsed_min))
            if s is None:
                continue
            ei = s["j"] + 1
            if ei >= len(ts):
                continue
            entry = float(o[ei]); stop = s["L"]
            if not (stop < entry):
                continue
            rec = {"symbol": symbol, "mo": row.mo, "week": row.week, "hour": int(row.hour),
                   "stop_frac": (entry - stop) / entry, "atr_at_entry": float(atr[d0]) / entry}
            rec.update({k: s[k] for k in
                        ("depth", "sh_position", "consol_bars", "sh_touches", "low_time_frac",
                         "choppiness", "high_wick_frac", "pullback_vol_ratio",
                         "breakout_vol_ratio", "pullback_velocity",
                         "vol_cv_pump", "trade_cv_pump", "vol_pump_vs_dormant",
                         "trade_pump_vs_dormant", "vol_sustain_frac")})
            rec["consol_vs_pump"] = s["consol_bars"] / (float(row.pump_elapsed_min) + 1e-9)
            for rough in TRAIL_ROUGHNESS:
                res = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                         initial_stop_price=stop, spec=SPEC, swing_reversal_atr=rough,
                                         stage_switch_r=R_SWITCH, atr=atr)
                rec[f"net_{rough}"] = res.net_return if res.status == STATUS_FILLED else np.nan
            for f in CARRY_FEATURES:
                rec[f] = float(getattr(row, f)) if hasattr(row, f) else np.nan
            rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    df = build_outcomes()
    out = Path(".output/results/pump_long_v1/pullback_breakout_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"\nsaved {len(df)} setups x {len(df.columns)} cols -> {out}", flush=True)
    # quick headline at 2.5 roughness, ex-October
    exo = df.loc[df["mo"] != "2025-10"]
    for lbl, s in (("ALL", df), ("ex-Oct", exo)):
        n = s["net_2.5"].to_numpy(); n = n[np.isfinite(n)]
        print(f"  {lbl:<7} net_2.5: n={len(n)} EV={n.mean()*100:.2f}% win={(n>0).mean()*100:.1f}%", flush=True)


if __name__ == "__main__":
    main()
