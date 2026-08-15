"""Full WIN vs LOSS profile within each setup (and per regime): what separates winners from losers?

Global direction selectors failed (AUC ~0.53). Here we (a) profile EVERY feature win-vs-loss with
standardized separation + per-year sign-stability, per setup; (b) run a walk-forward win/loss AUC
per setup GLOBALLY and CONDITIONED on BTC regime -- maybe within a regime cell there is separability
the global model missed. This feeds selection (skip losers) and per-cell sizing. IS 2020-2025.
Run: python -m anomaly_science.strategy.prl.research.winloss_profile --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4


def reg(v, thr=0.12):
    return "bull" if v > thr else ("bear" if v < -thr else "side")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv, tbq = (px(c) for c in ("close", "high", "low", "quote_volume", "taker_buy_quote_volume"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    eths = close["ETHUSDT"] if "ETHUSDT" in cols else btcs
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
    btc_r = (btcs / btcs.shift(60 * 24) - 1).to_numpy(); eth_r = (eths / eths.shift(60 * 24) - 1).to_numpy()
    coin_r = (close / close.shift(60 * 24) - 1).to_numpy()
    vs = (qv / qv.rolling(168, min_periods=48).mean()).to_numpy()
    tk = ((2 * tbq - qv) / (qv + 1e-9)).to_numpy()
    m24 = (close / close.shift(24) - 1).to_numpy(); m72 = (close / close.shift(72) - 1).to_numpy()
    emad = (close / close.ewm(span=168, min_periods=84).mean() - 1).to_numpy()
    breadth = above.mean(axis=1).to_numpy(); disp = ret.std(axis=1).to_numpy()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy()

    def feats_at(k, si):
        return dict(vsurge=vs[k, si], taker=tk[k, si], mom24=m24[k, si], mom72=m72[k, si], emadist=emad[k, si],
                    atrp=A[k, si] / C[k, si], coin_r=coin_r[k, si], btc_r=btc_r[k], eth_r=eth_r[k],
                    breadth=breadth[k], disp=disp[k])

    sweep, bd, bo = [], [], []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + 64 >= len(idx) or not (datr[i, si] > 0) or not np.isfinite(coin_r[i, si]):
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
            r = feats_at(j, si); r.update(bn=bn, wick=(ext - lv) / (A[i, si] + 1e-9), reclaim=(lv - C[j, si]) / (A[i, si] + 1e-9),
                                          year=idx[j].year, breg=reg(btc_r[j])); sweep.append(r)
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0) or not np.isfinite(coin_r[e, si]):
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
            r = feats_at(e, si); r.update(bn=bn, risk=risk, year=idx[e].year, breg=reg(btc_r[e])); bd.append(r)
        for i in np.where(brk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]) or not np.isfinite(coin_r[dj, si]):
                continue
            raw = C[dj + 48, si] / C[dj, si] - 1.0 - COST
            r = feats_at(dj, si); r.update(bn=raw, year=idx[dj].year, breg=reg(btc_r[dj])); bo.append(r)

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    for name, rows in [("BREAKOUT", bo), ("SWEEP", sweep), ("BREAKDOWN", bd)]:
        d = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna()
        d["win"] = (d.bn > 0).astype(int)
        feats = [c for c in d.columns if c not in ("bn", "win", "year", "breg", "risk")]
        print(f"\n{'='*66}\n{name}: {len(d)} trades, win-rate {d.win.mean():.0%}")
        print("  feature separation (win-loss, std units; * year-stable sign):")
        recs = []
        for f in feats:
            a, b = d[d.win == 1][f], d[d.win == 0][f]; sd = d[f].std()
            sep = (a.mean() - b.mean()) / sd if sd > 0 else 0
            signs = [np.sign(gy[gy.win == 1][f].mean() - gy[gy.win == 0][f].mean()) for _, gy in d.groupby("year") if gy.win.nunique() == 2]
            stab = len(signs) > 2 and all(s == signs[0] for s in signs)
            recs.append((f, sep, stab))
        for f, sep, stab in sorted(recs, key=lambda x: -abs(x[1]))[:8]:
            print(f"    {f:10s} {sep:+.3f}{'*' if stab else ' '}")
        # walk-forward win/loss AUC global + per regime
        dn = pd.to_datetime(pd.to_datetime(d.index if False else np.arange(len(d))), unit="D")  # placeholder
        dd = d.reset_index(drop=True); order = np.arange(len(dd))
        # use year as time proxy for folds
        yv = dd.year.values
        oof = pd.Series(np.nan, index=dd.index)
        for tr_y, te_y in [([2020, 2021, 2022], [2023, 2024, 2025]), ([2020, 2021, 2022, 2023], [2024, 2025])]:
            tri = dd.index[np.isin(yv, tr_y)]; tei = dd.index[np.isin(yv, te_y)]
            if len(tri) < 300 or dd.loc[tri, "win"].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(dd.loc[tri, feats], dd.loc[tri, "win"]); oof.loc[tei] = cb.predict_proba(dd.loc[tei, feats])[:, 1]
        mm = oof.notna()
        auc = roc_auc_score(dd.loc[mm, "win"], oof[mm]) if mm.sum() > 100 else np.nan
        print(f"  win/loss AUC (walk-forward): global {auc:.3f}")
        for rg in ["bull", "side", "bear"]:
            msk = mm & (dd.breg == rg)
            if msk.sum() > 200 and dd.loc[msk, "win"].nunique() == 2:
                print(f"    within {rg:5s}: AUC {roc_auc_score(dd.loc[msk,'win'], oof[msk]):.3f} (n={int(msk.sum())})")


if __name__ == "__main__":
    main()
