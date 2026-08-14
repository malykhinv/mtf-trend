"""MAX sweep-fade: structural ATR-zigzag levels + reinforcing factors -> best sleeve + 3-book.

Combine everything that helped: structural swing levels (ATR-zigzag N=2, uniform + high volume),
the limit-retest short, close-based stop, 1d-ATR target, and factor tilts we found -- funding
(crowded-long: high funding => breakout more likely to fail => better fade) and formation impulse
(prominent sweeping levels). Build the risk-managed sleeve (unweighted vs funding/impulse-weighted),
correlation with PRL, and the three-sleeve book PRL+breakout+sweep -> Calmar / per-year DD / lever
to the user's targets. IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.sweep_max --n 150
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.sweep_structural import zigzag_highs
from anomaly_science.strategy.prl.research.three_sleeve_book import stat, per_year_dd, line, vt, rp

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"
KFAIL, RETEST_WIN, MAXH, LIVE, THR = 4, 12, 48, 240, 2
COST = 6 / 1e4


def build_structural(n):
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    pc = close.shift(1)
    atr1h = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    breadth = (close >= close.rolling(480, min_periods=100).max().shift(1)).mean(axis=1).to_numpy()
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f); tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(tt)["funding_rate"].last()
    fund = (pd.DataFrame(fu).reindex(idx).ffill().reindex(columns=cols).to_numpy() if fu else np.zeros((len(idx), len(cols))))
    C, Hh, L, AT = (x.to_numpy() for x in (close, high, low, atr1h))
    rows = []
    for si in range(len(cols)):
        for (pi, Hlvl, plow, imp, ci) in zigzag_highs(Hh[:, si], L[:, si], AT[:, si], THR):
            if not (datr_pct[ci, si] > 0):
                continue
            swept = None
            for t in range(ci, min(ci + LIVE, len(idx))):
                if Hh[t, si] >= Hlvl:
                    jf = None
                    for u in range(t, min(t + KFAIL + 1, len(idx))):
                        if C[u, si] < Hlvl:
                            jf = u; break
                    if jf is not None:
                        swept = (t, jf, np.nanmax(Hh[t:jf + 1, si]))
                    break
            if swept is None:
                continue
            t, jf, ext = swept
            if jf + RETEST_WIN + MAXH >= len(idx):
                continue
            datrp = datr_pct[jf, si]; entry = None
            for u in range(jf + 1, jf + 1 + RETEST_WIN):
                if Hh[u, si] >= Hlvl:
                    entry = Hlvl; start = u + 1; break
            if entry is None:
                continue
            stop = ext + 0.25 * datrp * entry; tp = entry * (1 - 1.0 * datrp)
            out, exitpx, xb = "time", C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
            for u in range(start, min(start + MAXH, len(idx))):
                if C[u, si] >= stop:
                    out, exitpx, xb = "stop", C[u, si], u; break
                if L[u, si] <= tp:
                    out, exitpx, xb = "tp", tp, u; break
            mrel = (entry - exitpx) / entry + (btc[xb] / btc[jf] - 1.0) - COST
            rows.append(dict(entry_t=idx[start - 1], exit_t=idx[xb], mrel=mrel,
                             risk=(stop - entry) / entry, imp=imp, breadth=breadth[jf], fund=fund[jf, si]))
    return pd.DataFrame(rows), idx


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    tr, hidx = build_structural(args.n)
    print(f"structural sweeps: {len(tr)}  exp={tr.mrel.mean()*100:+.3f}%")
    hpos = {t: k for k, t in enumerate(hidx)}

    def sleeve(weight=None, risk_per=0.005, maxconc=20):
        pnl_h = np.zeros(len(hidx)); open_exits = []
        w = (weight.values if weight is not None else np.ones(len(tr)))
        for (_, r), wi in zip(tr.sort_values("entry_t").iterrows(), w):
            je, xe = hpos[r.entry_t], hpos[r.exit_t]
            open_exits = [e for e in open_exits if e > je]
            if len(open_exits) >= maxconc:
                continue
            open_exits.append(xe)
            pnl_h[xe] += wi * (risk_per / max(r.risk, 1e-4)) * r.mrel
        d = pd.Series(pnl_h, index=hidx).resample("1D").sum()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        return d

    # factor tilts (causal, per-trade multiplicative weight in [0.5,1.5])
    def tilt(col, sign):
        z = (tr[col] - tr[col].median()) / (tr[col].std() + 1e-9)
        return (1 + sign * 0.4 * np.tanh(z)).clip(0.3, 1.7)

    sl_base = sleeve()
    sl_fund = sleeve(tilt("fund", +1))      # high funding (crowded long) -> bigger fade
    sl_imp = sleeve(tilt("imp", +1))        # bigger impulse level -> bigger fade
    sl_both = sleeve(tilt("fund", +1) * tilt("imp", +1))

    # daily PRL + breakout
    from anomaly_science.strategy.xsect_momentum.research import panel as pn
    from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
    from anomaly_science.strategy.prl.research import factors as fx
    from anomaly_science.strategy.prl.research.anatomy import run_book
    from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
    from anomaly_science.strategy.prl.research.policy import PRIMARY
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]; ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    score = oof.assign(date=pd.to_datetime(oof["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo = norm(prl), norm(bo)

    print("\n=== structural sweep-fade sleeves ===")
    for nm, s in (("base", sl_base), ("funding-tilt", sl_fund), ("impulse-tilt", sl_imp), ("fund+imp", sl_both)):
        a, b = s.align(prl, join="inner")
        line(f"sweep {nm}", s); print(f"      corr(PRL) {a.corr(b):+.2f}")
    best = max([sl_base, sl_fund, sl_imp, sl_both], key=lambda s: stat(s)["calmar"])

    print("\n=== three-sleeve book (PRL + BO + best structural sweep) ===")
    line("PRL+BO", rp([prl, bo]))
    d3 = rp([prl, bo, best]); line("PRL+BO+Sweep(struct)", d3)
    for tgt, lbl in ((-0.15, "per-year DD<=15%"), (None, "+150% worst-year")):
        L = None
        for LL in np.arange(1.0, 30.01, 0.1):
            dd_ok = (tgt is not None and min(per_year_dd(LL * d3).values()) <= tgt)
            ret_ok = (tgt is None and min(stat(LL * d3)["yr"].values()) >= 1.50)
            if dd_ok or ret_ok:
                L = LL; break
        if L is None:
            print(f"  {lbl}: not reached"); continue
        s = stat(L * d3); ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
        print(f"  {lbl}: L={L:.1f}x worst-year {min(s['yr'].values())*100:+.0f}% CAGR {s['cagr']*100:+.0f}% "
              f"maxDD {s['dd']*100:+.0f}% worstYrDD {s['worst_pyd']*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
