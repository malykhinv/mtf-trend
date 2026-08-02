"""Account-level backtest of the anomalous-volume PUMP-FADE short (champion cell).
Realistic trade: short at the open of the session after the pump; initial stop above
the pump high (defines the per-trade risk for position sizing); move stop to
break-even after 8 bars; otherwise hold ~2 sessions. Per-trade R = net return /
initial-stop distance. Then a concurrency-aware equity sim from $1000 at 2/3/4/5%
risk, and a full human-readable stat block (everything in %).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008          # 8bp round-trip
MAXBARS = 48
LOOKBACK = 32
BE_BARS = 8
STOP_BUF = 0.5         # initial stop = pump_high + STOP_BUF*ATR


def sim_trade(o, h, l, c, atr, e, we, pump_high):
    entry = o[e]; a = atr[e] if atr[e] > 0 else entry * 0.005
    if entry <= 0:
        return None
    stop = pump_high + STOP_BUF * a
    stop_dist = (stop - entry) / entry
    if stop_dist <= 0:
        return None
    be_on = False; exit_px = None; exit_bar = we - 1
    mae = 0.0; mfe = 0.0                       # short: adverse=up, favourable=down (fractions)
    for j in range(e, we):
        mae = max(mae, (h[j] - entry) / entry)
        mfe = max(mfe, (entry - l[j]) / entry)
        if not be_on and (j - e) >= BE_BARS:
            stop = min(stop, entry); be_on = True
        if h[j] >= stop:
            exit_px = stop; exit_bar = j; break
    if exit_px is None:
        exit_px = c[we - 1]
    pnl = -(exit_px / entry - 1) - COST        # short net return
    return {"pnl": pnl, "R": pnl / stop_dist, "stop_dist": stop_dist,
            "mae": mae, "mfe": mfe, "exit_bar": exit_bar - e}


def build_trades(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sym, g in events.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW); n = len(ts)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + 3 >= n:
                continue
            we = min(e + MAXBARS, n)
            res = sim_trade(o, h, l, c, atr, e, we, float(h[e - LOOKBACK:e].max()))
            if res is None:
                continue
            res.update({"symbol": sym, "entry_ts": int(ts[e]), "exit_ts": int(ts[min(e + res["exit_bar"], n - 1)]),
                        "pair": r.pair, "reltc_z": r.reltc_z, "week": r.week,
                        "day": pd.Timestamp(int(ts[e]), unit="ms", tz="UTC").strftime("%Y-%m-%d")})
            rows.append(res)
    return pd.DataFrame(rows)


def equity_sim(tr: pd.DataFrame, risk_pct: float):
    """Concurrency-aware: size each trade at risk_pct of settled equity at entry,
    settle P&L at exit. Returns final equity multiple, max DD, max concurrent."""
    ev = []
    for i, r in tr.iterrows():
        ev.append((int(r.entry_ts), 0, i))   # open
        ev.append((int(r.exit_ts), 1, i))    # close
    ev.sort(key=lambda x: (x[0], x[1]))
    eq = 1.0; size = {}; peak = 1.0; maxdd = 0.0; openset = set(); maxconc = 0
    for ts, typ, i in ev:
        if typ == 0:
            size[i] = (risk_pct / 100.0) * eq
            openset.add(i); maxconc = max(maxconc, len(openset))
        else:
            eq += tr.loc[i, "R"] * size.get(i, 0.0)
            openset.discard(i)
            peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak)
    return eq, maxdd, maxconc


def pct(x):
    return f"{x*100:+.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}")
    tr = build_trades(champ).sort_values("entry_ts").reset_index(drop=True)
    print(f"simulated trades: {len(tr):,}\n")

    R = tr.R.to_numpy()
    win = R > 0
    ndays = tr.day.nunique(); nweeks = tr.week.nunique()

    # ---- account sim per risk level ----
    print("=== ACCOUNT ($1000 start, concurrency-aware) ===")
    print(f"{'risk/trade':>11}{'final':>10}{'maxDD':>9}{'final$':>10}")
    for rp in (2, 3, 4, 5):
        eq, dd, conc = equity_sim(tr, rp)
        print(f"{rp:>10}%{pct(eq-1):>10}{-dd*100:>8.1f}%{1000*eq:>9.0f}$")
    _, _, maxconc = equity_sim(tr, 2)
    print(f"max concurrent open positions: {maxconc}   trades/day: {len(tr)/ndays:.1f}")

    # ---- streaks ----
    s = np.sign(R); mw = ml = cw = cl = 0
    for v in s:
        if v > 0:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        mw = max(mw, cw); ml = max(ml, cl)

    # ---- edge concentration: % of top trades to remove to go negative ----
    srt = np.sort(R)[::-1]; k = 0; tot = R.sum()
    run = tot
    for r in srt:
        run -= r; k += 1
        if run <= 0:
            break
    frac_top = k / len(R)

    # ---- MAE in winners / MFE in losers ----
    mae_win = tr.loc[win, "mae"]; mfe_los = tr.loc[~win, "mfe"]

    print("\n=== TRADE STATS (all in %) ===")
    print(f"trades={len(tr)}  win rate={win.mean()*100:.1f}%  expectancy/trade={R.mean():.3f}R  total={R.sum():.0f}R")
    print(f"avg win={R[win].mean():.2f}R  avg loss={R[~win].mean():.2f}R  profit factor={R[win].sum()/-R[~win].sum():.2f}")
    print(f"max win streak={mw}  max loss streak={ml}")
    print(f"remove top {frac_top*100:.1f}% of trades to turn total negative ({k} of {len(R)})")
    print(f"realised R: mean={R.mean():.3f}  median={np.median(R):.3f}  best={R.max():.1f}R  worst={R.min():.1f}R")
    print(f"MAE we sit through in WINNERS: avg={mae_win.mean()*100:.1f}%  max={mae_win.max()*100:.1f}%  (of price)")
    print(f"MFE that runs in LOSERS:       avg={mfe_los.mean()*100:.1f}%  max={mfe_los.max()*100:.1f}%  (of price)")

    # ---- positive days / weeks ----
    dayR = tr.groupby("day").R.sum(); weekR = tr.groupby("week").R.sum()
    print(f"positive days={ (dayR>0).mean()*100:.0f}% ({int((dayR>0).sum())}/{len(dayR)})   "
          f"positive weeks={ (weekR>0).mean()*100:.0f}% ({int((weekR>0).sum())}/{len(weekR)})")
    print(f"best day={dayR.max():.1f}R  worst day={dayR.min():.1f}R  best week={weekR.max():.1f}R  worst week={weekR.min():.1f}R")

    # ---- distribution by session pair ----
    print("\n=== by SESSION PAIR (share of trades, avg R, win%) ===")
    for pair, g in tr.groupby("pair"):
        print(f"  {pair:<14} {len(g)/len(tr)*100:>5.1f}% of trades   avgR={g.R.mean():+.3f}  win={ (g.R>0).mean()*100:.0f}%")

    # ---- distribution by anomaly-to-BTC ratio ----
    print("\n=== by ANOMALY-vs-BTC trade ratio (reltc_z tercile) ===")
    tr2 = tr[np.isfinite(tr.reltc_z)].copy()
    tr2["btc_tier"] = pd.qcut(tr2.reltc_z, 3, labels=["low", "mid", "high"])
    for tier, g in tr2.groupby("btc_tier"):
        print(f"  {tier:<5} {len(g)/len(tr2)*100:>5.1f}% of trades   avgR={g.R.mean():+.3f}  win={ (g.R>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
