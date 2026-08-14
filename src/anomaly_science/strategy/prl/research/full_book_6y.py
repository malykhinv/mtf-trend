"""Full 6-year book (2020-2025): PRL + breakout-long + sweep-fade + breakdown-short.

Now that 2020-2022 is downloaded and the daily panel rebuilt, run the WHOLE book across
bull (2021), bear (2022), COVID-crash (2020) and 2023-25, to see end-to-end year-uniformity
of the regime-complementary portfolio. PRL uses the causal residual-momentum score (all years);
breakout is Donchian long-beta; the two SHORT alpha sleeves (sweep-fade закол reversion +
breakdown-short continuation) are beta-neutral, risk-managed daily sleeves from 1h. Report each
sleeve by-year, pairwise correlations, and the combined book (vol-parity + vol-target) uniformity
+ Calmar. IS only (<2026), OOS reserved. Run: python -m anomaly_science.strategy.prl.research.full_book_6y --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4


def short_trades(n):
    """Build sweep-fade (закол) and breakdown-short beta-neutral trades from 1h, 2020-2025."""
    panel = load_1h(_liquid(n))
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
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy()
    KF, RW, MH = 4, 12, 48

    sweep = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KF + RW + MH >= len(idx) or not (datr[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KF + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 1 + RW):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            expx, xb = C[min(start + MH, len(idx) - 1), si], min(start + MH, len(idx) - 1)
            for u in range(start, min(start + MH, len(idx))):
                if C[u, si] >= stop:
                    expx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j, si] * (btc[xb] / btc[j] - 1.0) - (expx / entry - 1.0) - COST
            sweep.append(dict(entry_t=idx[start - 1], exit_t=idx[xb], bn=bn, risk=(stop - entry) / entry))

    bd = []
    for si in range(len(cols)):
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)   # N=4 stretch
        below = C[:, si] < SL
        fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + MH >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0):
                continue
            entry = C[e, si]; istop = SH[e]
            if istop <= entry:
                continue
            risk = (istop - entry) / entry
            if risk <= 0 or risk > 1.0:
                continue
            active = istop; xb, expx = e + MH, C[e + MH, si]
            for t in range(e + 1, e + MH + 1):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t, si] > active:
                    xb, expx = t, C[t, si]; break
            bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - COST
            bd.append(dict(entry_t=idx[e], exit_t=idx[xb], bn=bn, risk=risk))
    return pd.DataFrame(sweep), pd.DataFrame(bd), idx


def daily_sleeve(tr, hidx, risk_per=0.005, maxconc=20):
    hpos = {t: k for k, t in enumerate(hidx)}
    pnl = np.zeros(len(hidx)); open_ex = []
    for _, r in tr.sort_values("entry_t").iterrows():
        je, xe = hpos[r.entry_t], hpos[r.exit_t]
        open_ex = [e for e in open_ex if e > je]
        if len(open_ex) >= maxconc:
            continue
        open_ex.append(xe)
        pnl[xe] += (risk_per / max(r.risk, 1e-4)) * r.bn
    d = pd.Series(pnl, index=hidx).resample("1D").sum()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


def yr(d):
    return {int(y): (1 + g).prod() - 1 for y, g in d.dropna().groupby(d.dropna().index.year)}


def line(nm, d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1; sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    y = yr(d); mn = min(y.values())
    ys = " ".join(f"{k}:{v*100:+.0f}%" for k, v in y.items())
    print(f"  {nm:24s} Sh={sh:+.2f} CAGR={cagr*100:+4.0f}% mDD={dd*100:+4.0f}% Cal={cagr/abs(dd) if dd<0 else 0:4.1f} minYr={mn*100:+.0f}%  [{ys}]")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building short sleeves from 1h (2020-2025) ...")
    swtr, bdtr, hidx = short_trades(args.n)
    print(f"  sweep trades {len(swtr)}, breakdown trades {len(bdtr)}")
    sweep = daily_sleeve(swtr, hidx); bdown = daily_sleeve(bdtr, hidx)

    print("building PRL + breakout from daily panel (2020-2025) ...")
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    ret = close.pct_change(fill_method=None)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sweep, bdown = map(norm, (prl, bo, sweep, bdown))
    A = pd.concat([prl.rename("PRL"), bo.rename("BO"), sweep.rename("SW"), bdown.rename("BD")], axis=1).dropna()

    print("\n=== individual sleeves (2020-2025, honest/beta-neutral shorts) ===")
    for c in ["PRL", "BO", "SW", "BD"]:
        line(c, A[c])
    print(f"\n  corr matrix:\n{A.corr().round(2)}")

    def vt(d, lb=20, cap=3.0):
        v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0); return d * sc

    def rp(cols_):
        al = A[cols_]; inv = 1 / al.std(); return (al * (inv / inv.sum())).sum(axis=1)

    print("\n=== combined books (vol-parity) ===")
    line("PRL+BO", rp(["PRL", "BO"]))
    line("PRL+BO+SW", rp(["PRL", "BO", "SW"]))
    line("PRL+BO+SW+BD", rp(["PRL", "BO", "SW", "BD"]))
    line("ALL + vol-target", vt(rp(["PRL", "BO", "SW", "BD"])))


if __name__ == "__main__":
    main()
