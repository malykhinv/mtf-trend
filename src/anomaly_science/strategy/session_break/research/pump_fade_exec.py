"""Execution-mechanics sweep for the anomalous-volume PUMP-FADE short. We enter at
the open of the session after the pump and simulate, bar by bar over a fixed hold
window, a family of exits: hold-to-end, hard structural stop (above the pump high),
partial 0.5 take at an ATR target, and break-even after a PRICE trigger or a TIME
trigger -- plus their combinations. Reports realised short return (bp, net of cost),
win rate and weekly stability per variant, on the champion and broad cells.
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
COST_BP = 8.0
MAXBARS = 48       # ~12h hold from entry (about +2 sessions on 15m)
LOOKBACK = 32      # bars back to find the pump high for the structural stop


def sim_short(o, h, l, c, atr, e, we, pump_high, *, stop_buf=None, tp_atr=None, tp_frac=0.0,
              be_px_atr=None, be_time=None, be_after_tp=False):
    """Return realised SHORT return (fraction, +=profit). Conservative intrabar order:
    adverse (stop) checked before favourable (target)."""
    entry = o[e]; a = atr[e] if atr[e] > 0 else entry * 0.005
    if entry <= 0:
        return np.nan
    stop = pump_high + stop_buf * a if stop_buf is not None else None
    tp = entry - tp_atr * a if tp_atr else None
    rem = 1.0; pnl = 0.0; be_on = False
    for j in range(e, we):
        if be_px_atr is not None and not be_on and l[j] <= entry - be_px_atr * a:
            stop = entry; be_on = True
        if be_time is not None and not be_on and (j - e) >= be_time:
            stop = entry if (stop is None or entry < stop) else stop; be_on = True
        if stop is not None and h[j] >= stop:               # adverse first (pessimistic)
            pnl += -(stop / entry - 1) * rem; rem = 0.0; break
        if tp is not None and rem > 0.999 and l[j] <= tp:    # partial take once
            pnl += -(tp / entry - 1) * tp_frac; rem -= tp_frac
            if be_after_tp:
                stop = entry if (stop is None or entry < stop) else stop
    if rem > 0:
        pnl += -(c[we - 1] / entry - 1) * rem
    return pnl


VARIANTS = {
    "V0 hold-to-end": {},
    "SL pumpHi+0.5a": {"stop_buf": 0.5},
    "P50@2a + SL": {"stop_buf": 0.5, "tp_atr": 2.0, "tp_frac": 0.5},
    "P50@3a + SL": {"stop_buf": 0.5, "tp_atr": 3.0, "tp_frac": 0.5},
    "BE@px1.5a + SL": {"stop_buf": 0.5, "be_px_atr": 1.5},
    "BE@px1.0a + SL": {"stop_buf": 0.5, "be_px_atr": 1.0},
    "BE@time16 + SL": {"stop_buf": 0.5, "be_time": 16},
    "BE@time8 + SL": {"stop_buf": 0.5, "be_time": 8},
    "P50@2a + BE-after-TP": {"stop_buf": 0.5, "tp_atr": 2.0, "tp_frac": 0.5, "be_after_tp": True},
    "P50@2a + BE@px1.5a": {"stop_buf": 0.5, "tp_atr": 2.0, "tp_frac": 0.5, "be_px_atr": 1.5},
}


def run_cell(events: pd.DataFrame, name: str):
    print(f"\n################ {name}  (n={len(events):,}) ################")
    rows = {v: [] for v in VARIANTS}
    wk = {v: [] for v in VARIANTS}
    by_sym = {s: g for s, g in events.groupby("symbol")}
    for sym, g in by_sym.items():
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
            pump_high = float(h[e - LOOKBACK:e].max())
            for v, kw in VARIANTS.items():
                pnl = sim_short(o, h, l, c, atr, e, we, pump_high, **kw)
                if np.isfinite(pnl):
                    rows[v].append(pnl); wk[v].append((r.week, pnl))
    print(f"{'variant':<24}{'n':>7}{'net@8bp':>9}{'median':>9}{'win%':>7}{'wk+':>7}")
    for v in VARIANTS:
        a = np.array(rows[v])
        if len(a) < 100:
            continue
        net = a.mean() * 1e4 - COST_BP; med = np.median(a) * 1e4
        wdf = pd.DataFrame(wk[v], columns=["week", "r"]).groupby("week").r.mean()
        print(f"{v:<24}{len(a):>7}{net:>+8.1f}b{med:>+8.1f}b{(a>0).mean():>7.2f}{(wdf>0).mean():>7.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END].copy()
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)].copy()
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    run_cell(champ, "CHAMPION (isolated + close-top + new-high)")
    run_cell(base, "BASE (fade-pair up-vol top-decile)")


if __name__ == "__main__":
    main()
