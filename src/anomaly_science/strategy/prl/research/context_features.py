"""Context features on the daily book win/lose (user questions):
  * proximity to ATH/ATL (expanding + 250d)
  * position vs EMA 10/20/50/100/200
  * entry-candle & last-3 range/body vs ATR
  * recurrence: count of prior volume-spikes / big-moves in history
  * volume & trade-count levels/z
  * spike dynamics: fast (vertical) vs smooth

For each we report the win-lose separation WITH per-year sign stability, and whether
adding them to a causal walk-forward win-classifier raises OOF AUC over the base pool
(i.e. do they add real, OOS predictive power the ranker did not already have?).

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.context_features
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import feature_pool as fpm
from anomaly_science.strategy.prl.research.loser_filter import _oof_winprob, _auc
from anomaly_science.strategy.prl.research.policy import PRIMARY as P
from anomaly_science.strategy.prl.research.run_coarse import _warmup

STEP, Q = 15, 0.10
_T = 1e-9


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(3, w // 2)), fn)()


def _atr(high, low, close, win=14):
    pc = close.shift(1)
    tr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum)
    return tr.rolling(win, min_periods=win // 2).mean()


def build():
    oos = pd.Timestamp(P.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qvp = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qvp, P.universe_n, P.liquidity_lb, P.min_age_days)
    b = fpm.build_descriptors(panel, um, P)
    base_d, label, um, close = b["descriptors"], b["label"], b["umask"], b["close"]
    high, low, open_ = (pn.pivot(panel, x).reindex_like(close) for x in ("high", "low", "open"))
    qv = pn.pivot(panel, "quote_volume").reindex_like(close)
    ntr = pn.pivot(panel, "number_of_trades").reindex_like(close)
    atr = _atr(high, low, close)
    ret = close.pct_change(fill_method=None)
    sk = P.skip

    ctx = {}
    ctx["dist_ath_all"] = (close / close.cummax() - 1).shift(sk)
    ctx["dist_atl_all"] = (close / close.cummin() - 1).shift(sk)
    ctx["dist_ath_250"] = (close / _roll(close, 250, "max") - 1).shift(sk)
    for span in (10, 20, 50, 100, 200):
        ema = close.ewm(span=span, min_periods=span // 2).mean()
        ctx[f"ema{span}_pos"] = (close / ema - 1).shift(sk)
    ctx["ecandle_range_atr"] = (high - low) / (atr + _T)          # entry-day candle (no shift)
    ctx["ecandle_body_atr"] = (close - open_).abs() / (atr + _T)
    ctx["ecandle_ret_atr"] = (close - open_) / (atr + _T)          # signed
    ctx["last3_range_atr"] = _roll((high - low) / (atr + _T), 3, "mean")
    spike = (qv > 3 * _roll(qv, 28, "mean")).astype(float)
    ctx["n_volspikes_60"] = _roll(spike, 60, "sum").shift(sk)
    bigmove = (ret.abs() > 3 * _roll(ret, 20, "std")).astype(float)
    ctx["n_bigmoves_60"] = _roll(bigmove, 60, "sum").shift(sk)
    ctx["log_qv"] = np.log(qv + 1).shift(sk)
    ctx["log_ntr"] = np.log(ntr + 1).shift(sk)
    ctx["qv_z60"] = ((qv - _roll(qv, 60, "mean")) / (_roll(qv, 60, "std") + _T)).shift(sk)
    up = ret.clip(lower=0)
    ctx["verticality14"] = (_roll(up, 14, "max") / (_roll(up, 14, "sum") + _T)).shift(sk)
    ctx["recent_gain_share"] = (_roll(ret, 3, "sum") / (_roll(ret, 14, "sum").abs() + _T)).shift(sk)

    def rank(m):
        return m.where(um).rank(axis=1, pct=True)
    base_feats = {k: rank(v) for k, v in base_d.items()}
    ctx_feats = {k: rank(v) for k, v in ctx.items()}

    # OOF linear score as conviction feature
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)

    idx = close.index
    rows = []
    allf = {**base_feats, **ctx_feats}
    for ri in range(_warmup(P), len(idx) - STEP - 2, STEP):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 40:
            continue
        fwd = (close.iloc[ri + 1 + STEP] / close.iloc[ri + 1] - 1.0).reindex(s.index)
        rel = fwd - fwd.mean()
        order = s.sort_values(ascending=False)
        k = max(1, int(len(order) * Q))
        for side, syms in ((+1, order.index[:k]), (-1, order.index[-k:])):
            for sym in syms:
                r = rel.get(sym, np.nan)
                if not np.isfinite(r):
                    continue
                row = {"date": idx[ri], "side": side, "win": int((r > 0) if side > 0 else (r < 0))}
                for fn, mat in allf.items():
                    row[fn] = mat.iloc[ri].get(sym, np.nan)
                rows.append(row)
    return pd.DataFrame(rows), list(base_feats), list(ctx_feats)


def main():
    tr, base_feats, ctx_feats = build()
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    print(f"trades: {len(tr)}  longs={int((tr.side>0).sum())}  shorts={int((tr.side<0).sum())}\n")

    for side, nm in ((+1, "LONG"), (-1, "SHORT")):
        g = tr[tr.side == side]
        rows = []
        for f in ctx_feats:
            gap = (g.loc[g.win == 1, f].mean() - g.loc[g.win == 0, f].mean()) / (g[f].std() + _T)
            signs = [np.sign(gy.loc[gy.win == 1, f].mean() - gy.loc[gy.win == 0, f].mean())
                     for y, gy in g.groupby("year") if gy.win.nunique() == 2]
            stable = len(signs) > 1 and all(s == signs[0] for s in signs)
            rows.append((f, gap, stable))
        rows.sort(key=lambda x: -abs(x[1]))
        print(f"=== {nm} (win {g.win.mean():.3f}) — context separators (win-lose std; * sign-stable) ===")
        for f, gap, stable in rows:
            print(f"    {f:20s} {gap:+.3f} {'*' if stable else ' '}")
        auc_base = _auc(g["win"].to_numpy(), _oof_winprob(g, base_feats).to_numpy())
        auc_full = _auc(g["win"].to_numpy(), _oof_winprob(g, base_feats + ctx_feats).to_numpy())
        print(f"    OOF AUC: base={auc_base:.3f}  base+context={auc_full:.3f}  "
              f"(delta {auc_full-auc_base:+.3f} = context's real OOS contribution)\n")


if __name__ == "__main__":
    main()
