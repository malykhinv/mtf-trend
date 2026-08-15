"""Final regime-gated book: fix PRL (#2) + tame sweep concentration (#3) + full stats dashboard.

Builds the regime-gated book (breakout->bull, sweep->bull/side, breakdown->bear, cash in vol-chop)
with TAMED sweep concurrency (#3), tests dropping PRL (#2), and reports the full trader dashboard by
REGIME and by YEAR: trade count, day count, % positive days/weeks/months, win-rate, PnL, top-x%-trade-
removal to go negative (fragility), day streaks, and the biggest wins/losses. IS 2020-2025, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.final_dashboard --n 150
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
from anomaly_science.strategy.prl.research.regime_modes import stabilized_regime

COST = 6 / 1e4


def build_trades(n):
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
    # daily BTC regime (stabilized)
    dclose = close.resample("1D").last()["BTCUSDT"] if "BTCUSDT" in cols else close.resample("1D").last().median(axis=1)
    tr_reg, _ = stabilized_regime(dclose)
    reg_by_day = tr_reg.copy(); reg_by_day.index = pd.to_datetime(reg_by_day.index).tz_localize(None).normalize()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy(); bk = brk.to_numpy() if hasattr(brk, "to_numpy") else brk

    def reg_at(t):
        d = pd.Timestamp(idx[t]).tz_localize(None).normalize()
        return reg_by_day.get(d, "side")

    sweep, bd, bo = [], [], []
    for si in range(len(cols)):
        # sweep-fade (закол)
        for i in np.where(pk[:, si])[0]:
            if i + 4 + 12 + 48 >= len(idx) or not (datr[i, si] > 0):
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
            sweep.append((idx[start - 1], idx[xb], bn, (stop - entry) / entry, reg_at(j)))
        # breakdown-short
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0):
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
            bd.append((idx[e], idx[xb], bn, risk, reg_at(e)))
        # breakout-long (RAW)
        for i in np.where(bk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]):
                continue
            entry = C[dj, si]; expx = C[dj + 48, si]
            raw = expx / entry - 1.0 - COST
            bo.append((idx[dj], idx[dj + 48], raw, 0.05, reg_at(dj)))
    def mk(rows, cols_):
        d = pd.DataFrame(rows, columns=["entry_t", "exit_t", "pnl", "risk", "regime"])
        d["setup"] = cols_; return d
    return mk(sweep, "sweep"), mk(bd, "breakdown"), mk(bo, "breakout"), idx


def daily_sleeve(tr, hidx, risk_per=0.005, maxconc=8, dcap=0.03):
    hpos = {t: k for k, t in enumerate(hidx)}
    pnl = np.zeros(len(hidx)); open_ex = []
    day_risk = {}
    for _, r in tr.sort_values("entry_t").iterrows():
        je, xe = hpos[r.entry_t], hpos[r.exit_t]
        open_ex = [e for e in open_ex if e > je]
        if len(open_ex) >= maxconc:
            continue
        dkey = pd.Timestamp(r.entry_t).normalize()
        w = risk_per / max(r.risk, 1e-4)
        if day_risk.get(dkey, 0) + risk_per > dcap:            # daily risk cap (#3 tame concentration)
            continue
        day_risk[dkey] = day_risk.get(dkey, 0) + risk_per
        open_ex.append(xe); pnl[xe] += w * r.pnl
    d = pd.Series(pnl, index=hidx).resample("1D").sum()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


def dstats(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / max(len(d), 1)) - 1; sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    wk = d.resample("W").sum(); mo = d.resample("ME").sum()
    return dict(cagr=cagr, dd=dd, sh=sh, pday=float((d > 0).mean()), pwk=float((wk > 0).mean()),
                pmo=float((mo > 0).mean()), n=len(d))


def streaks(d):
    s = (d.dropna() > 0).astype(int).values
    if len(s) == 0:
        return 0, 0
    runs = []; cur = s[0]; ln = 1
    for x in s[1:]:
        if x == cur:
            ln += 1
        else:
            runs.append((cur, ln)); cur = x; ln = 1
    runs.append((cur, ln))
    w = max([n for c, n in runs if c == 1], default=0); l = max([n for c, n in runs if c == 0], default=0)
    return w, l


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print("building trades ...")
    sw, bd, bo, hidx = build_trades(args.n)
    # regime gates
    sw_g = sw[sw.regime.isin(["bull", "side"])]
    bd_g = bd[bd.regime == "bear"]
    bo_g = bo[bo.regime == "bull"]
    alltr = pd.concat([sw_g, bd_g, bo_g], ignore_index=True)
    alltr["year"] = pd.to_datetime(alltr["entry_t"]).dt.tz_localize(None).dt.year

    print("\n=== TRADE-LEVEL stats (regime-gated, all setups pooled) ===")
    def trade_report(g, label):
        r = g.pnl.values; r = r[np.isfinite(r)]
        if len(r) == 0:
            print(f"  {label}: no trades"); return
        w = r[r > 0]; l = r[r <= 0]
        srt = np.sort(r); cum = r.sum()
        # how many top trades to remove to go negative
        k = 0; tot = cum
        for x in np.sort(r)[::-1]:
            tot -= x; k += 1
            if tot <= 0:
                break
        print(f"  {label:16s} n={len(r):6d} win={np.mean(r>0):.0%} avgW={w.mean()*100 if len(w) else 0:+.2f}% "
              f"avgL={l.mean()*100 if len(l) else 0:+.2f}% exp={r.mean()*100:+.3f}% "
              f"top{k}({k/len(r)*100:.1f}%)->neg bigW={r.max()*100:+.0f}% bigL={r.min()*100:+.0f}%")
    trade_report(alltr, "ALL")
    for s in ["breakout", "sweep", "breakdown"]:
        trade_report(alltr[alltr.setup == s], s)
    print("  by regime:")
    for rg in ["bull", "side", "bear"]:
        trade_report(alltr[alltr.regime == rg], f"  {rg}")
    print("  by year:")
    for y, g in alltr.groupby("year"):
        trade_report(g, f"  {y}")

    # build daily book
    print("\nbuilding daily book ...")
    sweep = daily_sleeve(sw_g, hidx); bdown = daily_sleeve(bd_g, hidx)
    bout = daily_sleeve(bo_g, hidx, risk_per=0.01, maxconc=15, dcap=0.06)
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um, score = m["close"], m["umask"], m["score"]
    prl, _, _ = run_book(close, close.pct_change(fill_method=None), um, score, p, step=15, hyst=0.5)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, sweep, bdown, bout = map(norm, (prl, sweep, bdown, bout))

    def vt(d, lb=20, cap=3.0):
        v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0); return d * sc

    def book(cols):
        A = pd.concat(cols, axis=1).dropna(); inv = 1 / A.std(); return vt((A * (inv / inv.sum())).sum(axis=1))

    with_prl = book([prl.rename("p"), bout.rename("bo"), sweep.rename("sw"), bdown.rename("bd")])
    no_prl = book([bout.rename("bo"), sweep.rename("sw"), bdown.rename("bd")])

    print("\n=== #2 PRL: book WITH vs WITHOUT PRL ===")
    for nm, d in [("with PRL", with_prl), ("no PRL (3 setups)", no_prl)]:
        s = dstats(d); w, l = streaks(d)
        yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
        ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
        print(f"  {nm:18s} CAGR={s['cagr']*100:+4.0f}% Sh={s['sh']:+.2f} maxDD={s['dd']*100:+3.0f}% "
              f"+day={s['pday']*100:.0f}% +wk={s['pwk']*100:.0f}% +mo={s['pmo']*100:.0f}% Wstreak={w} Lstreak={l}  [{ys}]")

    book_d = with_prl
    print("\n=== DAILY book (WITH PRL, tamed) by YEAR ===")
    print("  " + "year".ljust(6) + "days".rjust(6) + "ret".rjust(7) + "+day".rjust(6) + "+wk".rjust(6) + "+mo".rjust(6) + "maxDD".rjust(7))
    for y, g in book_d.groupby(book_d.index.year):
        s = dstats(g); ret = (1 + g).prod() - 1
        print(f"  {str(y).ljust(6)}{s['n']:6d}{ret*100:+6.0f}%{s['pday']*100:5.0f}%{s['pwk']*100:5.0f}%{s['pmo']*100:5.0f}%{s['dd']*100:+6.0f}%")

    # by regime
    dclose = pn.pivot(panel, "close")["BTCUSDT"]; treg, _ = stabilized_regime(dclose)
    treg.index = pd.to_datetime(treg.index).tz_localize(None).normalize(); treg = treg.reindex(book_d.index).ffill()
    print("\n=== DAILY book (no-PRL) by REGIME ===")
    for rg in ["bull", "side", "bear"]:
        g = book_d[treg == rg]
        if len(g) < 10:
            continue
        s = dstats(g)
        print(f"  {rg:6s} days={s['n']:5d} annRet={g.mean()*252*100:+4.0f}% +day={s['pday']*100:.0f}% +wk={s['pwk']*100:.0f}% maxDD={s['dd']*100:+.0f}%")

    # ===== DOWNSIDE / LIQUIDATION / EQUITY (with-PRL final book) =====
    d = with_prl.dropna()
    eq = (1 + d).cumprod(); dollars = 1000 * eq / eq.iloc[0]
    ddser = eq / eq.cummax() - 1
    # drawdown duration
    underwater = (ddser < 0).astype(int); seg = (underwater != underwater.shift(1)).cumsum()
    maxdur = max([len(g) for k, g in underwater.groupby(seg) if g.iloc[0] == 1], default=0)
    print("\n=== DOWNSIDE / LIQUIDATION (final with-PRL book, unlevered) ===")
    print(f"  $1000 -> ${dollars.iloc[-1]:,.0f} over {len(d)} days ({len(d)/252:.1f}y)")
    print(f"  worst DAY {d.min()*100:+.2f}%   worst WEEK {d.resample('W').sum().min()*100:+.2f}%   "
          f"worst MONTH {d.resample('ME').sum().min()*100:+.1f}%")
    print(f"  maxDD {ddser.min()*100:+.1f}%   longest underwater {maxdur} days   worst 1% of days avg {np.percentile(d,1)*100:+.2f}%")
    # is the profit concentrated in time? (the "what if the winners are at the end" test)
    half = len(d) // 2
    h1 = (1 + d.iloc[:half]).prod() - 1; h2 = (1 + d.iloc[half:]).prod() - 1
    mo = d.resample("ME").sum(); best_mo = mo.idxmax()
    no_best_mo = (1 + d[d.index.to_period("M") != best_mo.to_period("M")]).prod() - 1
    print(f"  first half {h1*100:+.0f}%  second half {h2*100:+.0f}%  (both>0 => not an end-spike)")
    print(f"  best month {mo.max()*100:+.0f}% ({best_mo.date()}); WHOLE book minus best month = {no_best_mo*100:+.0f}%")
    print(f"  positive years: {int(sum((1+g).prod()>1 for _,g in d.groupby(d.index.year)))}/6")
    # liquidation headroom: leverage where maxDD hits -50% / -90%
    for tgt in (-0.50, -0.90):
        L = None
        for LL in np.arange(1, 20.1, 0.5):
            e = (1 + LL * d).cumprod()
            if (e / e.cummax() - 1).min() <= tgt:
                L = LL; break
        print(f"  leverage to reach maxDD {tgt*100:.0f}%: {('%.1fx' % L) if L else '>20x'}  (unlevered maxDD {ddser.min()*100:.0f}% -> huge liquidation headroom)")
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6.5), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
        ax1.plot(dollars.index, dollars.values, color="#1f77b4", lw=1.4); ax1.set_yscale("log")
        ax1.set_ylabel("Equity $ (log)"); ax1.set_title("Final regime-gated book (with PRL, tamed) — $1000, unlevered, IS 2020-2025")
        ax1.grid(True, alpha=0.3, which="both")
        ax2.fill_between(ddser.index, ddser.values * 100, 0, color="#d62728", alpha=0.5)
        ax2.set_ylabel("Drawdown %"); ax2.grid(True, alpha=0.3)
        out = ".output/results/prl_coarse/final_equity.png"; fig.tight_layout(); fig.savefig(out, dpi=110)
        print(f"  chart -> {out}")
    except Exception as e:
        print("chart skipped:", e)


if __name__ == "__main__":
    main()
