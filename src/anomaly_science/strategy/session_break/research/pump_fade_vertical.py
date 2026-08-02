"""Reframe pump-fade around VERTICAL anomalies: run the real anomaly detector on 1m
(sharp volume/trade/range spike per our anomaly definition), take each anomaly's
session as the pump, then trade it SHORT by the session + structure-break rules
(BOS entry, scale-in at the anomaly high, hold ~2 sessions, structural stop). This
replaces the smooth session-volume-z anomaly with a real vertical spike.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.anomaly_detector import detect_broad_anomaly_events_from_frame
import anomaly_science.strategy.session_break.research.session_streak as ss
from anomaly_science.strategy.session_break.research.session_streak import btc_session_index, _init_worker
from anomaly_science.strategy.session_break.research.market_context import market_asof
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.reclaim import _block_runs
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ, block_seq_for_ms, utc_day_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_SEQ = {0: "ASIA", 3: "US", 4: "LATE", 1: "EU", 2: "OVERLAP"}
# pump session seq -> the fade only works into liquidity: ASIA->EU(0->1), LATE->ASIA(4->0), EU->OVERLAP(1->2)
FADE_NEXT = {0: 1, 4: 0, 1: 2}
PIVK = 2; KCONF = 10; CAP = 60; LOOKBACK = 32; COST = 0.0008; FLOOR = 0.04
PUMP_MIN = 0.20          # real manipulation pump: >= +20% from base to peak over the burst window
BASE_MIN = 60            # 1m bars before ignition to find the base (last low before the run)
PEAK_WIN = 120           # 1m bars after ignition to find the pump peak (the "pump period", up to ~2h)


def build_symbol(path: Path, tf_min: int = 15) -> pd.DataFrame:
    raw = load_ohlcv_parquet(path)
    n1 = len(raw["timestamp"])
    if n1 < 5000:
        return pd.DataFrame()
    fr = pd.DataFrame({
        "symbol": path.stem, "open_time_ms": raw["timestamp"].astype("int64"),
        "available_time_ms": raw["timestamp"].astype("int64") + 60000,
        "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"],
        "volume": raw["quote_volume"], "quote_volume": raw["quote_volume"],   # only quote_volume available
        "number_of_trades": raw.get("trade_count", pd.Series(np.nan, index=range(n1))),
    })
    try:
        events = detect_broad_anomaly_events_from_frame(fr)
    except Exception:
        return pd.DataFrame()
    if not events:
        return pd.DataFrame()
    t1 = raw["timestamp"].to_numpy(np.int64) if hasattr(raw["timestamp"], "to_numpy") else np.asarray(raw["timestamp"], np.int64)
    h1 = np.asarray(raw["high"], float); l1 = np.asarray(raw["low"], float)
    df = resample_ohlcv_np(raw, tf_min)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; qv = df["quote_volume"]
    n = len(ts)
    atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    last_pl, last_ph = causal_pivots(h, l, PIVK)
    runs = _block_runs(day, seq)
    # map each 15m bar -> (session start_idx, end_idx, seq) for its run
    run_of = {}
    for d, s, st, en in runs:
        for _ in (0,):
            run_of[st] = (st, en, int(s))
    # sorted run starts for lookup
    starts = np.array(sorted(run_of))
    seen = set(); rows = []
    for ev in events:
        ig = int(ev.event_start_time_ms)
        # REAL vertical pump: base = low in the 30m before ignition, peak = high in the
        # ~1h burst after; require >= PUMP_MIN move base->peak (our anomaly definition).
        i1 = int(np.searchsorted(t1, ig))
        if i1 < BASE_MIN or i1 + PEAK_WIN >= len(t1):
            continue
        base = float(l1[i1 - BASE_MIN:i1].min()); peak = float(h1[i1:i1 + PEAK_WIN].max())
        pump_mag = peak / base - 1.0 if base > 0 else 0.0
        if pump_mag < PUMP_MIN:
            continue
        bi = int(np.searchsorted(ts, ig, side="right")) - 1      # 15m bar containing the ignition
        if bi < LOOKBACK or bi >= n:
            continue
        # pump session = the run containing bi
        k = int(np.searchsorted(starts, bi, side="right")) - 1
        if k < 0:
            continue
        pst = int(starts[k]); pen, pseq = run_of[pst][1], run_of[pst][2]
        if pst in seen:
            continue
        if pseq not in FADE_NEXT:
            continue
        # pump session must be an UP move closing near its high (FOMO top)
        p_open = float(o[pst]); p_close = float(c[pen - 1]); p_hi = float(h[pst:pen].max()); p_lo = float(l[pst:pen].min())
        rng = p_hi - p_lo
        if not (p_close > p_open and rng > 0 and (p_close - p_lo) / rng > 0.55):
            continue
        # entry session = next run; require it is the fade-partner block
        e = pen
        if e >= n or int(seq[e]) != FADE_NEXT[pseq]:
            continue
        seen.add(pst)
        # idiosyncratic (coin move vs alt market during the pump)
        mk = market_asof(ss._MKT_TS, ss._MKT_VALS, int(ts[pen - 1])) if ss._MKT_TS is not None else {}
        idio = (p_close / p_open - 1.0) - mk.get("mkt_alt_ret_med", 0.0) if mk else np.nan
        # BOS short entry in the entry session
        run_hi = h[e]; ent = None
        for j in range(e + 1, min(e + KCONF, n)):
            run_hi = max(run_hi, h[j])
            if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                ent = j; break
        if ent is None or ent + CAP + 2 >= n:
            continue
        entry = float(c[ent]); a = atr[ent] if atr[ent] > 0 else entry * 0.005
        sh = last_ph[ent]; stop = float((sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a)
        sd = (stop - entry) / entry
        if sd <= 0:
            continue
        pump_hi = float(h[pst - LOOKBACK:e].max()) if pst - LOOKBACK >= 0 else p_hi
        # exit: hold to end of session 2 or structural stop; scale-in add 0.5 at pump-high retest
        s1e = run_of.get(int(starts[int(np.searchsorted(starts, e, side="right")) - 1]), (e, e, 0))[1]
        we = min(int(np.searchsorted(ts, ts[s1e] + 8 * 3600_000)), ent + CAP, n - 1)
        added = False; cstop = stop; exit_px = None; pstop = pump_hi + 0.5 * a
        for j in range(ent + 1, we + 1):
            if not added and h[j] >= pump_hi:
                added = True; cstop = pstop
            if h[j] >= cstop:
                exit_px = cstop; break
        if exit_px is None:
            exit_px = float(c[we])
        if added:
            pnl = 0.5 * (-(exit_px / entry - 1)) + 0.5 * (-(exit_px / pump_hi - 1)) - COST
            risk = 0.5 * (pstop / entry - 1) + 0.5 * (pstop / pump_hi - 1)
        else:
            pnl = -(exit_px / entry - 1) - COST; risk = sd
        R = pnl / max(risk, FLOOR)
        rows.append({
            "symbol": path.stem, "pump_ts": int(ts[pst]), "entry_ts": int(ts[ent]),
            "week": pd.Timestamp(int(ts[ent]), unit="ms", tz="UTC").strftime("%G-W%V"),
            "pair": f"{FADE_SEQ[pseq]}->{FADE_SEQ[FADE_NEXT[pseq]]}",
            # VERTICAL anomaly features (from the detector)
            "vol_z": _f(ev.initial_quote_volume_zscore), "trade_z": _f(ev.initial_trade_count_zscore),
            "move_pct": _f(ev.initial_move_pct), "pump_mag": pump_mag, "trigger": str(ev.trigger_component),
            "p_close_pos": (p_close - p_lo) / rng, "idio_ret": idio, "p_ret": p_close / p_open - 1.0,
            "R": R, "net_pct": pnl, "sd": risk, "win": int(pnl > 0),
        })
    return pd.DataFrame(rows)


def _f(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def _one(a):
    try:
        return build_symbol(Path(a[0]))
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/pump_fade_vertical.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    btc = btc_session_index(15)
    from anomaly_science.strategy.session_break.research.market_context import load_market_arrays
    mp = Path(".output/results/session_break/market_index_tf15.parquet")
    mts, mvals = load_market_arrays(mp) if mp.exists() else (None, {})
    print(f"symbols={len(files)}  running vertical-anomaly detector + fade trade...")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(btc, mts, mvals)) as ex:
        futs = {ex.submit(_one, (f,)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(files)} trades={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    if not len(t):
        print("\nNO trades produced -- check detector/funnel"); return
    d = t[t.entry_ts < DEV_END]
    print(f"\nwrote {len(t):,} vertical-anomaly fade trades ({len(d):,} DEV)")
    if len(d):
        R = d.R.to_numpy()
        print(f"win={ (R>0).mean()*100:.1f}%  exp={R.mean():+.3f}R  PF={R[R>0].sum()/(-R[R<0].sum()+1e-9):.2f}  "
              f"median pump={d.pump_mag.median()*100:.1f}%  (p25={d.pump_mag.quantile(.25)*100:.1f}% p75={d.pump_mag.quantile(.75)*100:.1f}%)  vol_z med={d.vol_z.median():.1f}")
        for pr, g in d.groupby("pair"):
            print(f"  {pr:<14} n={len(g):>5} win={ (g.R>0).mean()*100:.0f}% exp={g.R.mean():+.3f}R")


if __name__ == "__main__":
    main()
