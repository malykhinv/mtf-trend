"""Structure-based SHORT entry for the pump-fade: enter when price BREAKS the last
confirmed swing LOW (a real lower-low / break of structure down) within the first
bars of the session after the pump -- not the crude first-bar low. Swings via causal
pivots (K bars each side, confirmed K bars later). Compare three stop policies:
  struct : just above the last confirmed swing HIGH (structural, non-noisy)
  pumpHi : above the pump high (wide)
  none   : no stop, hold to exit
Exit = hold ~2 sessions (plus an ATR-target sweep on the structural stop).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
PIVK = 2          # pivot half-window (bars each side)
KCONF = 10        # bars after session open to look for the structure break
MAXBARS = 40
LOOKBACK = 32
TGTS = [1.0, 1.5, 2.0, 2.5, 3.0]


def causal_pivots(h, l, K):
    n = len(h)
    last_pl = np.full(n, np.nan); last_ph = np.full(n, np.nan)
    cpl = np.nan; cph = np.nan
    for j in range(n):
        i = j - K                                   # a pivot at i is confirmed now (needs K bars right)
        if i - K >= 0:
            seg_l = l[i - K:i + K + 1]; seg_h = h[i - K:i + K + 1]
            if l[i] == seg_l.min():
                cpl = l[i]
            if h[i] == seg_h.max():
                cph = h[i]
        last_pl[j] = cpl; last_ph[j] = cph
    return last_pl, last_ph


def prof(R, wk, label):
    R = np.asarray(R)
    if len(R) < 80:
        print(f"  {label:<24} n={len(R)} (too few)"); return
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    wdf = pd.DataFrame({"w": wk, "R": R}).groupby("w").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    print(f"  {label:<24} n={len(R):>5} win={(R>0).mean()*100:>4.1f}% exp={R.mean():>+6.3f}R "
          f"PF={pf:>4.2f} top%toNeg={k/len(R)*100:>4.1f}% posWk={(wdf>0).mean()*100:>3.0f}%")


def build(champ):
    rows = []
    for sym, g in champ.groupby("symbol"):
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
            run_hi = h[e]; ent = None
            for j in range(e + 1, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                pl = last_pl[j]
                if np.isfinite(pl) and c[j] < pl:            # break of structure DOWN (lower low)
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            sh = last_ph[ent]
            stop_struct = (sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a
            stop_pump = float(h[e - LOOKBACK:e].max()) + 0.5 * a
            we = min(ent + MAXBARS, n)
            row = {"symbol": sym, "week": r.week}
            for name, stop in [("struct", stop_struct), ("pumpHi", stop_pump), ("none", np.inf)]:
                sd = (stop - entry) / entry if np.isfinite(stop) else (2 * a / entry)  # 'none' risk-normalised by 2ATR
                exitpx = None
                for j in range(ent + 1, we):
                    if np.isfinite(stop) and h[j] >= stop:
                        exitpx = stop; break
                if exitpx is None:
                    exitpx = c[we - 1]
                net = -(exitpx / entry - 1) - COST
                row[f"R_{name}"] = net / max(sd, 1e-6)
            # target sweep on the structural stop
            sd = (stop_struct - entry) / entry
            for tg in TGTS:
                tgt = entry - tg * a; res = None
                for j in range(ent + 1, we):
                    if h[j] >= stop_struct:
                        res = -(stop_struct / entry - 1) - COST; break
                    if l[j] <= tgt:
                        res = -(tgt / entry - 1) - COST; break
                if res is None:
                    res = -(c[we - 1] / entry - 1) - COST
                row[f"T_{tg}"] = res / max(sd, 1e-6)
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building swing-BOS trades...)")
    tr = build(champ)
    conf_rate = len(tr) / len(champ) * 100
    print(f"structure-break-confirmed trades: {len(tr):,}  ({conf_rate:.0f}% of setups broke structure down)\n")

    print("=== SWING-BOS entry, STOP policy (hold ~2 sessions) ===")
    for name in ["struct", "pumpHi", "none"]:
        prof(tr[f"R_{name}"].to_numpy(), tr.week.to_numpy(), f"stop={name}")

    print("\n=== SWING-BOS entry, structural stop x ATR target ===")
    for tg in TGTS:
        prof(tr[f"T_{tg}"].to_numpy(), tr.week.to_numpy(), f"target {tg} ATR")


if __name__ == "__main__":
    main()
