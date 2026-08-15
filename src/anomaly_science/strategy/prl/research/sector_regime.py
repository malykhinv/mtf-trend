"""Coin-OWN (sector) regime vs BTC regime: does gating by the coin's own trend beat gating by BTC?

User: measure regimes per coin/sector, not only by BTC -- alts have their own timing. The most
granular version: gate each setup by the COIN'S OWN trend/vol regime (its own r60/vol) instead of
BTC's. We (1) measure how often coin-own regime disagrees with BTC (the "alts move on their own"
claim), and (2) compare each setup's beta-neutral edge gated by coin-own home regime vs BTC home
regime (breakout=uptrend, sweep=range, breakdown=downtrend). If coin-own gating is better where
they disagree, sector-regime has real edge. IS 2020-2025. Run:
python -m anomaly_science.strategy.prl.research.sector_regime --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4


def regime_of(r60v, r60_thr=0.12):
    return "bull" if r60v > r60_thr else ("bear" if r60v < -r60_thr else "side")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    br = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(br).div(br.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    hi_lvl = high.resample("1D").max().rolling(20, min_periods=10).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    brk = (above & (~above.shift(1).fillna(False)) & hi_lvl.notna()).to_numpy()
    btc_r60 = (btcs / btcs.shift(60 * 24) - 1).to_numpy()
    coin_r60 = (close / close.shift(60 * 24) - 1).to_numpy()      # each coin's OWN 60d trend
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy(); bk = brk

    sweep, bd, bo = [], [], []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + 4 + 12 + 48 >= len(idx) or not (datr[i, si] > 0) or not np.isfinite(coin_r60[i, si]):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + 5):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 13):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            expx, xb = C[min(start + 48, len(idx) - 1), si], min(start + 48, len(idx) - 1)
            for u in range(start, min(start + 48, len(idx))):
                if C[u, si] >= stop:
                    expx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j, si] * (btc[xb] / btc[j] - 1.0) - (expx / entry - 1.0) - COST
            sweep.append((bn, regime_of(btc_r60[j]), regime_of(coin_r60[j, si])))
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0) or not np.isfinite(coin_r60[e, si]):
                continue
            entry = C[e, si]; istop = SH[e]
            if istop <= entry:
                continue
            risk = (istop - entry) / entry
            if risk <= 0 or risk > 1.0:
                continue
            active = istop; xb, expx = e + 48, C[e + 48, si]
            for t in range(e + 1, e + 49):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t, si] > active:
                    xb, expx = t, C[t, si]; break
            bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - COST
            bd.append((bn, regime_of(btc_r60[e]), regime_of(coin_r60[e, si])))
        for i in np.where(bk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]) or not np.isfinite(coin_r60[dj, si]):
                continue
            entry = C[dj, si]; expx = C[dj + 48, si]
            raw = expx / entry - 1.0 - COST
            bo.append((raw, regime_of(btc_r60[dj]), regime_of(coin_r60[dj, si])))

    def frame(rows):
        d = pd.DataFrame(rows, columns=["pnl", "btc_reg", "coin_reg"]).replace([np.inf, -np.inf], np.nan).dropna()
        return d
    sw, bdf, bof = frame(sweep), frame(bd), frame(bo)
    alld = pd.concat([sw, bdf, bof]);
    print(f"\nsweep {len(sw)}, breakdown {len(bdf)}, breakout {len(bof)}")
    print(f"\ncoin-own vs BTC regime DISAGREEMENT: {(alld.btc_reg != alld.coin_reg).mean()*100:.0f}% of events")
    print("  (high => alts genuinely on their own timing)")

    print("\n=== each setup: edge gated by BTC-home vs COIN-OWN-home regime ===")
    for name, d, home in [("breakout(->uptrend)", bof, "bull"), ("sweep(->range)", sw, "side"), ("breakdown(->downtrend)", bdf, "bear")]:
        base = d.pnl.mean()
        btc_g = d[d.btc_reg == home].pnl.mean()
        coin_g = d[d.coin_reg == home].pnl.mean()
        both = d[(d.btc_reg == home) & (d.coin_reg == home)].pnl.mean()
        print(f"  {name:24s} all={base*100:+.3f}%  BTC-gate={btc_g*100:+.3f}% (n={int((d.btc_reg==home).sum())})  "
              f"COIN-gate={coin_g*100:+.3f}% (n={int((d.coin_reg==home).sum())})  BOTH={both*100:+.3f}%")

    print("\n=== where they DISAGREE: which regime label is right? (edge when only one says home) ===")
    for name, d, home in [("breakout", bof, "bull"), ("sweep", sw, "side"), ("breakdown", bdf, "bear")]:
        only_coin = d[(d.coin_reg == home) & (d.btc_reg != home)].pnl.mean()
        only_btc = d[(d.btc_reg == home) & (d.coin_reg != home)].pnl.mean()
        print(f"  {name:12s} only-COIN-home={only_coin*100:+.3f}%  only-BTC-home={only_btc*100:+.3f}%")


if __name__ == "__main__":
    main()
