"""Bee-bite spring detection, outcomes, and spring-success metrics.

Per qualified pump (from the runner lattice), find its high, a tight
consolidation range beneath it, then the first SWEEP below the range low that
RECLAIMS within a few bars. Enter the reclaim, stop under the sweep low, target
the range high (the "new high" = перехай). We record the character of the grab
so the online question - will the sweep reclaim into a new high? - can be
predicted from candles alone. Reuses the Core simulator and the shared metrics.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import STATUS_FILLED, causal_atr, simulate_long_path
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.research.metrics import dissect_wins_losses, summarize
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

# Frozen detection constants (registered before inspecting outcomes).
CONSOL_BARS = 30          # bars after the high used to define the range
SWEEP_WINDOW = 90         # bars after the range to look for the sweep
RECLAIM_LIMIT = 6         # bars to reclaim back above the range low
MICRO_OFFSET = 0.001      # reclaim must close this far back above the range low
MAX_RANGE_FRAC = 0.20     # range height / range high: a real consolidation
HOLD_RATIO = 0.5          # price must hold above base + 0.5*(high-base)
NEW_HIGH_EPS = 0.002      # "перехай" = range high + 0.2%
DORMANT_BARS = 240

SPEC = PumpLongExecutionSpec()
METRICS = [
    "sweep_wick_frac", "sweep_depth_frac", "reclaim_bars", "sweep_vol_ratio",
    "reclaim_vol_ratio", "range_tightness", "reclaim_close_loc", "n_prior_sweeps",
    "vol_pump_vs_dormant", "held_ratio", "rr_to_target",
]


def _detect(o, h, low, c, vol, hi_idx, H, B, pump_elapsed):
    """Causal range -> sweep -> reclaim detection. Returns setup dict or None."""
    hold = B + HOLD_RATIO * (H - B)
    n = len(c)
    consol_end = hi_idx + CONSOL_BARS
    if consol_end + 5 >= n:
        return None
    # range from the consolidation window (must hold above 0.5 pump height)
    seg_lo = low[hi_idx + 1:consol_end + 1]
    seg_hi = h[hi_idx + 1:consol_end + 1]
    if len(seg_lo) < CONSOL_BARS // 2 or c[hi_idx + 1:consol_end + 1].min() < hold:
        return None
    range_low = float(seg_lo.min())
    range_high = max(float(seg_hi.max()), float(H))
    range_height = range_high - range_low
    if range_height <= 0 or range_height / range_high > MAX_RANGE_FRAC:
        return None
    consol_vol = float(vol[hi_idx + 1:consol_end + 1].mean()) + 1e-9

    # dormant baseline flow (pump vs sleep) - our confirmed tilt
    span = int(pump_elapsed) if np.isfinite(pump_elapsed) and pump_elapsed >= 2 else CONSOL_BARS
    p0 = max(0, hi_idx - span)
    dorm = vol[max(0, p0 - DORMANT_BARS):p0]
    dorm_m = float(dorm.mean()) if len(dorm) else np.nan
    vol_pump_vs_dormant = (float(vol[p0:hi_idx + 1].mean()) / dorm_m) if np.isfinite(dorm_m) and dorm_m > 0 else np.nan

    n_prior_sweeps = 0
    for k in range(consol_end + 1, min(consol_end + SWEEP_WINDOW, n - 1) + 1):
        if float(c[k]) < hold:  # gave back the pump -> setup dead
            return None
        if float(low[k]) < range_low:  # SWEEP below the range low
            sweep_low = float(low[k]); depth = range_low - sweep_low
            if depth >= range_height:  # a real breakdown, not a grab
                return None
            # reclaim within the limit
            for m in range(k, min(k + RECLAIM_LIMIT, n - 1) + 1):
                if float(c[m]) > range_low * (1 + MICRO_OFFSET):
                    ei = m + 1
                    if ei >= n:
                        return None
                    entry = float(o[ei])
                    if not (sweep_low < entry < range_high):
                        return None
                    cw = float(h[k]) - float(low[k]) + 1e-12
                    return {
                        "entry_index": ei, "sweep_low": sweep_low, "range_high": range_high,
                        "sweep_wick_frac": (min(float(o[k]), float(c[k])) - float(low[k])) / cw,
                        "sweep_depth_frac": depth / range_height,
                        "reclaim_bars": m - k,
                        "sweep_vol_ratio": float(vol[k]) / consol_vol,
                        "reclaim_vol_ratio": float(vol[m]) / consol_vol,
                        "range_tightness": range_height / range_high,
                        "reclaim_close_loc": (float(c[m]) - range_low) / range_height,
                        "n_prior_sweeps": n_prior_sweeps,
                        "vol_pump_vs_dormant": vol_pump_vs_dormant,
                        "held_ratio": (range_low - B) / (H - B + 1e-12),
                        "rr_to_target": (range_high - entry) / (entry - sweep_low + 1e-12),
                    }
            n_prior_sweeps += 1  # swept but did not reclaim in time -> failed grab
    return None


def build_spring_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                          end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    # one row per event: the bar of its highest confirmed high
    idx = lat.groupby("group")["anchor_high"].idxmax()
    ev = lat.loc[idx, ["group", "symbol", "snapshot_time_ms", "anchor_high", "base_level",
                       "pump_elapsed_min"]].reset_index(drop=True)
    stamp = pd.to_datetime(ev["snapshot_time_ms"], unit="ms", utc=True)
    ev["mo"] = stamp.dt.strftime("%Y-%m"); ev["week"] = stamp.dt.strftime("%G-W%V")
    print(f"pump events: {len(ev)} across {ev['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    for gi, (symbol, g) in enumerate(ev.groupby("symbol"), 1):
        if gi % 60 == 0 or gi == ev["symbol"].nunique():
            print(f"  {gi} symbols, springs {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        vol = frame["quote_volume"].to_numpy(float)
        atr = causal_atr(high=h, low=low, close=c, window=30)
        for row in g.itertuples():
            hi_idx = int(np.searchsorted(ts, int(row.snapshot_time_ms)))
            if hi_idx >= len(ts) or int(ts[hi_idx]) != int(row.snapshot_time_ms):
                continue
            s = _detect(o, h, low, c, vol, hi_idx, float(row.anchor_high),
                        float(row.base_level), float(row.pump_elapsed_min))
            if s is None:
                continue
            ei = s["entry_index"]
            tp = s["range_high"] * (1 + NEW_HIGH_EPS)
            res_tp = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                        initial_stop_price=s["sweep_low"], spec=SPEC, take_profit_price=tp)
            res_tr = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                        initial_stop_price=s["sweep_low"], spec=SPEC,
                                        swing_reversal_atr=2.5, stage_switch_r=2.0, atr=atr)
            if res_tp.status != STATUS_FILLED:
                continue
            rec = {"symbol": symbol, "mo": row.mo, "week": row.week,
                   "net_tp": res_tp.net_return, "net_trail": res_tr.net_return,
                   "new_high": bool(res_tp.net_return > 0)}
            rec.update({k: s[k] for k in METRICS})
            rows.append(rec)
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    for col, lbl in (("net_tp", "TP@new-high"), ("net_trail", "structural trail")):
        net = df[col].to_numpy()
        s = summarize(net, months=df["mo"].to_numpy(), weeks=df["week"].to_numpy())
        print(s.line(lbl), flush=True)
    rate = df["new_high"].mean()
    print(f"\nbase rate P(new high after reclaim) = {rate*100:.1f}%  (n={len(df)})", flush=True)
    dissect_wins_losses(df, "net_tp", METRICS)


def main() -> None:
    df = build_spring_outcomes()
    out = Path(".output/results/bee_bite_v1/spring_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    report(df)
    print(f"\nsaved {len(df)} springs -> {out}", flush=True)


if __name__ == "__main__":
    main()
