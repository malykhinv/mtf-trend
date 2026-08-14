"""Minimize DRAWDOWN / maximize Calmar -> per-year maxDD <= 15% (user objective 2026-08-14).

Leverage scales return and drawdown together, so the target "+150-200%/yr with each year's
drawdown <= 15%" reduces to a single unlevered quantity: CALMAR ~ 10 (150%/15%). We are at
~3.3. This study attacks drawdown with the untapped levers:

  1. MORE quasi-independent sleeves: PRL at horizons H=5/15/30 + Donchian breakout at L=10/20/40
     -> diversification cuts portfolio DD.
  2. NET-BETA-HEDGE the combined book (it carries residual long-beta from the breakout sleeve;
     market crashes ARE the drawdowns) -> rolling causal beta to market, subtract.
  3. RISK-PARITY combine + causal VOL-TARGET overlay.

Scoreboard = unlevered Calmar, overall maxDD, and WORST per-year maxDD; then the leverage that
hits +150% worst-year and the resulting worst per-year DD; and the return reachable at per-year
DD <= 15%. IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.min_drawdown
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.overlap import overlapping
from anomaly_science.strategy.prl.research.policy import PRIMARY


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def donchian(close, ret, um, L, p):
    hi = _roll(close, L, "max"); lo = _roll(close, L // 2, "min")
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    sig[close >= hi.shift(1)] = 1.0; sig[close <= lo.shift(1)] = -1.0
    inl = (sig.ffill() == 1.0) & um
    n = inl.sum(axis=1); w = inl.div(n.where(n > 0), axis=0).fillna(0.0)
    breadth = (inl.sum(axis=1) / um.sum(axis=1)).clip(0, 1)
    gross = w.mul(breadth, axis=0)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) / 1e4
    return (gross.shift(1) * ret).sum(axis=1) - gross.diff().abs().sum(axis=1) * side


def per_year_dd(d):
    d = d.dropna(); out = {}
    for y, g in d.groupby(d.index.year):
        eq = (1 + g).cumprod(); out[int(y)] = float((eq / eq.cummax() - 1).min())
    return out


def summ(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    pyd = per_year_dd(d)
    return dict(cagr=cagr, sh=sh, dd=dd, calmar=cagr / abs(dd) if dd < 0 else np.nan,
                yr=yr, minyr=min(yr.values()), pyd=pyd, worst_pyd=min(pyd.values()))


def line(name, d):
    s = summ(d)
    pyds = " ".join(f"{y}:{v*100:.0f}%" for y, v in s["pyd"].items())
    print(f"  {name:26s} CAGR={s['cagr']*100:+4.0f}% Sh={s['sh']:+.2f} maxDD={s['dd']*100:+4.0f}% "
          f"Calmar={s['calmar']:4.1f} worstYrDD={s['worst_pyd']*100:+.0f}%  [DDbyYr {pyds}]")
    return s


def vol_target(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1)
    sc = (1.0 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def beta_hedge(d, mret):
    d, mret = d.align(mret, join="inner")
    beta = (d.rolling(60, min_periods=30).cov(mret) / mret.rolling(60, min_periods=30).var()).shift(1).fillna(0).clip(-3, 3)
    return d - beta * mret


def rp(series):
    al = pd.concat(series, axis=1).dropna(); inv = 1 / al.std(); w = inv / inv.sum()
    return (al * w).sum(axis=1)


def lever_to_minyr(d, target=1.50):
    for L in np.arange(1.0, 30.01, 0.1):
        yr = {y: (1 + g).prod() - 1 for y, g in (L * d).dropna().groupby((L * d).dropna().index.year)}
        if min(yr.values()) >= target:
            return L
    return 30.0


def lever_to_pyd(d, target=-0.15):
    L = 1.0
    for LL in np.arange(0.5, 30.01, 0.1):
        if min(per_year_dd(LL * d).values()) <= target:
            return LL
        L = LL
    return L


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    mret = ret.where(um).mean(axis=1)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)

    print("building sleeves (PRL H=5/15/30, Donchian L=10/20/40) ...")
    prl = {H: overlapping(close, ret, um, score, p, H=H, hyst=0.5) for H in (5, 15, 30)}
    don = {L: donchian(close, ret, um, L, p) for L in (10, 20, 40)}

    print("\n=== individual sleeves ===")
    for H, d in prl.items():
        line(f"PRL H={H}", d)
    for L, d in don.items():
        line(f"Donchian L={L}", d)

    prl_rp = rp(list(prl.values()))
    don_rp = rp(list(don.values()))
    print("\n=== sleeve baskets ===")
    line("PRL risk-parity(5/15/30)", prl_rp)
    line("Donchian rp(10/20/40)", don_rp)

    # PER-SLEEVE overlay first (fix each sleeve's own DD, esp. the breakout -50% DD), then combine
    print("\n=== per-sleeve vol-target overlay (fix each sleeve's DD before combining) ===")
    prl_ov = {H: vol_target(d) for H, d in prl.items()}
    don_ov = {L: vol_target(d) for L, d in don.items()}
    for L, d in don_ov.items():
        line(f"Donchian L={L} +VT", d)
    don_ov_rp = rp(list(don_ov.values()))
    line("Donchian rp +VT", don_ov_rp)

    # combined constructions, each then net-beta-hedged (partial sweep) and vol-target overlaid
    print("\n=== combined book: per-sleeve-overlaid, partial beta-hedge sweep, final overlay ===")
    raw = rp(list(prl_ov.values()) + list(don_ov.values()))
    best = None
    for hf in (0.0, 0.5, 1.0):
        d0, mr = raw.align(mret, join="inner")              # partial hedge: raw - hf*beta*mret
        beta = (d0.rolling(60, min_periods=30).cov(mr) / mr.rolling(60, min_periods=30).var()).shift(1).fillna(0).clip(-3, 3)
        bh = d0 - hf * beta * mr
        ov = vol_target(bh)
        s = line(f"all-6 +VT, hedge={hf:.1f}, +VT", ov)
        if best is None or s["calmar"] > best[2]["calmar"]:
            best = (f"all-6 +VT hedge={hf:.1f} +VT", ov, s)

    # scoreboard on the best book
    nm, d, s = best
    print(f"\n=== SCOREBOARD (best unlevered book: {nm}) ===")
    print(f"  unlevered: Calmar {s['calmar']:.1f}  maxDD {s['dd']*100:+.0f}%  worst-year DD {s['worst_pyd']*100:+.0f}%")
    L1 = lever_to_minyr(d, 1.50); s1 = summ(L1 * d)
    ys1 = " ".join(f"{v*100:+.0f}%" for v in s1["yr"].values())
    print(f"  lever to +150% worst-year: L={L1:.1f}x -> worst-year DD {s1['worst_pyd']*100:+.0f}%  [{ys1}]")
    L2 = lever_to_pyd(d, -0.15); s2 = summ(L2 * d)
    ys2 = " ".join(f"{v*100:+.0f}%" for v in s2["yr"].values())
    print(f"  lever to per-year DD=15%:  L={L2:.1f}x -> worst-year return {s2['minyr']*100:+.0f}%  CAGR {s2['cagr']*100:+.0f}%  [{ys2}]")
    print(f"  (target: worst-year return +150% AT per-year DD <=15% needs Calmar ~10; have {s['calmar']:.1f})")


if __name__ == "__main__":
    main()
