"""Hunt a HIGH-WEEKLY uncorrelated THIRD sleeve -> the direct lever to 80% weekly.

The PRL + runner/regime-breakout combo caps at ~64% weeks because the breakout sleeve
is directional (~55% weekly). To push toward 80%+ positive weeks we need a third source
that wins OFTEN-and-SMALL and is uncorrelated with both PRL (short-beta market-neutral)
and the long-beta breakout sleeve. Two natural candidates:

  A. FUNDING-CARRY: dollar-neutral book short high-funding / long low(neg)-funding coins;
     P&L marked on (price_ret - funding) so the funding accrual IS the return. Carry
     accrues every period -> structurally high-weekly, orthogonal (a different return
     source, not price prediction). Funding now full-year for all symbols.
  B. XS SHORT-HORIZON MEAN-REVERSION: long recent losers / short recent winners
     (residualized), rank-weight dollar-neutral, rolling -- high hit-rate, opposite sign
     to momentum/breakout.

Each built via the SAME tested overlapping() rank-weighted dollar-neutral harness. Report
weekly %positive (KEY), Sharpe, by-year, and correlation with PRL and the breakout sleeve;
shuffle controls (permute funding / permute MR score). Best high-weekly low-corr candidate
-> TRIPLE combo vol-parity (PRL + breakout + third). IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.third_sleeve
"""

from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve, _stats
from anomaly_science.strategy.prl.research.overlap import overlapping
from anomaly_science.strategy.prl.research.policy import PRIMARY

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"


def _load_funding_daily(cols, idx):
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f)
            tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
            fu[s] = df.set_index(tt)["funding_rate"].resample("1D").sum()
    fund = pd.DataFrame(fu).reindex(columns=cols)
    fund.index = fund.index.tz_convert("UTC") if fund.index.tz else fund.index.tz_localize("UTC")
    return fund.reindex(idx).fillna(0.0)


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    idx, cols = close.index, close.columns

    # reference sleeves
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    print("=== reference sleeves ===")
    _stats(prl, "PRL market-neutral")
    _stats(bo, "Breakout-long trend")

    def corr(a, b):
        a, b = a.align(b, join="inner"); return a.corr(b)

    # ---- A. FUNDING-CARRY ----
    fund = _load_funding_daily(cols, idx)
    print(f"\nfunding coverage: {int((fund.abs().sum() > 0).sum())}/{len(cols)} symbols  "
          f"mean|f|={fund[fund != 0].abs().stack().mean()*1e4:.2f}bps/day")
    ret_carry = ret - fund                                   # short high-funding receives carry
    print("\n=== A. FUNDING-CARRY (dollar-neutral, marked on price-funding) ===")
    carry_books = {}
    for H in (3, 5, 10, 15):
        sc = (-fund.rolling(3, min_periods=1).mean()).shift(1)   # causal: short recently-high funding
        b = overlapping(close, ret_carry, um, sc, p, H=H, hyst=0.5)
        carry_books[H] = b
        s = _stats(b, f"carry H={H}")
        print(f"      corr(PRL)={corr(b, prl):+.2f} corr(BO)={corr(b, bo):+.2f}")
    # shuffle control: permute funding across symbols each day -> carry edge must vanish
    rng = np.random.default_rng(0)
    fsh = pd.DataFrame(rng.permuted(fund.to_numpy(), axis=1), index=idx, columns=cols)
    sc_sh = (-fsh.rolling(3, min_periods=1).mean()).shift(1)
    b_sh = overlapping(close, ret - fsh, um, sc_sh, p, H=5, hyst=0.5)
    _stats(b_sh, "carry SHUFFLED H=5")

    # ---- B. XS SHORT-HORIZON MEAN-REVERSION ----
    print("\n=== B. XS MEAN-REVERSION (long losers / short winners, residualized) ===")
    mr_books = {}
    for L in (1, 2, 3, 5):
        r = close / close.shift(L) - 1
        resid = r.sub(r.where(um).mean(axis=1), axis=0)          # market-residual
        sc = (-resid).shift(1)                                    # long losers
        b = overlapping(close, ret, um, sc, p, H=max(L, 3), hyst=0.5)
        mr_books[L] = b
        _stats(b, f"MR L={L} H={max(L,3)}")
        print(f"      corr(PRL)={corr(b, prl):+.2f} corr(BO)={corr(b, bo):+.2f}")
    r = close / close.shift(2) - 1
    resid = r.sub(r.where(um).mean(axis=1), axis=0)
    sc_sh = pd.DataFrame(rng.permuted((-resid).shift(1).to_numpy(), axis=1), index=idx, columns=cols)
    _stats(overlapping(close, ret, um, sc_sh, p, H=3, hyst=0.5), "MR SHUFFLED L=2")

    # ---- best candidate -> TRIPLE combo ----
    def vp(series_list):
        al = pd.concat(series_list, axis=1).dropna()
        inv = 1 / al.std()
        w = inv / inv.sum()
        return (al * w).sum(axis=1)

    print("\n=== TRIPLE combo vol-parity (PRL + breakout + third) ===")
    # pick best-weekly carry and best-weekly MR
    best_carry = max(carry_books.items(), key=lambda kv: (kv[1].resample("W").sum() > 0).mean())
    best_mr = max(mr_books.items(), key=lambda kv: (kv[1].resample("W").sum() > 0).mean())
    print(f"  (best carry H={best_carry[0]}, best MR L={best_mr[0]})")
    _stats(vp([prl, bo]), "PRL+BO (2-sleeve ref)")
    _stats(vp([prl, bo, best_carry[1]]), "PRL+BO+CARRY")
    _stats(vp([prl, bo, best_mr[1]]), "PRL+BO+MR")
    _stats(vp([prl, bo, best_carry[1], best_mr[1]]), "PRL+BO+CARRY+MR")
    _stats(vp([prl, best_carry[1]]), "PRL+CARRY (2-sleeve)")
    _stats(vp([prl, best_mr[1]]), "PRL+MR (2-sleeve)")


if __name__ == "__main__":
    main()
