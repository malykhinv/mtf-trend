"""Harden the Calmar-overlay peak: parameter robustness + leverage cost/borrow stress.

Before spending the reserved OOS we must confirm (a) the vol-target Calmar gain is not a
single-parameter artifact -- it should hold across vol lookback / cap / dd-threshold / cut;
and (b) the levered book survives realistic LEVERAGE COSTS: borrow/funding on the (L-1)
notional plus the overlay's daily scaling turnover, which were NOT modeled in the headline.

Grid the overlay params -> Calmar distribution (robust if mostly >> base 1.12). Then at a
fixed leverage, sweep a daily borrow/funding rate on the levered notional -> CAGR/DD/minYr.
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.calmar_robust
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


def st(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    return cagr, dd, (cagr / abs(dd) if dd < 0 else np.nan), min(yr.values()), yr


def overlay(base, lb, cap, dd_th, dd_cut):
    v = base.rolling(lb, min_periods=lb // 2).std().shift(1)
    sc = (1.0 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    d = base * sc
    eq = (1 + d).cumprod(); peak = eq.cummax()
    in_dd = ((eq / peak - 1) < -dd_th).shift(1).fillna(False).astype(bool)
    sc2 = pd.Series(np.where(in_dd, dd_cut, 1.0), index=d.index)
    return d * sc2, sc * sc2


def lever_to_dd(d, target=-0.38):
    for L in np.arange(0.5, 20.01, 0.1):
        eq = (1 + L * d).cumprod()
        if (eq / eq.cummax() - 1).min() <= target:
            return L
    return 20.0


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
    base = 0.5 * j["p"] + 0.5 * j["b"]
    c0, dd0, cal0, _, _ = st(base)
    print(f"50/50 base: CAGR {c0*100:+.0f}% maxDD {dd0*100:+.0f}% Calmar {cal0:.2f}\n")

    print("=== overlay param robustness (Calmar across grid; base 1.12) ===")
    cals = []
    for lb in (10, 20, 40, 60):
        row = []
        for cap in (2.0, 3.0, 5.0):
            _, _, cal, _, _ = st(overlay(base, lb, cap, 0.05, 0.5)[0])
            row.append(cal); cals.append(cal)
        print(f"  lb={lb:>2}  " + "  ".join(f"cap{c:.0f}:{v:.2f}" for c, v in zip((2, 3, 5), row)))
    for dd_th, dd_cut in ((0.03, 0.5), (0.08, 0.5), (0.05, 0.3), (0.05, 0.7)):
        _, _, cal, _, _ = st(overlay(base, 20, 3.0, dd_th, dd_cut)[0])
        cals.append(cal)
    cals = np.array(cals)
    print(f"  -> Calmar over {len(cals)} configs: min {cals.min():.2f}  median {np.median(cals):.2f}  "
          f"max {cals.max():.2f}  (all > base {cal0:.2f}: {bool((cals > cal0).all())})")

    # canonical overlay
    ov, scale = overlay(base, 20, 3.0, 0.05, 0.5)
    turn = scale.diff().abs().fillna(0.0)
    print("\n=== leverage COST/BORROW stress (canonical vol-target+dd overlay) ===")
    print("  net_t = L*ov_t - (L-1)*borrow_daily - |dscale|*L*2bps   (perp market-neutral: net funding")
    print("  on the small L*imbalance; a dollar-neutral book has ~0 net so low bps is realistic)")
    for target_dd in (-0.38, -0.50):
        L = lever_to_dd(ov, target_dd)
        print(f"\n  -- levered to maxDD {target_dd*100:.0f}%  (L={L:.1f}x) --")
        for bday_bps in (0.0, 1.0, 2.0, 5.0):
            borrow = (L - 1) * (bday_bps / 1e4)                 # daily borrow drag on levered equity
            net = L * ov - borrow - turn * L * (2.0 / 1e4)      # levered ONCE; costs applied once
            c, dd, cal, minyr, yr = st(net)
            ys = " ".join(f"{v*100:+.0f}%" for v in yr.values())
            print(f"     borrow={bday_bps:>4.0f}bps/day  CAGR={c*100:+4.0f}% minYr={minyr*100:+4.0f}% "
                  f"maxDD={dd*100:+.0f}% Calmar={cal:.2f}  [{ys}]")


if __name__ == "__main__":
    main()
