"""Idea #1: on the CONTINUATION pairs (pump into US keeps going -- OVERLAP->US, US->LATE)
trade the anomalous-volume up-pump as a LONG with a structural TRAILING stop (trail
under the last confirmed swing low). This is meant as a DIVERSIFIER for the fade-short:
if it is a real edge that fires at different times, the combined equity is smoother and
the sequencing/tail-risk drops. We report the long's profile and, crucially, the time
correlation with the fade-short so we can judge diversification.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
CONT_PAIRS = ["OVERLAP->US", "US->LATE"]
COST = 0.0008
PIVK = 2; KCONF = 10; MAXBARS = 40; LOOKBACK = 32; FLOOR = 0.04


def build_long(events):
    rows = []
    for sym, g in events.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + MAXBARS + 2 >= n:
                continue
            run_lo = l[e]; ent = None
            for j in range(e + 1, min(e + KCONF, n)):          # structural break UP: close over last swing high
                run_lo = min(run_lo, l[j])
                if np.isfinite(last_ph[j]) and c[j] > last_ph[j]:
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            sl0 = (last_pl[ent] if (np.isfinite(last_pl[ent]) and last_pl[ent] < entry) else run_lo) - 0.3 * a
            sd = (entry - sl0) / entry
            we = min(ent + MAXBARS, n); stop = sl0; exitpx = None
            for j in range(ent + 1, we):
                if l[j] <= stop:
                    exitpx = stop; break
                if np.isfinite(last_pl[j]) and last_pl[j] - 0.3 * a > stop:   # trail up under new swing lows
                    stop = last_pl[j] - 0.3 * a
            if exitpx is None:
                exitpx = c[we - 1]
            net = (exitpx / entry - 1) - COST
            rows.append({"symbol": sym, "week": r.week, "entry_ts": int(ts[ent]),
                         "net_pct": net, "sd": sd, "R": net / max(sd, FLOOR)})
    return pd.DataFrame(rows)


def prof(R, wk, label):
    R = np.asarray(R)
    if len(R) < 80:
        print(f"  {label:<26} n={len(R)} (too few)"); return
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    w = pd.DataFrame({"w": wk, "R": R}).groupby("w").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    print(f"  {label:<26} n={len(R):>5} win={(R>0).mean()*100:>4.1f}% exp={R.mean():>+6.3f}R "
          f"PF={pf:>4.2f} top%toNeg={k/len(R)*100:>4.1f}% posWk={(w>0).mean()*100:>3.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    cont = d[d.pair.isin(CONT_PAIRS)]
    base = cont[(cont.vol_z >= cont.vol_z.quantile(0.9)) & (cont.p_up == 1)]
    strong = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"continuation up-pump events: {len(strong):,}  (building LONG w/ structural trail...)")
    tr = build_long(strong)
    print(f"structure-break-UP confirmed longs: {len(tr):,}\n")
    prof(tr.R.to_numpy(), tr.week.to_numpy(), "CONTINUATION LONG (trail)")

    # weekly R series, for diversification vs the fade-short
    wk = tr.groupby("week").R.sum()
    wk.to_frame("longR").to_parquet(".output/results/session_break/cont_long_weekly.parquet")
    print(f"\n  weekly: posWk={ (wk>0).mean()*100:.0f}%  mean/wk={wk.mean():+.2f}R  saved weekly series for combo.")


if __name__ == "__main__":
    main()
