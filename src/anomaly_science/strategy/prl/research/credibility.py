"""CREDIBILITY battery for the sweep-fade edge -- what real science checks before believing it.

The result looks too sweet. Before spending the reserved OOS we run the honesty battery on IS:
  1. HEDGE DECOMPOSITION: mrel = short_coin + long_BTC(hedge). Is the edge in the fade (coin
     underperforms market after the sweep) or is it hidden BTC beta in an up-market? Report raw
     short, hedge contribution, and a BETA-hedged version, per year.
  2. CONCENTRATION: share of total PnL from the top coins / top days / top months; does the edge
     SURVIVE dropping the best coin and the best month? (knife-catch lesson: one crash week.)
  3. BOOTSTRAP CI: block-bootstrap 95% CI on mean per-trade PnL (is +0.36% significant?).
  4. PARAMETER PLATEAU: vary KFAIL / MAXH / target-ATR / stop-buf -- sharp peak (overfit) vs broad
     plateau (robust)?
  5. PLACEBOS: reverse the signal (go LONG the sweep -> must be ~ -edge); big time-shift entry
     (+10 days -> must decorrelate to ~0).
  6. TEMPORAL STABILITY: monthly expectancy, % positive months.
IS only, OOS untouched. Run: python -m anomaly_science.strategy.prl.research.credibility --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, RETEST_WIN, MAXH = 20, 4, 12, 48


def load(n):
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    return idx, cols, close.to_numpy(), high.to_numpy(), low.to_numpy(), hi_lvl.to_numpy(), datr_pct, poke.to_numpy(), btc


def build(idx, cols, C, Hh, L, HL, datr_pct, pk, btc, kfail=4, retw=12, maxh=48, tp_atr=1.0, sbuf=0.25,
          side=+1, tshift=0):
    """side=+1 fade short; side=-1 reverse (long). tshift shifts entry bar by +tshift (placebo)."""
    rows = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + kfail + retw + maxh + tshift + 2 >= len(idx) or not (datr_pct[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + kfail + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si])
            entry = None
            for u in range(j + 1, j + 1 + retw):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1 + tshift; break
            if entry is None or start >= len(idx):
                continue
            datrp = datr_pct[j, si]
            if side > 0:
                stop = ext + sbuf * datrp * entry; tp = entry * (1 - tp_atr * datrp)
            else:
                stop = entry * (1 - sbuf * datrp); tp = entry * (1 + tp_atr * datrp)   # reversed long
            out, exitpx, xb = "time", C[min(start + maxh, len(idx) - 1), si], min(start + maxh, len(idx) - 1)
            for u in range(start, min(start + maxh, len(idx))):
                if side > 0 and C[u, si] >= stop:
                    exitpx, xb = C[u, si], u; break
                if side > 0 and L[u, si] <= tp:
                    exitpx, xb = tp, u; break
                if side < 0 and C[u, si] <= stop:
                    exitpx, xb = C[u, si], u; break
                if side < 0 and Hh[u, si] >= tp:
                    exitpx, xb = tp, u; break
            raw = side * ((entry - exitpx) / entry) if side > 0 else side * 0 + (exitpx - entry) / entry
            raw = (entry - exitpx) / entry if side > 0 else (exitpx - entry) / entry
            hedge = (btc[xb] / btc[j] - 1.0) * (1 if side > 0 else -1)
            rows.append(dict(si=si, sym=cols[si], date=idx[start - 1], raw=raw, hedge=hedge,
                             mrel=raw + hedge - 6 / 1e4))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    D = load(args.n)
    tr = build(*D)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["ym"] = pd.to_datetime(tr["date"]).dt.to_period("M")
    print(f"trades: {len(tr)}  mrel {tr.mrel.mean()*100:+.3f}%\n")

    print("=== 1. HEDGE DECOMPOSITION (is the edge the fade or hidden BTC beta?) ===")
    for y, g in tr.groupby("year"):
        print(f"    {y}: raw-short {g.raw.mean()*100:+.3f}%  +hedge {g.hedge.mean()*100:+.3f}%  = mrel {g.mrel.mean()*100:+.3f}%")
    print(f"    ALL: raw-short {tr.raw.mean()*100:+.3f}%  +hedge {tr.hedge.mean()*100:+.3f}%  = mrel {tr.mrel.mean()*100:+.3f}%")
    print(f"    -> {'EDGE IS THE FADE (raw short positive)' if tr.raw.mean()>0 else 'WARNING: raw short <=0, edge leans on the BTC hedge'}")

    print("\n=== 2. CONCENTRATION (survive dropping the best coin / month?) ===")
    bycoin = tr.groupby("sym").mrel.sum().sort_values(ascending=False)
    bymon = tr.groupby("ym").mrel.sum().sort_values(ascending=False)
    tot = tr.mrel.sum()
    print(f"    top-5 coins = {bycoin.head(5).sum()/tot*100:.0f}% of total PnL; top-1 coin {bycoin.iloc[0]/tot*100:.0f}% ({bycoin.index[0]})")
    print(f"    top-3 months = {bymon.head(3).sum()/tot*100:.0f}% of total PnL; top-1 month {bymon.iloc[0]/tot*100:.0f}% ({bymon.index[0]})")
    drop_coin = tr[tr.sym != bycoin.index[0]]; drop_mon = tr[tr.ym != bymon.index[0]]
    print(f"    drop best coin  -> mrel {drop_coin.mrel.mean()*100:+.3f}% (n={len(drop_coin)})")
    print(f"    drop best month -> mrel {drop_mon.mrel.mean()*100:+.3f}% (n={len(drop_mon)})")

    print("\n=== 3. BLOCK-BOOTSTRAP 95% CI on mean per-trade mrel (weekly blocks) ===")
    tr2 = tr.copy(); tr2["wk"] = pd.to_datetime(tr2["date"]).dt.to_period("W")
    blocks = [g.mrel.values for _, g in tr2.groupby("wk")]
    rng = np.random.default_rng(0); means = []
    for _ in range(2000):
        samp = np.concatenate([blocks[k] for k in rng.integers(0, len(blocks), len(blocks))])
        means.append(samp.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    print(f"    mean {tr.mrel.mean()*100:+.3f}%  95% CI [{lo*100:+.3f}%, {hi*100:+.3f}%]  -> {'significant >0' if lo>0 else 'CROSSES 0'}")

    print("\n=== 4. PARAMETER PLATEAU (edge stable in a neighborhood?) ===")
    for name, kw in [("KFAIL=2", dict(kfail=2)), ("KFAIL=6", dict(kfail=6)),
                     ("MAXH=24", dict(maxh=24)), ("MAXH=72", dict(maxh=72)),
                     ("tp=0.75", dict(tp_atr=0.75)), ("tp=1.5", dict(tp_atr=1.5)),
                     ("sbuf=0.1", dict(sbuf=0.1)), ("sbuf=0.5", dict(sbuf=0.5))]:
        g = build(*D, **kw)
        ys = " ".join(f"{y}:{gg.mrel.mean()*100:+.2f}" for y, gg in g.assign(year=pd.to_datetime(g.date).dt.year).groupby("year"))
        print(f"    {name:10s} mrel {g.mrel.mean()*100:+.3f}%  [{ys}]")

    print("\n=== 5. PLACEBOS ===")
    rev = build(*D, side=-1)
    shift = build(*D, tshift=240)      # enter 10 days after the retest (decorrelated)
    print(f"    reverse signal (LONG the sweep)  mrel {rev.mrel.mean()*100:+.3f}%  (should be << real, ~ -edge)")
    print(f"    entry +240h time-shift            mrel {shift.mrel.mean()*100:+.3f}%  (should decorrelate to ~0)")

    print("\n=== 6. TEMPORAL STABILITY ===")
    m = tr.groupby("ym").mrel.mean()
    print(f"    months positive: {(m>0).mean()*100:.0f}%  ({int((m>0).sum())}/{len(m)})  median monthly {m.median()*100:+.3f}%")


if __name__ == "__main__":
    main()
