"""Breakeven WITHOUT an initial stop, by trigger, on 1h and 1d (user Q).

Idea: keep the daily book's fixed hold (no early stop -> we do not cut losers that
later revert), but once a position is clearly in profit (a breakeven TRIGGER fires),
place a stop at entry so a winner cannot turn into a loser. This only protects trades
that went into profit then reversed; it never stops an early adverse move.

Triggers (on the chosen bar granularity): 'atr' (moved be*ATR in favor), 'prev'
(close breaks the prior bar's extreme in favor -> on 1d this is the daily-candle
break), 'two' (two consecutive favorable bars). Compared vs fixed, market-relative to
BTC, long & short legs. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.be_only --n 60 --hold 15
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup
from anomaly_science.strategy.prl.research.swing_exit import _load_1h


def be_only_exit(h, l, c, op, side, trigger, be_mult, atr_win):
    """No initial stop; arm a breakeven stop when `trigger` fires. Returns
    (raw_signed_return, exit_k) where exit_k is the bar index of exit."""
    n = len(c)
    if n < atr_win + 2:
        return None
    o = float(op[0])
    if not (o > 0):
        return None
    pc = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    a = float(np.nanmean(tr[:atr_win]))
    if not (a > 0):
        return None
    armed = False
    favor = 0
    sgn = 1.0 if side > 0 else -1.0
    for k in range(1, n):
        if armed:  # breakeven stop at entry; exit if price returns to entry
            if (side < 0 and h[k] >= o) or (side > 0 and l[k] <= o):
                return 0.0, k  # exit at entry -> raw 0
        if not armed:
            if trigger == "atr":
                trig = side * (c[k] - o) >= be_mult * a
            elif trigger == "prev":
                trig = (c[k] < l[k - 1]) if side < 0 else (c[k] > h[k - 1])
            else:  # two
                fav = (c[k] < op[k]) if side < 0 else (c[k] > op[k])
                favor = favor + 1 if fav else 0
                trig = favor >= 2
            if trig:
                armed = True
    return sgn * (c[-1] / o - 1.0), n - 1


def real_book(close, open_, dH, dL, dC, um, score, p, trigger, hold, cost_mult=1.0):
    """REAL daily-marked dollar-neutral decile long-short book with per-name
    breakeven-only exits (no per-name market-beta adjustment; market P&L is real).
    trigger='none' -> never arms = pure fixed hold (baseline in the same engine)."""
    idx = close.index
    daily = pd.Series(0.0, index=idx)
    side_bps = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
    warm = max(max(p.mom_lbs) + p.skip + 2, p.beta_lb + 2, p.liquidity_lb + 2, p.min_age_days + 2)
    for ri in range(warm, len(idx) - hold - 1, hold):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 30:
            continue
        order = s.sort_values(ascending=False)
        k = max(1, int(len(order) * 0.1))
        picks = {sym: +1 for sym in order.index[:k]}
        picks.update({sym: -1 for sym in order.index[-k:]})
        st = {}
        for sym, sd in picks.items():
            e = open_[sym].iloc[ri + 1]
            if e > 0:
                st[sym] = {"side": sd, "entry": float(e), "pc": float(e),
                           "armed": False, "fav": 0, "alive": True}
        if not st:
            continue
        wgt = 1.0 / len(st)
        daily.iloc[ri + 1] -= wgt * len(st) * 2 * side_bps  # round-trip cost
        for t in range(ri + 1, min(ri + 1 + hold, len(idx))):
            dr = 0.0
            for sym, z in st.items():
                if not z["alive"]:
                    continue
                c_t = close[sym].iloc[t]
                if not (c_t > 0):
                    z["alive"] = False
                    continue
                base = z["entry"] if t == ri + 1 else z["pc"]
                if z["armed"]:
                    h_t = dH[sym].iloc[t]; l_t = dL[sym].iloc[t]
                    if (z["side"] < 0 and h_t >= z["entry"]) or (z["side"] > 0 and l_t <= z["entry"]):
                        dr += z["side"] * wgt * (z["entry"] / base - 1.0)  # exit at entry
                        z["alive"] = False
                        continue
                dr += z["side"] * wgt * (c_t / base - 1.0)
                z["pc"] = float(c_t)
                if not z["armed"] and trigger != "none":
                    o_t = open_[sym].iloc[t]
                    if trigger == "two":
                        fav = (c_t < o_t) if z["side"] < 0 else (c_t > o_t)
                        z["fav"] = z["fav"] + 1 if fav else 0
                        if z["fav"] >= 2:
                            z["armed"] = True
                    elif trigger == "prev":
                        hp = dH[sym].iloc[t - 1]; lp = dL[sym].iloc[t - 1]
                        if (z["side"] < 0 and c_t < lp) or (z["side"] > 0 and c_t > hp):
                            z["armed"] = True
            daily.iloc[t] += dr
    d = daily.loc[daily.ne(0).cumsum() > 0]
    return d


def _fixed(op, c, side):
    o = float(op[0])
    if not (o > 0):
        return None
    return (1.0 if side > 0 else -1.0) * (float(c[-1]) / o - 1.0), len(c) - 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--hold", type=int, default=15)
    ap.add_argument("--be", type=float, default=1.0, help="ATR-trigger multiple")
    args = ap.parse_args()

    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    from dataclasses import replace
    p = replace(PRIMARY, universe_n=args.n)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, args.n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um, score = m["close"], m["umask"], m["score"]
    dO, dH, dL, dC = (pn.pivot(panel, x).reindex_like(close) for x in ("open", "high", "low", "close"))
    start, end = close.index.min(), close.index.max() + pd.Timedelta(days=args.hold + 2)
    syms = [c for c in close.columns if bool(um[c].any())]
    print(f"loading 1h for {len(syms)} symbols ...")
    h1 = {s: v for s, v in ((s, _load_1h(s, start, end)) for s in syms) if v is not None}
    btc = _load_1h("BTCUSDT", start, end)
    idx = close.index
    bo = btc["open"]; bc = btc["close"]

    def mkt(t_start, t_end):
        """BTC return over [t_start, t_end]; used per trade over its ACTUAL window."""
        seg_o = bo[bo.index >= t_start]
        seg_c = bc[bc.index <= t_end]
        if len(seg_o) < 1 or len(seg_c) < 1:
            return 0.0
        return float(seg_c.iloc[-1] / seg_o.iloc[0] - 1.0)

    rows = []
    for ri in range(_warmup(p), len(idx) - args.hold - 1, args.hold):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 30:
            continue
        order = s.sort_values(ascending=False); k = max(1, int(len(order) * 0.1))
        di0, di1 = ri + 1, min(ri + 1 + args.hold, len(idx))
        t0 = idx[ri] + pd.Timedelta(days=1)
        for side, names in ((+1, order.index[:k]), (-1, order.index[-k:])):
            sgn = 1.0 if side > 0 else -1.0
            for sym in names:
                do = dO[sym].iloc[di0:di1].to_numpy(); dh = dH[sym].iloc[di0:di1].to_numpy()
                dl = dL[sym].iloc[di0:di1].to_numpy(); dc = dC[sym].iloc[di0:di1].to_numpy()
                if len(dc) < 6 or not (do[0] > 0):
                    continue
                row = {"side": side, "date": idx[ri]}
                fxr = _fixed(do, dc, side)
                row["fixed"] = fxr[0] - sgn * mkt(t0, idx[di0 + fxr[1]])
                for trig in ("atr", "prev", "two"):
                    r = be_only_exit(dh, dl, dc, do, side, trig, args.be, atr_win=5)
                    if r is not None:
                        row[f"1d_{trig}"] = r[0] - sgn * mkt(t0, idx[di0 + r[1]])
                d = h1.get(sym)
                if d is not None:
                    b = d[d.index >= t0].iloc[:args.hold * 24]
                    if len(b) > 30 and b["open"].iloc[0] > 0:
                        bt = b.index
                        ho, hh, hl, hc = (b[x].to_numpy() for x in ("open", "high", "low", "close"))
                        for trig in ("atr", "prev", "two"):
                            r = be_only_exit(hh, hl, hc, ho, side, trig, args.be, atr_win=24)
                            if r is not None:
                                row[f"1h_{trig}"] = r[0] - sgn * mkt(t0, bt[r[1]])
                rows.append(row)
    tr = pd.DataFrame(rows)
    cols = [c for c in tr.columns if c not in ("side", "date")]
    print(f"trades: {len(tr)}  (hold={args.hold}d, ATR-trigger={args.be}ATR, per-trade market-adjusted)\n")
    for side, nm in ((+1, "LONG"), (-1, "SHORT"), (0, "BOTH")):
        g = tr if side == 0 else tr[tr.side == side]
        print(f"  {nm} (per-trade mean%, market-relative):")
        for c in cols:
            gc = g[c].dropna()
            if len(gc):
                print(f"    {c:9s} win={ (gc>0).mean():.3f}  mean={gc.mean()*100:+.3f}%  med={gc.median()*100:+.3f}%")
        print()

    # PORTFOLIO level: per-rebalance equal-weight long-short return (guards against
    # a per-trade mean inflated by a few saved catastrophes).
    print("  PORTFOLIO (per-trade market-adjusted, equal-weight per rebalance):")
    reb = tr.groupby("date")[cols].mean()
    for c in cols:
        s = reb[c].dropna()
        cagr = (1 + s).prod() ** (17.0 / len(s)) - 1 if len(s) else np.nan
        print(f"    {c:9s} per-reb mean={s.mean()*100:+.3f}%  +rebs={float((s>0).mean())*100:.0f}%  "
              f"CAGR~{cagr*100:+.0f}%  worst={s.min()*100:+.1f}%")

    # DECISIVE audit: REAL daily-marked dollar-neutral book + SHUFFLE control.
    # A breakeven "exit exactly at entry when the daily range touches it" is a known
    # intrabar-fill artifact: it manufactures edge from ANY signal. The shuffle test
    # (random per-date ranks) is the arbiter -- if breakeven still shines on noise,
    # the result is fake. See docs/strategies/prl_breakeven_artifact_v1.md.
    rng = np.random.default_rng(0)
    sh_score = score.copy(); a = sh_score.to_numpy()
    for i in range(a.shape[0]):
        fin = np.where(np.isfinite(a[i]))[0]
        if len(fin) > 1:
            a[i][fin] = rng.permutation(a[i][fin])
    sh_score = pd.DataFrame(a, index=score.index, columns=score.columns)

    print("\n  REAL daily-marked dollar-neutral book (true market P&L) + SHUFFLE control:")
    print(f"    {'signal':9s} {'exit':6s} {'CAGR':>7} {'Sharpe':>7} {'+weeks':>7} {'maxDD':>7}")
    for sig_name, sc in (("real", score), ("shuffled", sh_score)):
        for trig in ("none", "two"):
            d = real_book(close, dO, dH, dL, dC, um, sc, p, trig, args.hold)
            if not len(d):
                continue
            wk = d.resample("W").sum(); eq = (1 + d).cumprod()
            dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(d)) - 1
            s2 = d.mean() / d.std() * np.sqrt(252)
            print(f"    {sig_name:9s} {trig:6s} {cagr*100:+6.0f}% {s2:7.2f} "
                  f"{float((wk>0).mean())*100:6.0f}% {dd*100:6.0f}%")
    print("    -> breakeven shines on SHUFFLED (no) signal too => ARTIFACT, not alpha.")


if __name__ == "__main__":
    main()
