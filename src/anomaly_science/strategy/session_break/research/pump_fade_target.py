"""Reduce tail-dependence and raise win rate on the pump-fade short by using a FIXED
ATR target + ATR stop (mean-reversion style) instead of ride-the-fade. Sweep
stop x target; for each report win rate, R:R, expectancy, tail-concentration (% of
top trades to remove before total goes negative -- higher is more robust), profit
factor and weekly stability. A tight target makes wins uniform, so the edge stops
depending on a few fat tails.
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
MAXBARS = 48
LOOKBACK = 32
STOPS = [1.5, 2.0, 3.0]
TGTS = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]


def sim(o, h, l, c, atr, e, we, stop_atr, tgt_atr, partial=False):
    entry = o[e]; a = atr[e] if atr[e] > 0 else entry * 0.005
    if entry <= 0:
        return None
    stop = entry + stop_atr * a; tgt = entry - tgt_atr * a
    sd = stop_atr * a / entry
    rem = 1.0; pnl = 0.0
    for j in range(e, we):
        if h[j] >= stop:                              # adverse first (pessimistic)
            pnl += -(stop / entry - 1) * rem; rem = 0; break
        if l[j] <= tgt:
            if partial and rem > 0.999:
                pnl += -(tgt / entry - 1) * 0.5; rem = 0.5; stop = min(stop, entry)  # BE runner
            else:
                pnl += -(tgt / entry - 1) * rem; rem = 0; break
    if rem > 0:
        pnl += -(c[we - 1] / entry - 1) * rem
    net = pnl - COST
    return net, net / sd


def build(champ):
    per = {}
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        ts = df["timestamp"]; n = len(ts)
        idxs = []
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e < n and int(ts[e]) == int(r.start_ts) and e >= LOOKBACK and e + 3 < n:
                idxs.append((e, r.week))
        per[sym] = (o, h, l, c, atr, n, idxs)
    return per


def evaluate(per, stop_atr, tgt_atr, partial=False):
    Rs = []; weeks = []
    for sym, (o, h, l, c, atr, n, idxs) in per.items():
        for e, wk in idxs:
            res = sim(o, h, l, c, atr, e, min(e + MAXBARS, n), stop_atr, tgt_atr, partial)
            if res:
                Rs.append(res[1]); weeks.append(wk)
    R = np.array(Rs)
    if len(R) < 100:
        return None
    win = (R > 0).mean()
    exp = R.mean()
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    frac_top = k / len(R)
    wdf = pd.DataFrame({"w": weeks, "R": R}).groupby("w").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    return dict(n=len(R), win=win, exp=exp, med=np.median(R), pf=pf,
                top=frac_top, poswk=(wdf > 0).mean(), rr=tgt_atr / stop_atr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building bars...)")
    per = build(champ)

    print(f"\n{'stop':>5}{'tgt':>5}{'R:R':>6}{'win%':>7}{'exp(R)':>8}{'med':>7}{'PF':>6}{'top%toNeg':>11}{'posWk%':>8}")
    for s in STOPS:
        for tg in TGTS:
            r = evaluate(per, s, tg)
            if r:
                print(f"{s:>5.1f}{tg:>5.2f}{r['rr']:>6.2f}{r['win']*100:>6.1f}%{r['exp']:>+8.3f}{r['med']:>+7.2f}"
                      f"{r['pf']:>6.2f}{r['top']*100:>10.1f}%{r['poswk']*100:>7.0f}%")

    print("\n### PARTIAL 0.5 at target + BE runner (best-win configs) ###")
    for s, tg in [(2.0, 0.75), (2.0, 1.0), (1.5, 0.75), (3.0, 1.0)]:
        r = evaluate(per, s, tg, partial=True)
        if r:
            print(f"  stop {s} tgt {tg}: win={r['win']*100:.1f}% exp={r['exp']:+.3f}R PF={r['pf']:.2f} "
                  f"top%toNeg={r['top']*100:.1f}% posWk={r['poswk']*100:.0f}%")


if __name__ == "__main__":
    main()
