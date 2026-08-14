"""Regime-weighted, runner-SIZED breakout sleeve + PRL -> the combined book, deep+wide.

The wide grid settled it: breakout DIRECTION is unpredictable (win-AUC ~0.50) but runner
SIZE is real (AUC ~0.61) and dominated by market/regime. So we stop filtering direction
and instead (1) SIZE each breakout by its walk-forward runner-score, and (2) scale the
whole sleeve's gross exposure by a causal REGIME factor (breadth-rank). Hold-to-time
(HH=48h, no stops -> never cut the fat-tail runners). Then combine vol-parity with the
daily PRL market-neutral sleeve (net-short-beta) -- the long-beta breakout sleeve is the
regime complement.

Deep+wide: 4 sizing variants (equal / runner / regime / runner x regime) standalone and
combined, per-year, weekly %positive, Sharpe, maxDD, corr, and SHUFFLE controls (permute
runner-score and regime -> the sizing edge must beat shuffle). IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.regime_runner_book --n 150
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"
LVL, T, HH = 20, 4, 48
SIDE = (PRIMARY.fee_bps + PRIMARY.half_spread_bps + PRIMARY.base_slippage_bps) / 1e4


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def _stats(d, name):
    d = d.dropna()
    if len(d) < 30:
        print(f"  {name:26s} (too short)"); return d
    wk = d.resample("W").sum(); mo = d.resample("ME").sum()
    eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {y: (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
    print(f"  {name:26s} CAGR={cagr*100:+4.0f}% Sh={sh:+.2f} +wk={float((wk>0).mean())*100:2.0f}% "
          f"+mo={float((mo>0).mean())*100:2.0f}% mDD={dd*100:+3.0f}%  [{ys}]")
    return d


def build_breakout_events(n):
    """Return (idx, cols, close, ret hourly, events df with runner OOF, breadth-rank series)."""
    print(f"loading 1h (top-{n}) ...")
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv, ntr, tbq = (px(c) for c in ("close", "high", "low", "quote_volume", "number_of_trades", "taker_buy_quote_volume"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None)
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    eth = close["ETHUSDT"] if "ETHUSDT" in cols else btc
    btc_fwd = (btc.shift(-(T + 24)) / btc.shift(-T) - 1.0).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()

    # focused runner feature set (the wide-grid winners)
    F = {"ret168": close / close.shift(168) - 1, "ret24": close / close.shift(24) - 1,
         "ema480": close / close.ewm(span=480, min_periods=240).mean() - 1,
         "ema168": close / close.ewm(span=168, min_periods=84).mean() - 1,
         "dist_atl": close / close.cummin() - 1, "dist_ath": close / close.cummax() - 1,
         "vsurge168": qv / (_roll(qv, 168, "mean") + 1e-9),
         "vol_accel": _roll(qv, 6, "mean") / (_roll(qv, 72, "mean") + 1e-9),
         "avg_trade": qv / ntr.replace(0, np.nan),
         "taker_ratio": tbq / (qv + 1e-9), "ext_above": close / level - 1,
         "candle_atr": (high - low) / (atr + 1e-9)}
    breadth = above.mean(axis=1); mvol = ret.mean(axis=1).rolling(24).std()
    for nm, ser in (("breadth", breadth), ("mvol", mvol), ("btc168", btc / btc.shift(168) - 1),
                    ("btc24", btc / btc.shift(24) - 1), ("eth24", eth / eth.shift(24) - 1)):
        F[nm] = pd.DataFrame(np.repeat(ser.to_numpy()[:, None], len(cols), axis=1), index=idx, columns=cols)
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f); tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(tt)["funding_rate"].last()
    if fu:
        fund = pd.DataFrame(fu).reindex(idx).ffill().reindex(columns=cols)
        F["fnow"] = fund; F["fchg3d"] = fund - fund.shift(72)
    feats = list(F.keys())
    FM = {k: v.reindex(index=idx, columns=cols).to_numpy() for k, v in F.items()}
    Cn, Ln, An = close.to_numpy(), level.to_numpy(), atr.to_numpy()
    bmask = brk.to_numpy()
    rows = []
    for si in range(len(cols)):
        for i in np.where(bmask[:, si])[0]:
            dj, fj = i + T, i + T + 24
            if fj >= len(idx) or not (Cn[dj, si] > 0) or not (An[i, si] > 0):
                continue
            if not (Cn[dj, si] >= Ln[i, si]):     # confirmed HOLD only (long sleeve)
                continue
            rel = (Cn[fj, si] / Cn[dj, si] - 1.0) - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            row = {"si": si, "dj": dj, "date": idx[i], "runner": int(rel > 0.05)}
            for k in feats:
                row[k] = FM[k][i, si]
            rows.append(row)
    ev = pd.DataFrame(rows)
    print(f"confirmed-hold breakout events: {len(ev)}  runner-rate {ev.runner.mean():.3f}")

    # walk-forward OOF runner-probability
    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn = pd.to_datetime(ev["date"]).dt.tz_localize(None)
    oofp = pd.Series(np.nan, index=ev.index)
    for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
        tri = ev.index[dn.isin(tr_d)]; tei = ev.index[dn.isin(te_d)]
        if len(tri) < 200 or ev.loc[tri, "runner"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
        cb.fit(ev.loc[tri, feats], ev.loc[tri, "runner"]); oofp.loc[tei] = cb.predict_proba(ev.loc[tei, feats])[:, 1]
    ev["rscore"] = oofp.fillna(oofp.mean())
    mm = oofp.notna()
    print(f"runner OOF AUC = {roc_auc_score(ev.loc[mm,'runner'], oofp[mm]):.3f}  (rscore range {ev.rscore.min():.2f}-{ev.rscore.max():.2f})")

    # causal breadth-rank regime factor (expanding, in [0,1])
    breadth_rank = breadth.expanding(min_periods=200).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False).shift(1)
    return idx, cols, close, ret, atr, ev, breadth_rank.reindex(idx)


def sleeve_returns(idx, cols, ret, ev, breadth_rank, weight, gross):
    """Build a daily-marked long breakout book. weight: 'equal'|'runner'. gross: 'flat'|'regime'|shuffled arr."""
    Hold = np.zeros((len(idx), len(cols)))
    rs = ev["rscore"].to_numpy(); djs = ev["dj"].to_numpy(); sis = ev["si"].to_numpy()
    w_ev = rs if weight == "runner" else np.ones(len(ev)) if weight == "equal" else weight  # weight can be shuffled arr
    for k in range(len(ev)):
        a, b = djs[k] + 1, min(djs[k] + HH + 1, len(idx))
        Hold[a:b, sis[k]] += w_ev[k]
    W = pd.DataFrame(Hold, index=idx, columns=cols)
    Wrel = W.div(W.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)   # relative weights (runner tilt)
    if isinstance(gross, str):
        g = pd.Series(1.0, index=idx) if gross == "flat" else breadth_rank.fillna(0.5).clip(0, 1)
    else:
        g = pd.Series(gross, index=idx).clip(0, 1)                       # shuffled regime array
    P = Wrel.mul(g, axis=0)                                              # position book (sum<=gross, rest cash)
    gross_ret = (P.shift(1) * ret).sum(axis=1)
    turn = P.diff().abs().sum(axis=1)
    net = (gross_ret - turn * SIDE)
    return net.resample("1D").apply(lambda x: (1 + x).prod() - 1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    idx, cols, close, ret, atr, ev, breadth_rank = build_breakout_events(args.n)

    # sleeve variants
    print("\nbuilding breakout sleeve variants ...")
    variants = {
        "equal/flat":   sleeve_returns(idx, cols, ret, ev, breadth_rank, "equal", "flat"),
        "runner/flat":  sleeve_returns(idx, cols, ret, ev, breadth_rank, "runner", "flat"),
        "equal/regime": sleeve_returns(idx, cols, ret, ev, breadth_rank, "equal", "regime"),
        "runner/regime": sleeve_returns(idx, cols, ret, ev, breadth_rank, "runner", "regime"),
    }
    # shuffle controls: permute runner-score, permute regime
    rng = np.random.default_rng(0)
    ev_sh = ev.copy(); ev_sh["rscore"] = rng.permutation(ev["rscore"].values)
    variants["SHUF-runner/flat"] = sleeve_returns(idx, cols, ret, ev_sh, breadth_rank, "runner", "flat")
    g_sh = rng.permutation(breadth_rank.fillna(0.5).clip(0, 1).values)
    variants["equal/SHUF-regime"] = sleeve_returns(idx, cols, ret, ev, breadth_rank, "equal", g_sh)

    print("\n=== breakout sleeve standalone (long-beta, HH=48h hold-to-time) ===")
    for nm, s in variants.items():
        _stats(s, nm)

    # daily PRL market-neutral sleeve
    print("\nloading daily PRL sleeve ...")
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    dclose, dum = m["close"], m["umask"]; dret = dclose.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    dscore = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(dclose)
    prl, _, _ = run_book(dclose, dret, dum, dscore, p, step=15, hyst=0.5)

    _stats(prl, "PRL market-neutral")

    def vp(a, b):
        a, b = a.align(b, join="inner"); a, b = a.dropna(), b.dropna()
        a, b = a.align(b, join="inner")
        sa, sb = a.std(), b.std(); w = (1 / sa) / (1 / sa + 1 / sb)
        return w * a + (1 - w) * b

    print("\n=== combined vol-parity (PRL + breakout variant) ===")
    for nm in ("equal/flat", "runner/flat", "equal/regime", "runner/regime",
               "SHUF-runner/flat", "equal/SHUF-regime"):
        s = variants[nm]
        c = prl.align(s, join="inner")
        print(f"  corr(PRL, {nm}) = {c[0].corr(c[1]):+.2f}")
        _stats(vp(prl, s), f"PRL + {nm}")


if __name__ == "__main__":
    main()
