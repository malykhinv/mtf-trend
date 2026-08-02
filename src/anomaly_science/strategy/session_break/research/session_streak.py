"""Strategy #3 -- SESSION-STREAK MOMENTUM (rich version).

Trade: enter at the session OPEN, exit at the session CLOSE. No SL/TP -- a pure
one-session directional hold. We take the trade only when CatBoost says the next
same-type session is likely to REPEAT the direction of a streak of prior same-type
sessions (US1..USN all down -> short the next US; all up -> long the next US).

Both directions are tracked SEPARATELY. We sweep the streak length N and, for each
(direction, N), report the base continuation rate, the realised session-hold return,
and -- the real test -- the realised return of only the model's top-decile (high
P(continue)) signals, with weekly stability and a cost haircut.

Dozens of market-logical features are built (see build_symbol): streak shape,
cleanliness of the moves, range overlap/expansion between sessions, intermediate-
session range expansion, volatility, trend context, volume/trades/taker/OI trends.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.market_context import MKT_COLS, load_market_arrays, market_asof
from anomaly_science.strategy.session_break.research.reclaim import _block_runs
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ, block_seq_for_ms, utc_day_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
COST_BP = 8.0   # round-trip cost assumption (entry+exit)


def sessions_frame(path: Path, tf_min: int) -> pd.DataFrame:
    """Per-session aggregates (kept for the desk emitter)."""
    base = load_ohlcv_parquet(path)
    df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; lo = df["low"]; c = df["close"]
    qv = df["quote_volume"]; tc = df["trade_count"]
    if len(ts) < 200:
        return pd.DataFrame()
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    rows = []
    for d, s, st, en in _block_runs(day, seq):
        if en - st < 2:
            continue
        op = float(o[st]); cl = float(c[en - 1]); hi = float(h[st:en].max()); low = float(lo[st:en].min())
        rows.append({"start_ts": int(ts[st]), "end_ts": int(ts[en - 1]), "seq": int(s),
                     "open": op, "close": cl, "high": hi, "low": low,
                     "ret": cl / op - 1.0 if op > 0 else 0.0, "down": int(cl < op),
                     "range_pct": (hi - low) / op if op > 0 else 0.0,
                     "qv": float(qv[st:en].sum()), "tc": float(tc[st:en].sum())})
    return pd.DataFrame(rows)


def btc_session_index(tf_min: int) -> dict:
    """{(utc_day, seq): (btc_trade_count, btc_quote_volume)} for each BTC session --
    the market benchmark to normalise a symbol's per-session activity against."""
    p = CACHE / "BTCUSDT.parquet"
    if not p.exists():
        return {}
    base = load_ohlcv_parquet(p)
    df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    ts = df["timestamp"]; qv = df["quote_volume"]; tc = df["trade_count"]
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    idx = {}
    for d, s, st, en in _block_runs(day, seq):
        idx[(int(d), int(s))] = (float(tc[st:en].sum()), float(qv[st:en].sum()))
    return idx


_BTC: dict = {}
_MKT_TS = None
_MKT_VALS: dict = {}


def _init_worker(btc, mkt_ts, mkt_vals):
    global _BTC, _MKT_TS, _MKT_VALS
    _BTC = btc; _MKT_TS = mkt_ts; _MKT_VALS = mkt_vals


def _overlap(a_lo, a_hi, b_lo, b_hi):
    """Fraction of the smaller range that overlaps the other range's price span."""
    inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
    span = min(a_hi - a_lo, b_hi - b_lo)
    return inter / span if span > 0 else 0.0


