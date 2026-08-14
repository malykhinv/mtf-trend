"""Calmar-raising RISK OVERLAY -> the one lever that can move the +150% frontier.

Leverage is Calmar-invariant, so the ONLY way to earn more at a given drawdown is to
raise the book's UNLEVERED Calmar (CAGR / |maxDD|). A causal risk overlay changes the
TIMING of exposure (not the average), so any Calmar gain is genuine timing value:

  A. VOL-TARGET: scale_t = c / trailing_vol_{t-1}, c set so AVERAGE scale = 1 (pure
     timing, same mean exposure) -> de-risk into turbulence, re-risk into calm.
  B. DD-CONTROL: cut exposure when equity is in drawdown beyond a threshold, restore on
     recovery (causal, equity known through t-1).
  C. BOTH.

Test at a FIXED DRAWDOWN BUDGET: lever each book to maxDD = -38% (the 3x reference point)
and compare CAGR + year-uniformity. Overlay wins only if it delivers MORE uniform CAGR at
the same drawdown. Shuffle control: permute the scale sequence (same values, wrong timing)
-> must NOT help. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.calmar_overlay
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.policy import PRIMARY


def stats(d):
    d = d.dropna()
    eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    mo = d.resample("ME").sum()
    return dict(cagr=cagr, sh=sh, dd=dd, calmar=calmar, yr=yr, minyr=min(yr.values()),
                mo=float((mo > 0).mean()), worst_mo=float(mo.min()))


def line(name, d):
    s = stats(d)
    ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
    print(f"  {name:24s} CAGR={s['cagr']*100:+4.0f}% Sh={s['sh']:+.2f} maxDD={s['dd']*100:+4.0f}% "
          f"Calmar={s['calmar']:.2f} +mo={s['mo']*100:2.0f}% minYr={s['minyr']*100:+.0f}%  [{ys}]")
    return s


def vol_target(d, lb=20):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1)
    scale = 1.0 / v.replace(0, np.nan)
    scale = (scale / scale.mean()).clip(upper=3.0)          # avg exposure = 1 (pure timing), cap 3x
    return (scale.fillna(1.0) * d), scale.fillna(1.0)


def dd_control(d, thresh=0.05, cut=0.5, lb_recover=True):
    """Cut exposure to `cut` while equity is > thresh below its running peak (causal)."""
    d = d.copy(); eq = (1 + d).cumprod(); peak = eq.cummax()
    in_dd = ((eq / peak - 1) < -thresh).shift(1).fillna(False)
    scale = pd.Series(np.where(in_dd, cut, 1.0), index=d.index)
    return d * scale, scale


def lever_to_dd(d, target=-0.38):
    d = d.dropna()
    best = 1.0
    for L in np.arange(0.5, 15.01, 0.1):
        eq = (1 + L * d).cumprod(); dd = (eq / eq.cummax() - 1).min()
        if dd <= target:
            best = L; break
        best = L
    return best


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)
    j = pd.concat([prl.rename("p"), bo.rename("b")], axis=1).dropna()
    prl, bo = j["p"], j["b"]
    sp, sb = prl.std(), bo.std(); wvp = (1 / sp) / (1 / sp + 1 / sb)
    books = {"50/50": 0.5 * prl + 0.5 * bo, "vol-parity": wvp * prl + (1 - wvp) * bo}

    rng = np.random.default_rng(0)
    for bname, base in books.items():
        print(f"\n=== {bname} book: base vs risk overlays (unlevered Calmar) ===")
        b0 = line("base", base)
        vt, sc_vt = vol_target(base)
        line("vol-target", vt)
        dc, _ = dd_control(base)
        line("dd-control", dc)
        both, _ = dd_control(vt)
        line("vol-target + dd-ctrl", both)
        # shuffle control: same scale values, permuted timing
        sc_sh = pd.Series(rng.permutation(sc_vt.values), index=sc_vt.index)
        line("vol-target SHUFFLED", (sc_sh * base))

        # frontier test: lever each to maxDD = -38%, compare CAGR + uniformity
        print(f"  -- levered to maxDD -38% (frontier-lift test) --")
        for nm, d in (("base", base), ("vol-target", vt), ("dd-control", dc), ("vt+dd", both)):
            L = lever_to_dd(d, -0.38); s = stats(L * d)
            ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
            ratio = (max(s["yr"].values()) / s["minyr"]) if s["minyr"] > 0 else float("inf")
            print(f"     {nm:20s} L={L:4.1f}x CAGR={s['cagr']*100:+4.0f}% minYr={s['minyr']*100:+4.0f}% "
                  f"ratio={ratio:>4.1f}x maxDD={s['dd']*100:+.0f}% worstMo={s['worst_mo']*100:+.0f}%  [{ys}]")

        # what does the FULL dream (+150% EVERY year) cost on base vs overlay?
        print(f"  -- lever to worst-year >= +150% (DD cost of the dream) --")
        for nm, d in (("base", base), ("vt+dd", both)):
            L = 1.0
            for LL in np.arange(1.0, 20.01, 0.1):
                yr = {y: (1 + g).prod() - 1 for y, g in (LL * d).dropna().groupby((LL * d).dropna().index.year)}
                if min(yr.values()) >= 1.50:
                    L = LL; break
                L = LL
            s = stats(L * d)
            ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
            print(f"     {nm:20s} L={L:4.1f}x minYr={s['minyr']*100:+4.0f}% maxDD={s['dd']*100:+.0f}% "
                  f"worstMo={s['worst_mo']*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
