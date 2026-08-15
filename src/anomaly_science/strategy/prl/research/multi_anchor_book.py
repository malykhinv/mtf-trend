"""Multi-anchor regime-gated book vs baseline BTC-only gate + where does it HURT (year/month/week)?

Optimal gates from the exhaustive grid: breakout -> BTC bull; sweep -> BTC SIDE (not bull); breakdown
-> coin-bear AND BTC-bear. Build this book (with PRL) and compare to the baseline BTC-only gate
(breakout bull, sweep bull+side, breakdown bear). Then find every YEAR / MONTH / WEEK where the
refined book UNDERPERFORMS the baseline -- the multi-anchor gate trades per-trade quality for fewer
bets, so it can hurt in some periods; we surface them honestly. IS 2020-2025. Run:
python -m anomaly_science.strategy.prl.research.multi_anchor_book --n 150
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
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag
from anomaly_science.strategy.prl.research.final_dashboard import daily_sleeve

COST = 6 / 1e4


def reg(v, thr=0.12):
    return "bull" if v > thr else ("bear" if v < -thr else "side")


def build(n):
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
    brk = (above & (~above.shift(1).fillna(False)) & hi_lvl.notna()).to_numpy()
    btc_r = (btcs / btcs.shift(60 * 24) - 1).to_numpy(); coin_r = (close / close.shift(60 * 24) - 1).to_numpy()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy(); rows = {"sweep": [], "breakdown": [], "breakout": []}
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
            rows["sweep"].append((idx[start - 1], idx[xb], bn, (stop - entry) / entry, reg(coin_r[j, si]), reg(btc_r[j])))
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
            rows["breakdown"].append((idx[e], idx[xb], bn, risk, reg(coin_r[e, si]), reg(btc_r[e])))
        for i in np.where(brk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]) or not np.isfinite(coin_r[dj, si]):
                continue
            raw = C[dj + 48, si] / C[dj, si] - 1.0 - COST
            rows["breakout"].append((idx[dj], idx[dj + 48], raw, 0.05, reg(coin_r[dj, si]), reg(btc_r[dj])))
    mk = lambda r: pd.DataFrame(r, columns=["entry_t", "exit_t", "pnl", "risk", "coin", "btc"])
    return mk(rows["sweep"]), mk(rows["breakdown"]), mk(rows["breakout"]), idx


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building trades ...")
    sw, bd, bo, hidx = build(args.n)

    def sleeves(scheme):
        if scheme == "baseline":
            s = sw[sw.btc.isin(["bull", "side"])]; b = bd[bd.btc == "bear"]; o = bo[bo.btc == "bull"]
        else:  # refined multi-anchor optimum
            s = sw[sw.btc == "side"]; b = bd[(bd.coin == "bear") & (bd.btc == "bear")]; o = bo[bo.btc == "bull"]
        return daily_sleeve(s, hidx), daily_sleeve(b, hidx), daily_sleeve(o, hidx, risk_per=0.01, maxconc=15, dcap=0.06)

    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    prl, _, _ = run_book(close, close.pct_change(fill_method=None), um, score, p, step=15, hyst=0.5)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl = norm(prl)

    def vt(d, lb=20, cap=3.0):
        v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0); return d * sc

    def book(scheme):
        s, b, o = map(norm, sleeves(scheme))
        A = pd.concat([prl.rename("p"), o.rename("bo"), s.rename("sw"), b.rename("bd")], axis=1).dropna()
        inv = 1 / A.std(); return vt((A * (inv / inv.sum())).sum(axis=1))

    base = book("baseline"); ref = book("refined")
    A = pd.concat([base.rename("base"), ref.rename("ref")], axis=1).dropna()

    def st(d):
        eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min()); c = eq.iloc[-1] ** (252 / len(d)) - 1
        return c, dd, d.mean() / d.std() * np.sqrt(252)

    print("\n=== baseline (BTC-only gate) vs refined (multi-anchor optimum) ===")
    for nm, d in [("baseline", A.base), ("refined", A.ref)]:
        c, dd, sh = st(d); yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
        ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
        print(f"  {nm:10s} CAGR={c*100:+4.0f}% Sh={sh:+.2f} maxDD={dd*100:+3.0f}% Calmar={c/abs(dd):.1f}  [{ys}]")

    print("\n=== where REFINED HURTS vs baseline (refined - baseline) ===")
    diff = A.ref - A.base
    print("  by YEAR:")
    for y, g in diff.groupby(diff.index.year):
        rb = (1 + A.ref[A.ref.index.year == y]).prod() - 1; bb = (1 + A.base[A.base.index.year == y]).prod() - 1
        flag = "  <-- HURTS" if rb < bb else ""
        print(f"    {y}: refined {rb*100:+.0f}% vs baseline {bb*100:+.0f}%  (diff {(rb-bb)*100:+.0f}%){flag}")
    mo = pd.concat([(1 + A.ref).resample("ME").prod() - 1, (1 + A.base).resample("ME").prod() - 1], axis=1)
    mo.columns = ["ref", "base"]; mo["d"] = mo.ref - mo.base
    hurt_mo = mo[mo.d < -0.005].sort_values("d")
    print(f"  MONTHS where refined hurts (>0.5%): {len(hurt_mo)}/{len(mo)}  worst: " +
          ", ".join(f"{i.strftime('%Y-%m')}({r.d*100:+.0f}%)" for i, r in hurt_mo.head(6).iterrows()))
    wk = pd.concat([(1 + A.ref).resample("W").prod() - 1, (1 + A.base).resample("W").prod() - 1], axis=1)
    wk.columns = ["ref", "base"]; wk["d"] = wk.ref - wk.base
    print(f"  WEEKS where refined hurts: {int((wk.d<-0.002).sum())}/{len(wk)} ({(wk.d<-0.002).mean()*100:.0f}%)   "
          f"weeks it helps: {int((wk.d>0.002).sum())}/{len(wk)} ({(wk.d>0.002).mean()*100:.0f}%)")
    print(f"  net: refined beats baseline in {int((mo.d>0).sum())}/{len(mo)} months")


if __name__ == "__main__":
    main()