def build_symbol(path: Path, tf_min: int, kmax: int, btc_idx: dict | None = None) -> pd.DataFrame:
    btc_idx = btc_idx if btc_idx is not None else _BTC
    base = load_ohlcv_parquet(path)
    df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; lo = df["low"]; c = df["close"]
    qv = df["quote_volume"]; tc = df["trade_count"]
    taker = df.get("taker_buy_quote_volume"); oi = df.get("open_interest")
    n = len(ts)
    if n < 400:
        return pd.DataFrame()
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    ema = pd.Series(c).ewm(span=200, adjust=False).mean().to_numpy()
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = [(d, s, st, en) for (d, s, st, en) in _block_runs(day, seq) if en - st >= 3]

    # per-session rich record
    S = []
    for d, s, st, en in runs:
        op = float(o[st]); cl = float(c[en - 1]); hi = float(h[st:en].max()); low = float(lo[st:en].min())
        rng = hi - low; body = cl - op
        bars_ret = np.abs(np.diff(c[st:en]))
        path_len = float(bars_ret.sum()) + 1e-12
        base_qv = np.median(qv[max(0, st - 96):st]) + 1e-9
        base_tc = np.median(tc[max(0, st - 96):st]) + 1e-9
        tk = float(np.nanmean(taker[st:en]) / (qv[st:en].mean() + 1e-9)) if taker is not None else np.nan
        oichg = (float(oi[en - 1]) / float(oi[st]) - 1.0) if (oi is not None and np.isfinite(oi[st]) and oi[st] > 0) else np.nan
        btc = btc_idx.get((int(d), int(s))) if btc_idx else None
        rel_trades = (float(tc[st:en].sum()) / btc[0]) if (btc and btc[0] > 0) else np.nan   # this coin's trades vs BTC, same session
        rel_vol = (float(qv[st:en].sum()) / btc[1]) if (btc and btc[1] > 0) else np.nan
        S.append({
            "d": int(d), "seq": int(s), "st": st, "en": en, "st_ts": int(ts[st]),
            "open": op, "close": cl, "high": hi, "low": low,
            "ret": cl / op - 1.0 if op > 0 else 0.0, "down": int(cl < op), "up": int(cl > op),
            "range_pct": rng / op if op > 0 else 0.0, "range_atr": rng / atr[st] if atr[st] > 0 else 0.0,
            "body_frac": abs(body) / rng if rng > 0 else 0.0,                 # cleanliness: body vs range
            "close_pos": (cl - low) / rng if rng > 0 else 0.5,               # close near high(1)/low(0)
            "upper_wick": (hi - max(op, cl)) / rng if rng > 0 else 0.0,
            "lower_wick": (min(op, cl) - low) / rng if rng > 0 else 0.0,
            "efficiency": abs(cl - op) / path_len,                            # directed move / path travelled
            "green_frac": float((c[st:en] > o[st:en]).mean()),
            "qv": float(qv[st:en].sum()), "tc": float(tc[st:en].sum()),
            "rvol": float(qv[st:en].sum()) / (base_qv * (en - st)),
            "rtrades": float(tc[st:en].sum()) / (base_tc * (en - st)),
            "taker": tk, "oichg": oichg, "rel_trades": rel_trades, "rel_vol": rel_vol,
            # context at the session OPEN uses only the last CLOSED bar (st-1): no peek at bar st
            "atr_pct": atr[max(0, st - 1)] / op if op > 0 else 0.0,
            "ema_dist": (op - ema[max(0, st - 1)]) / atr[max(0, st - 1)] if atr[max(0, st - 1)] > 0 else 0.0,
            "trend20": (c[max(0, st - 1)] / c[max(0, st - 21)] - 1.0),
        })
    if len(S) < 30:
        return pd.DataFrame()
    sf = pd.DataFrame(S)

    out = []
    for direction, dirname in ((-1, "short"), (+1, "long")):
        col = "down" if direction < 0 else "up"
        for seq, g in sf.groupby("seq"):
            g = g.sort_values("st_ts").reset_index(drop=True)
            flag = g[col].to_numpy()
            run = 0
            for i in range(1, len(g)):
                run = run + 1 if flag[i - 1] == 1 else 0
                k = run
                if k < 2:
                    continue
                j0 = i - k                          # first streak session
                st_slice = g.iloc[j0:i]             # the k streak sessions
                ev = g.iloc[i]                       # the event session (trade)
                rets = st_slice.ret.to_numpy()
                # --- streak shape ---
                cum = float(np.prod(1 + rets) - 1)
                consistency = float(np.std(rets) / (abs(np.mean(rets)) + 1e-9))
                accel = float(rets[-1] - rets[0])
                # monotonic new-extreme closes (lower-lows for short / higher-highs for long)
                closes = st_slice.close.to_numpy()
                mono = float(np.mean(np.diff(closes) < 0)) if direction < 0 else float(np.mean(np.diff(closes) > 0))
                # consecutive range overlap (choppy=high overlap, clean trend=low)
                los = st_slice.low.to_numpy(); his = st_slice.high.to_numpy()
                ov = [_overlap(los[q], his[q], los[q + 1], his[q + 1]) for q in range(k - 1)]
                avg_overlap = float(np.mean(ov)) if ov else 0.0
                # session-to-session gaps (open vs prev close)
                opens = st_slice.open.to_numpy()
                gaps = [(opens[q + 1] - closes[q]) / closes[q] for q in range(k - 1)]
                avg_gap = float(np.mean(gaps)) if gaps else 0.0
                # --- intermediate-session range expansion (bars between streak start and event open) ---
                a = int(st_slice.iloc[0].st); b = int(ev.st)
                streak_hi = float(st_slice.high.max()); streak_lo = float(st_slice.low.min())
                seg_hi = float(h[a:b].max()); seg_lo = float(lo[a:b].min())
                srng = (streak_hi - streak_lo) + 1e-12
                exp_up = (seg_hi - streak_hi) / srng     # >0 => intermediate sessions pushed above the streak
                exp_dn = (streak_lo - seg_lo) / srng     # >0 => intermediate sessions pushed below the streak
                # WHERE the event session OPENS relative to the prior ranges
                pb = max(0, b - 1)                       # last CLOSED bar before the event open
                a_ev = atr[pb] if atr[pb] > 0 else max(ev.open * 0.005, 1e-9)
                last_lo = float(st_slice.low.iloc[-1]); last_hi = float(st_slice.high.iloc[-1])
                open_pos_streak = (ev.open - streak_lo) / srng            # 0=streak low, 1=streak high, <0/>1 outside
                open_pos_last = (ev.open - last_lo) / ((last_hi - last_lo) + 1e-12)   # position in last session's range
                # continuation vs pullback: how far the open already extends BEYOND the streak in the trade direction
                open_beyond_atr = ((streak_lo - ev.open) if direction < 0 else (ev.open - streak_hi)) / a_ev
                # --- OPEN INTEREST over the whole streak window (causal, up to the event open) ---
                oi_win_trend = ((float(oi[pb]) / float(oi[a]) - 1.0)
                                if (oi is not None and np.isfinite(oi[a]) and oi[a] > 0 and np.isfinite(oi[pb])) else np.nan)
                oi_trend = float(st_slice.oichg.iloc[-1] - st_slice.oichg.iloc[0])
                oi_last = float(st_slice.oichg.iloc[-1])
                oi_accel = float(st_slice.oichg.iloc[-1] - st_slice.oichg.iloc[0])
                # OI building WITH the move (down-streak + OI up = new shorts piling in; up-streak + OI up = longs)
                oi_with_move = float(np.nanmean(st_slice.oichg.to_numpy()))   # raw; sign read together with direction
                # --- more volatility / trendiness ---
                atr_trend = (atr[pb] / atr[a] - 1.0) if atr[a] > 0 else 0.0
                cum_move_atr = abs(cum) / (float(ev.atr_pct) + 1e-9)
                sum_rng = float(st_slice.range_pct.sum()) + 1e-12
                net_vs_range = abs(cum) / sum_rng                            # streak-level trendiness
                counter = rets if direction > 0 else -rets                  # >0 = with trend
                max_counter = float(max(0.0, -counter.min()))               # biggest against-trend session
                if direction < 0:
                    los2 = st_slice.low.to_numpy()
                    prev = np.concatenate([[np.inf], np.minimum.accumulate(los2)[:-1]])
                    new_ext = float(np.mean(los2 <= prev))
                else:
                    his2 = st_slice.high.to_numpy()
                    prev = np.concatenate([[-np.inf], np.maximum.accumulate(his2)[:-1]])
                    new_ext = float(np.mean(his2 >= prev))
                # --- participation micro-structure ---
                vol_last_vs_avg = float(st_slice.rvol.iloc[-1] / (st_slice.rvol.mean() + 1e-9))
                trades_per_vol = float(st_slice.tc.mean() / (st_slice.qv.mean() + 1e-9))   # small vs large traders
                gap_last = float((ev.open - st_slice.close.iloc[-1]) / (st_slice.close.iloc[-1] + 1e-12))
                min_overlap = float(np.min(ov)) if ov else 0.0
                max_overlap = float(np.max(ov)) if ov else 0.0
                row = {
                    "symbol": path.stem, "seq": int(seq), "start_ts": int(ev.st_ts),
                    "week": pd.Timestamp(ev.st_ts, unit="ms", tz="UTC").strftime("%G-W%V"),
                    "direction": direction, "streak_len": int(k),
                    # streak shape
                    "streak_ret": cum, "avg_ret": float(np.mean(rets)), "last_ret": float(rets[-1]),
                    "accel": accel, "consistency": consistency, "mono": mono,
                    "avg_body_frac": float(st_slice.body_frac.mean()), "avg_close_pos": float(st_slice.close_pos.mean()),
                    "avg_range_atr": float(st_slice.range_atr.mean()), "range_trend": float(st_slice.range_atr.iloc[-1] - st_slice.range_atr.iloc[0]),
                    "avg_efficiency": float(st_slice.efficiency.mean()), "avg_green": float(st_slice.green_frac.mean()),
                    "avg_overlap": avg_overlap, "avg_gap": avg_gap,
                    # volume / participation shape
                    "avg_rvol": float(st_slice.rvol.mean()), "vol_trend": float(st_slice.rvol.iloc[-1] - st_slice.rvol.iloc[0]),
                    "avg_rtrades": float(st_slice.rtrades.mean()), "tc_trend": float(st_slice.rtrades.iloc[-1] - st_slice.rtrades.iloc[0]),
                    "avg_taker": float(np.nanmean(st_slice.taker)), "taker_trend": float(st_slice.taker.iloc[-1] - st_slice.taker.iloc[0]),
                    "avg_oichg": float(np.nanmean(st_slice.oichg)),
                    # intermediate expansion
                    "exp_up": exp_up, "exp_dn": exp_dn,
                    # WHERE price opens the event session relative to prior ranges
                    "open_pos_streak": open_pos_streak, "open_pos_last": open_pos_last,
                    "open_beyond_atr": open_beyond_atr,
                    # open interest
                    "oi_win_trend": oi_win_trend, "oi_trend": oi_trend, "oi_last": oi_last,
                    "oi_accel": oi_accel, "oi_with_move": oi_with_move,
                    # volatility / trendiness / structure
                    "atr_trend": atr_trend, "cum_move_atr": cum_move_atr, "net_vs_range": net_vs_range,
                    "max_counter": max_counter, "new_ext": new_ext,
                    "vol_last_vs_avg": vol_last_vs_avg, "trades_per_vol": trades_per_vol,
                    "gap_last": gap_last, "min_overlap": min_overlap, "max_overlap": max_overlap,
                    # activity RELATIVE TO BTC in the same sessions (rotation / attention)
                    "avg_rel_trades": float(np.nanmean(st_slice.rel_trades)),
                    "rel_trades_trend": float(st_slice.rel_trades.iloc[-1] - st_slice.rel_trades.iloc[0]),
                    "last_rel_trades": float(st_slice.rel_trades.iloc[-1]),
                    "avg_rel_vol": float(np.nanmean(st_slice.rel_vol)),
                    "rel_vol_trend": float(st_slice.rel_vol.iloc[-1] - st_slice.rel_vol.iloc[0]),
                    # last-session emphasis
                    "last_body_frac": float(st_slice.body_frac.iloc[-1]), "last_close_pos": float(st_slice.close_pos.iloc[-1]),
                    "last_rvol": float(st_slice.rvol.iloc[-1]), "last_range_atr": float(st_slice.range_atr.iloc[-1]),
                    # context at the event open
                    "atr_pct": float(ev.atr_pct), "ema_dist": float(ev.ema_dist), "trend20": float(ev.trend20),
                    "cap": float(np.log10(st_slice.qv.mean() + 1)), "dow": pd.Timestamp(ev.st_ts, unit="ms", tz="UTC").dayofweek,
                    # OUTCOME: one-session directional hold (open->close), no stop
                    "cont": int(ev.down if direction < 0 else ev.up),
                    "trade_ret": float(-ev.ret if direction < 0 else ev.ret),
                    "ev_ret": float(ev.ret),
                }
                # market-wide regime as of the most recent completed session before the event
                mk = market_asof(_MKT_TS, _MKT_VALS, int(ev.st_ts)) if _MKT_TS is not None else {}
                row.update(mk)
                sgn = -1 if direction < 0 else 1
                row["mkt_alt_align"] = mk.get("mkt_alt_ret_med", np.nan) * sgn if mk else np.nan   # regime tailwind for the trade
                row["mkt_btc_align"] = mk.get("mkt_btc_ret", np.nan) * sgn if mk else np.nan
                out.append(row)
    return pd.DataFrame(out)


