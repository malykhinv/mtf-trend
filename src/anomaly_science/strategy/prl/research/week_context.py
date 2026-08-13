"""What distinguishes the book's NEGATIVE weeks from POSITIVE weeks? Is it market
context / BTC-ETH dynamics? Analyzed separately for the long and short legs (user).

Descriptive: contemporaneous market context (BTC/ETH weekly return & vol, ETH-BTC
spread, cross-sectional dispersion, breadth, market return) in positive vs negative
weeks, with per-year sign stability. Causal: can PRIOR-week context predict this
week's book sign -- walk-forward OOF AUC + shuffle (a real week-timing signal must
beat the shuffle). If yes, cutting exposure in predicted-bad weeks lifts +weeks.

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.week_context
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup
from dataclasses import replace


def _legs():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    idx = close.index
    longd = pd.Series(0.0, index=idx); shortd = pd.Series(0.0, index=idx); prev = pd.Series(dtype=float)
    for ri in range(_warmup(p), len(idx) - 16, 15):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 15:
            continue
        rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
        if len(prev):
            w = 0.5 * w + 0.5 * prev.reindex(w.index).fillna(0); w = w - w.mean(); w = w / w.abs().sum()
        wl = w[w > 0]; ws = w[w < 0]
        for dd in range(ri + 1, min(ri + 16, len(idx))):
            r = ret.iloc[dd]
            longd.iloc[dd] += float((wl * r.reindex(wl.index)).sum(skipna=True))
            shortd.iloc[dd] += float((ws * r.reindex(ws.index)).sum(skipna=True))
        prev = w
    mask = (longd.ne(0) | shortd.ne(0)).cumsum() > 0
    return longd[mask], shortd[mask], close, ret, um


def main():
    longd, shortd, close, ret, um = _legs()
    totald = longd + shortd
    # market context (daily)
    btc = close["BTCUSDT"].pct_change() if "BTCUSDT" in close else pd.Series(0.0, index=close.index)
    eth = close["ETHUSDT"].pct_change() if "ETHUSDT" in close else pd.Series(0.0, index=close.index)
    disp = ret.where(um).std(axis=1)
    breadth = (ret.where(um) > 0).sum(axis=1) / um.sum(axis=1)
    mret = ret.where(um).mean(axis=1)
    ctx_d = pd.DataFrame({
        "btc_ret": btc, "eth_ret": eth, "eth_minus_btc": eth - btc,
        "btc_vol20": btc.rolling(20).std(), "dispersion": disp, "breadth": breadth,
        "market_ret": mret, "btc_absret": btc.abs(),
    })
    W = "W"
    legs = {"TOTAL": totald, "LONG": longd, "SHORT": shortd}
    wk_legs = {k: v.resample(W).sum() for k, v in legs.items()}
    ctx_w = ctx_d.resample(W).mean()  # contemporaneous weekly context
    ctx_w["btc_ret"] = ctx_d["btc_ret"].resample(W).sum()
    ctx_w["eth_ret"] = ctx_d["eth_ret"].resample(W).sum()
    ctx_w["market_ret"] = ctx_d["market_ret"].resample(W).sum()
    ctx_w["year"] = ctx_w.index.year

    cfeats = ["btc_ret", "eth_ret", "eth_minus_btc", "btc_vol20", "dispersion", "breadth", "market_ret", "btc_absret"]
    for leg, wk in wk_legs.items():
        j = pd.concat([wk.rename("r"), ctx_w], axis=1).dropna(subset=["r"])
        pos, neg = j[j.r > 0], j[j.r < 0]
        print(f"\n=== {leg} leg: {len(pos)} pos weeks vs {len(neg)} neg weeks (contemporaneous context) ===")
        for f in cfeats:
            gap = (pos[f].mean() - neg[f].mean()) / (j[f].std() + 1e-9)
            signs = [np.sign(gy[gy.r > 0][f].mean() - gy[gy.r < 0][f].mean())
                     for y, gy in j.groupby("year") if (gy.r > 0).any() and (gy.r < 0).any()]
            stable = len(signs) > 1 and all(s == signs[0] for s in signs)
            print(f"    {f:14s} pos={pos[f].mean():+.4f}  neg={neg[f].mean():+.4f}  gap={gap:+.2f}{' *' if stable else ''}")

    # causal week-timing: PRIOR-week context -> this week's TOTAL sign (walk-forward + shuffle)
    print("\n=== causal week-timing: can PRIOR-week context predict this week's book sign? ===")
    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    lagged = ctx_w[cfeats].shift(1)
    total_oof = None
    for leg in ("TOTAL", "LONG", "SHORT"):
        j = pd.concat([wk_legs[leg].rename("r"), lagged], axis=1).dropna()
        j["y"] = (j["r"] > 0).astype(int)
        dn = j.index.tz_localize(None).values
        oof = pd.Series(np.nan, index=j.index)
        for tr_d, te_d in time_folds(dn, 4, embargo=1):
            tri = j.index[np.isin(dn, tr_d)]; tei = j.index[np.isin(dn, te_d)]
            if len(tri) < 40 or j.loc[tri, "y"].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=200, depth=3, learning_rate=0.03, l2_leaf_reg=8,
                                    random_seed=0, verbose=False)
            cb.fit(j.loc[tri, cfeats], j.loc[tri, "y"])
            oof.loc[tei] = cb.predict_proba(j.loc[tei, cfeats])[:, 1]
        mok = oof.notna() & j["y"].notna()
        auc = roc_auc_score(j.loc[mok, "y"], oof[mok]) if mok.sum() > 20 and j.loc[mok, "y"].nunique() == 2 else np.nan
        rng = np.random.default_rng(0)
        ysh = pd.Series(rng.permutation(j["y"].values), index=j.index)
        auc_sh = roc_auc_score(ysh[mok], oof[mok]) if mok.sum() > 20 else np.nan
        print(f"    {leg:5s} week-sign OOF AUC={auc:.3f}  (shuffle {auc_sh:.3f})  base +weeks={j.y.mean():.2f}")
        if leg == "TOTAL":
            total_oof = pd.concat([wk_legs["TOTAL"].rename("r"), oof.rename("p")], axis=1).dropna()

    # CONVERSION: go flat in predicted-bad weeks (bottom-tercile P(up)) + shuffle control
    print("\n=== does the timing signal CONVERT? go flat in predicted-bad weeks ===")
    def wk_stats(s):
        s = s.dropna(); eq = (1 + s).prod() ** (52 / len(s)) - 1
        return eq, s.mean() / s.std() * np.sqrt(52), float((s > 0).mean())
    t = total_oof.copy()
    thr = t["p"].quantile(1 / 3)
    base = t["r"]; timed = t["r"].where(t["p"] > thr, 0.0)
    rng = np.random.default_rng(1)
    sh_p = pd.Series(rng.permutation(t["p"].values), index=t.index)
    shuf = t["r"].where(sh_p > sh_p.quantile(1 / 3), 0.0)
    for name, s in (("baseline", base), ("timed (flat bad wks)", timed), ("shuffle control", shuf)):
        c, sh, wp = wk_stats(s)
        print(f"    {name:22s} CAGR~{c*100:+.0f}%  Sharpe={sh:.2f}  +weeks(active)={wp:.2f}")
    print("    (timed >> baseline AND >> shuffle => a real, tradeable week-timing lever)")

    # STRUCTURAL fix: the book has net short-beta -> hedge it with a causal rolling beta
    print("\n=== structural: beta-neutralize the net market exposure (causal rolling beta) ===")
    mret_d = ret.where(um).mean(axis=1).reindex(totald.index).fillna(0.0)
    btc_d = close["BTCUSDT"].pct_change().reindex(totald.index).fillna(0.0)
    for hname, hedge in (("market", mret_d), ("BTC", btc_d)):
        cov = totald.rolling(60, min_periods=30).cov(hedge)
        var = hedge.rolling(60, min_periods=30).var()
        beta = (cov / var).shift(1).fillna(0.0).clip(-3, 3)
        hedged = totald - beta * hedge
        for tag, s in (("raw", totald), (f"beta-neutral vs {hname}", hedged)):
            wk = s.resample(W).sum(); c, shh, wp = wk_stats(wk)
            yr = {y: (1 + g).prod() - 1 for y, g in s.groupby(s.index.year)}
            ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
            print(f"    {tag:22s} CAGR~{c*100:+.0f}%  Sharpe={shh:.2f}  +weeks={wp:.2f}  [{ys}]")


if __name__ == "__main__":
    main()
