"""Honest profit-concentration analysis of the swing-BOS pump-fade short. We measure
each trade in FIXED position size (net % of price), not R=pnl/stop_dist (which
explodes on tight stops and fakes tail-dependence). Then: what share of gross profit
comes from the top 5/10/25/50% of trades, and how many top trades you must remove
before cumulative goes negative -- the real 'do we sit for one saviour trade' test.
Compared against the R-normalised view to expose the artefact.
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
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
PIVK = 2; KCONF = 10; MAXBARS = 40; LOOKBACK = 32
TARGET_ATR = 2.5
MIN_STOP_FRAC = 0.006     # floor the stop distance (~0.6%) so R can't explode on tiny swings


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
                if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            sh = last_ph[ent]
            stop = (sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a
            sd = (stop - entry) / entry
            sd_floor = max(sd, MIN_STOP_FRAC)
            tgt = entry - TARGET_ATR * a
            we = min(ent + MAXBARS, n); res = None
            for j in range(ent + 1, we):
                if h[j] >= stop:
                    res = -(stop / entry - 1) - COST; break
                if l[j] <= tgt:
                    res = -(tgt / entry - 1) - COST; break
            if res is None:
                res = -(c[we - 1] / entry - 1) - COST
            rows.append({"symbol": sym, "week": r.week, "pair": r.pair,
                         "net_pct": res, "R_raw": res / max(sd, 1e-6), "R_floor": res / sd_floor})
    return pd.DataFrame(rows)


def concentration(x, name):
    x = np.asarray(x); tot = x.sum()
    srt = np.sort(x)[::-1]
    n = len(x)
    # share of gross profit from top buckets
    gp = x[x > 0].sum()
    shares = {p: srt[:max(1, int(n * p))][srt[:max(1, int(n * p))] > 0].sum() / gp * 100 for p in (0.05, 0.10, 0.25, 0.50)}
    # remove top-k until cumulative <= 0
    run = tot; k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    print(f"\n  [{name}] mean={x.mean()*100:+.3f}%  median={np.median(x)*100:+.3f}%  total={tot*100:+.0f}%")
    print(f"    gross-profit share:  top5%={shares[0.05]:.0f}%  top10%={shares[0.10]:.0f}%  "
          f"top25%={shares[0.25]:.0f}%  top50%={shares[0.50]:.0f}%")
    print(f"    remove top {k/n*100:.1f}% of trades -> total turns negative")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building swing-BOS target-{TARGET_ATR}ATR trades...)")
    tr = build(champ)
    print(f"trades: {len(tr):,}  win={ (tr.net_pct>0).mean()*100:.1f}%")

    concentration(tr.R_raw, "R = pnl/stop_dist (raw -- explodes on tight stops)")
    concentration(tr.R_floor, f"R with stop floored at {MIN_STOP_FRAC*100:.1f}%")
    concentration(tr.net_pct, "FIXED position size (net % of price) -- the deployable view")

    # winners/losers size in fixed %
    w = tr[tr.net_pct > 0].net_pct; ll = tr[tr.net_pct <= 0].net_pct
    print(f"\n  FIXED-size: avg win={w.mean()*100:+.2f}%  avg loss={ll.mean()*100:+.2f}%  "
          f"win/loss size ratio={-w.mean()/ll.mean():.2f}")
    print(f"    median win={w.median()*100:+.2f}%  max win={w.max()*100:+.1f}%  "
          f"median loss={ll.median()*100:+.2f}%  max loss={ll.min()*100:+.1f}%")

    # weekly, fixed size
    wk = tr.groupby("week").net_pct.sum()
    print(f"    weekly (fixed size): positive weeks={ (wk>0).mean()*100:.0f}%  "
          f"best={wk.max()*100:+.1f}%  worst={wk.min()*100:+.1f}%  mean/wk={wk.mean()*100:+.2f}%")


if __name__ == "__main__":
    main()