def _one(a):
    p, tf, kmax = a
    try:
        return build_symbol(Path(p), tf, kmax)
    except Exception:
        return pd.DataFrame()


FEATS = ["streak_len", "streak_ret", "avg_ret", "last_ret", "accel", "consistency", "mono",
         "avg_body_frac", "avg_close_pos", "avg_range_atr", "range_trend", "avg_efficiency", "avg_green",
         "avg_overlap", "avg_gap", "avg_rvol", "vol_trend", "avg_rtrades", "tc_trend", "avg_taker",
         "taker_trend", "avg_oichg", "exp_up", "exp_dn", "open_pos_streak", "open_pos_last",
         "open_beyond_atr", "last_body_frac", "last_close_pos",
         "last_rvol", "last_range_atr", "atr_pct", "ema_dist", "trend20", "cap", "dow",
         # open interest
         "oi_win_trend", "oi_trend", "oi_last", "oi_accel", "oi_with_move",
         # volatility / trendiness / structure
         "atr_trend", "cum_move_atr", "net_vs_range", "max_counter", "new_ext",
         "vol_last_vs_avg", "trades_per_vol", "gap_last", "min_overlap", "max_overlap",
         # activity relative to BTC in the same sessions
         "avg_rel_trades", "rel_trades_trend", "last_rel_trades", "avg_rel_vol", "rel_vol_trend",
         # market-wide regime (alt backdrop + BTC) + alignment with the trade direction
         *["mkt_" + c for c in MKT_COLS], "mkt_alt_align", "mkt_btc_align"]


