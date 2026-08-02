"""Both directions on the mature pump events (RR=1, reward>3%): SHORT on a structure
break DOWN (fade), LONG on a structure break UP (continuation). Read the win rate of
each by session -- hypothesis: liquid-session (ASIA/EU) pumps FADE, low-liquidity
(offhours/US) pumps CONTINUE. Uncorrelated sessions -> a diversified pair of edges.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
STATES = Path(".output/results/pump_fade_lifecycle_v3/online_states.parquet")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
PIVK = 2; KCONF = 12; PEAK_WIN = 8; HORIZON = 48; REWARD_MIN = 0.03


def race(h, l, ent, we, tgt, stp, is_long):
    for j in range(ent + 1, we):
        if is_long:
            if l[j] <= stp:
                return 0
            if h[j] >= tgt:
                return 1
        else:
            if h[j] >= stp:
                return 0
            if l[j] <= tgt:
                return 1
    return None


def build_symbol(sym, ev):
    p = CACHE / f"{sym}.parquet"
    if not p.exists():
        return []
    df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
    ts = df["timestamp"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
    last_pl, last_ph = causal_pivots(h, l, PIVK)
    out = []
    for r in ev.itertuples():
        ign = int(r.ignition_time_ms); base = float(r.base_level)
        bi = int(np.searchsorted(ts, ign, side="right")) - 1
        if bi < 40 or bi + HORIZON + 4 >= n:
            continue
        pk_bar = bi + int(np.argmax(h[bi:min(bi + PEAK_WIN, n)]))
        peak = float(h[pk_bar])
        pump = (peak - base) / base if base > 0 else 0.0
        reward = 0.5 * pump
        if reward < REWARD_MIN:
            continue
        wk = pd.Timestamp(int(ts[pk_bar]), unit="ms", tz="UTC").strftime("%G-W%V")
        # SHORT: structure break down
        se = None
        for j in range(pk_bar + 1, min(pk_bar + KCONF, n)):
            if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                se = j; break
        ys = None
        if se is not None and se + HORIZON + 2 < n:
            e = float(c[se]); ys = race(h, l, se, min(se + HORIZON, n), e * (1 - reward), e * (1 + reward), False)
        # LONG: BUY THE PULLBACK, not the breakout. Wait for a pullback pivot low
        # (higher-low) after the peak, then enter on the reclaim above the swing high.
        plb = None; le = None
        for j in range(pk_bar + 1, min(pk_bar + 2 * KCONF, n)):
            if plb is None:
                if np.isfinite(last_pl[j]) and (j - pk_bar) >= PIVK and last_pl[j] > base:
                    plb = j
            elif np.isfinite(last_ph[j]) and c[j] > last_ph[j]:
                le = j; break
        yl = None
        if le is not None and le + HORIZON + 2 < n:
            e = float(c[le]); yl = race(h, l, le, min(le + HORIZON, n), e * (1 + reward), e * (1 - reward), True)
        if ys is None and yl is None:
            continue
        out.append({"symbol": sym, "session": r.session, "week": wk, "pump": pump, "reward": reward,
                    "y_short": ys if ys is not None else np.nan, "y_long": yl if yl is not None else np.nan})
    return out


def wr(s, col, lbl):
    s = s[np.isfinite(s[col])]
    if len(s) < 80:
        print(f"    {lbl:<16} n={len(s)} (few)"); return
    wk = s.groupby("week")[col].mean()
    print(f"    {lbl:<16} n={len(s):>4} win={s[col].mean():.3f}  posWk={(wk>0.5).mean()*100:.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/pump_dir.parquet"))
    args = ap.parse_args()
    st = pd.read_parquet(STATES, columns=["event_id", "symbol", "snapshot_time_ms", "ignition_time_ms", "base_level", "session"])
    st = st.sort_values(["event_id", "snapshot_time_ms"]).drop_duplicates("event_id", keep="first")
    st = st[st.ignition_time_ms < DEV_END]
    rows = []; syms = st.symbol.unique()
    for i, sym in enumerate(syms):
        rows += build_symbol(sym, st[st.symbol == sym])
        if (i + 1) % 150 == 0:
            print(f"  {i+1}/{len(syms)} events={len(rows):,}")
    t = pd.DataFrame(rows)
    t.to_parquet(args.out)
    print(f"\nevents={len(t):,}")
    for s in ["asia", "eu", "us", "offhours"]:
        g = t[t.session == s]
        print(f"\n  === {s.upper()} ===")
        wr(g, "y_short", "SHORT (fade)")
        wr(g, "y_long", "LONG (continue)")
    print("\n  === POOLED best-side per session ===")
    wr(t[t.session.isin(["asia", "eu"])], "y_short", "asia+eu SHORT")
    wr(t[t.session.isin(["offhours", "us"])], "y_long", "offh+us LONG")


if __name__ == "__main__":
    main()