def _oof(d):
    X = d[FEATS].to_numpy(float); y = d.cont.to_numpy(int); wk = d.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = CatBoostClassifier(depth=5, iterations=400, learning_rate=0.03, l2_leaf_reg=6,
                               verbose=False, random_seed=0)
        m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--kmax", type=int, default=8)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/streak.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    btc_idx = btc_session_index(args.tf)
    mkt_path = Path(f".output/results/session_break/market_index_tf{args.tf}.parquet")
    mkt_ts, mkt_vals = load_market_arrays(mkt_path) if mkt_path.exists() else (None, {})
    print(f"tf={args.tf}m symbols={len(files)} feats={len(FEATS)} btc_sessions={len(btc_idx)} "
          f"mkt_sessions={0 if mkt_ts is None else len(mkt_ts)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(btc_idx, mkt_ts, mkt_vals)) as ex:
        futs = {ex.submit(_one, (f, args.tf, args.kmax)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} events={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    dev = t[t.start_ts < DEV_END].copy()
    print(f"\nwrote {len(t):,} events ({len(dev):,} DEV)\n")

    for direction, dirname in ((-1, "SHORT (down-streak)"), (+1, "LONG (up-streak)")):
        dd = dev[dev.direction == direction].copy()
        print(f"\n######## {dirname}  n={len(dd):,}  base cont={dd.cont.mean():.3f} "
              f"mean trade_ret={dd.trade_ret.mean()*1e4:+.1f}bp ########")
        print(f"{'k':>4}{'n':>8}{'P(cont)':>8}{'raw_ret':>9}{'AUC':>6}{'topDec_ret':>11}{'topDec_P':>9}{'net@8bp':>9}{'pos_wk':>7}")
        for k in range(2, args.kmax + 1):
            s = dd[dd.streak_len == k] if k < args.kmax else dd[dd.streak_len >= k]
            if len(s) < 300:
                continue
            o = _oof(s.dropna(subset=["week"]))
            s = s.iloc[:len(o)].copy(); s["p"] = o
            mk = np.isfinite(s.p)
            s = s[mk]
            if len(s) < 200:
                continue
            auc = roc_auc_score(s.cont, s.p) if s.cont.nunique() > 1 else np.nan
            thr = np.nanpercentile(s.p, 90)
            td = s[s.p >= thr]
            twk = td.groupby("week").trade_ret.mean()
            net = td.trade_ret.mean() * 1e4 - COST_BP
            lbl = f">={k}" if k == args.kmax else str(k)
            print(f"{lbl:>4}{len(s):>8}{s.cont.mean():>8.3f}{s.trade_ret.mean()*1e4:>+8.1f}b{auc:>6.3f}"
                  f"{td.trade_ret.mean()*1e4:>+10.1f}b{td.cont.mean():>9.3f}{net:>+8.1f}b{(twk>0).mean():>7.2f}")

    # global discrimination + importances (both directions pooled, direction as a feature would leak; keep split)
    print("\n--- top feature importances (short, streak>=3) ---")
    dd = dev[(dev.direction == -1) & (dev.streak_len >= 3)].dropna(subset=["week"])
    X = dd[FEATS].to_numpy(float); y = dd.cont.to_numpy(int)
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    imp = pd.Series(CatBoostClassifier(depth=5, iterations=400, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(X, y).get_feature_importance(), index=FEATS).sort_values(ascending=False)
    print(imp.head(15).to_string(float_format=lambda x: f"{x:.1f}"))


if __name__ == "__main__":
    main()
