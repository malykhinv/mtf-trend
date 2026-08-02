"""SESSION-ANCHORED RR=1 fade-vs-newhigh race (user hypothesis 2026-07-31).

Setup, strictly causal, session-anchored (blocks = ASIA/EU/OVERLAP/US/LATE):
  1. the PRIOR session P printed an anomaly: a clean LONG pump >= PUMP_MIN (peak vs
     the base = lowest low at/ before the peak) AND quote-volume & trade-count both
     >= VOL_MULT x the median of several same-type-session lookbacks (6/9/12/16);
  2. the pump is a SINGLE ascending cycle (one saw, not a meander): high path
     efficiency and at most one significant retrace between base and peak;
  3. the CURRENT session Sc opens NEAR the high (open within NEAR_HIGH_MAX of P's peak);
  4. from Sc's open we monitor OI, taker-buy (buyer pressure), volume, trades and
     price; the FADE-ONSET trigger fires on the first bar where long sentiment rolls
     over -- price off the session high AND taker-buy EMA decaying AND OI below its
     session peak (leverage starting to unwind);
  5. at the trigger we run a SYMMETRIC RR=1 race: reward = 0.5 * pump. Down target =
     entry*(1-reward) ("fade to the middle of the anomalous growth"); up target =
     entry*(1+reward) ("a new high of the same size"). y=1 iff the DOWN target hits
     first. Because RR=1, the whole edge is WIN RATE -- a model with AUC>0.5 lets you
     pick the confident side on either tail.

NOVEL vs prior fade/RR=1 work (session-break-discovery, pump_fade_rr1): (a) OI
trajectory features -- OI is now dense for the full year; (b) the intraday
fade-onset trigger as the entry gate (not a fixed structure break); (c) the
single-cycle cleanliness gate. Everything else (RR=1 label, GroupKFold-by-week
catboost, shuffled null, week stability, DEV/OOS split) mirrors the frozen battery.

DEV only (start_ts < 2026-01-01); OOS is held out and NOT touched here.
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

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.reclaim import _block_runs, _resample
from anomaly_science.strategy.session_break.research.sessions import (
    BLOCK_BY_SEQ, block_seq_for_ms, utc_day_for_ms,
)

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

# --- setup gates ---
TF = 5                       # minutes; matches Binance OI cadence
PUMP_MIN = 0.20              # >= 20% clean long move in the prior session
VOL_MULT = 5.0              # quote-vol & trade-count both >= 5x baseline...
LOOKBACKS = (6, 9, 12, 16)  # ...vs the median of each same-type-session lookback (strictest = min ratio)
NEAR_HIGH_MAX = 0.12        # current session opens within 12% below the pump peak
MAXDD_FRAC = 0.55           # single-cycle: deepest retrace <= 55% of the TOTAL run
MAX_EPISODES = 2            # ...and at most two pullback episodes exceeding RETRACE_FRAC
RETRACE_FRAC = 0.20
# --- fade-onset trigger ---
WARMUP = 3                  # bars into the current session before a trigger may fire
TAKER_FAST, TAKER_SLOW = 3, 9
# --- race ---
HORIZON = 96                # 5m bars (~8h) to resolve the RR=1 race (reward can be >=10%)
REWARD_MIN = 0.03
COST_BP = 8.0

FEATS = [
    # prior-session anomaly / geometry
    "pump", "vol_ratio", "tc_ratio", "same_vol_z", "efficiency", "n_retrace",
    "p_close_pos", "p_upper_wick", "p_taker", "p_atr_pct", "cap",
    "p_oi_build",                       # OI growth across the pump session (new leverage in)
    # setup geometry into the current session
    "gap_to_peak", "bars_into_session", "reward_frac",
    # intraday state AT the fade-onset trigger (all causal)
    "entry_vs_peak", "dd_from_sc_high", "ret_since_sc_open", "ret_30m",
    "oi_drop_from_peak", "oi_slope_30m", "oi_vs_open",
    "taker_now", "taker_trend", "cvd_dd_from_peak", "cvd_slope_30m",
    "vol_vs_sess", "tc_trend", "avg_trade_size_z",
]


def _ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def build_symbol(path: Path, g: dict) -> pd.DataFrame:
    raw = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "quote_volume",
        "trade_count", "taker_buy_quote_volume", "open_interest"])
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, TF)
    n = len(df)
    if n < 400:
        return pd.DataFrame()
    ts = df["timestamp"].to_numpy(np.int64)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); tc = df["trade_count"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    oi = df["open_interest"].to_numpy(float)
    atr = causal_atr(high=h, low=lo, close=c, window=64)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
        avg_ts = np.where(tc > 0, qv / tc, 0.0)     # average trade size (USDT)
    cvd = np.cumsum((2 * taker - 1) * qv)           # signed taker flow, running
    taker_fast = _ema(taker, TAKER_FAST); taker_slow = _ema(taker, TAKER_SLOW)

    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = _block_runs(day, seq)                    # ordered (day, seq, start, end) sessions
    if len(runs) < max(LOOKBACKS) + 3:
        return pd.DataFrame()

    # per-session aggregates for the volume/trade baselines
    S = []
    for d, s, st, en in runs:
        if en - st < 3:
            S.append(None); continue
        S.append({"seq": s, "st": st, "en": en, "qv": float(qv[st:en].sum()),
                  "tc": float(tc[st:en].sum())})

    out = []
    for i in range(max(LOOKBACKS) + 1, len(runs) - 1):
        P = S[i - 1]; Sc = S[i]                      # prior (pump) and current (trade) sessions
        if P is None or Sc is None:
            continue
        pst, pen = P["st"], P["en"]; sst, sen = Sc["st"], Sc["en"]
        pseq = P["seq"]

        # --- (1) clean pump in P: peak and the base = lowest low at/before the peak ---
        pk_rel = int(np.argmax(h[pst:pen])); pk = pst + pk_rel
        base_rel = int(np.argmin(lo[pst:pk + 1])); base_i = pst + base_rel
        base = float(lo[base_i]); peak = float(h[pk])
        if base <= 0 or pk <= base_i:
            continue
        pump = (peak - base) / base
        if pump < g["pump_min"]:
            continue

        # --- (2) volume & trade anomaly vs same-type lookbacks (strictest = min ratio) ---
        same_prior = [S[j] for j in range(i - 1) if S[j] is not None and S[j]["seq"] == pseq]
        if len(same_prior) < max(LOOKBACKS):
            continue
        qv_hist = np.array([x["qv"] for x in same_prior])
        tc_hist = np.array([x["tc"] for x in same_prior])
        vol_ratios = [P["qv"] / (np.median(qv_hist[-k:]) + 1e-9) for k in LOOKBACKS]
        tc_ratios = [P["tc"] / (np.median(tc_hist[-k:]) + 1e-9) for k in LOOKBACKS]
        vol_ratio = float(min(vol_ratios)); tc_ratio = float(min(tc_ratios))
        if vol_ratio < g["vol_mult"] or tc_ratio < g["vol_mult"]:
            continue
        logq = np.log(qv_hist[-max(LOOKBACKS):] + 1)
        same_vol_z = float((np.log(P["qv"] + 1) - logq.mean()) / (logq.std() + 1e-9))

        # --- (2b) single-cycle cleanliness between base and peak (retrace vs TOTAL run) ---
        run = peak - base
        path_len = float(np.abs(np.diff(c[base_i:pk + 1])).sum())
        efficiency = run / (path_len + 1e-9)                          # kept as a feature
        run_max = np.maximum.accumulate(h[base_i:pk + 1])
        dd = (run_max - lo[base_i:pk + 1]) / run                      # retrace depth vs total run
        maxdd_frac = float(dd.max())
        above = (dd > RETRACE_FRAC).astype(int)
        n_retrace = int((np.diff(above) == 1).sum() + (1 if above[0] else 0))
        if maxdd_frac > g["maxdd"] or n_retrace > g["max_ep"]:
            continue

        # --- (3) current session opens near the high ---
        sc_open = float(o[sst])
        gap_to_peak = (peak - sc_open) / peak
        if not (0 <= gap_to_peak <= g["near"]):
            continue

        # prior-session descriptive features
        prng = peak - float(lo[pst:pen].min())
        p_close_pos = (float(c[pen - 1]) - float(lo[pst:pen].min())) / (prng + 1e-9)
        p_upper_wick = (peak - max(float(o[pst]), float(c[pen - 1]))) / (prng + 1e-9)
        p_taker = float(np.nanmean(taker[pst:pen]))
        p_atr_pct = float(atr[pst] / sc_open) if sc_open > 0 else 0.0
        cap = float(np.log10(P["qv"] + 1))
        p_oi_build = float(oi[pen - 1] / (oi[pst] + 1e-9) - 1.0)

        # --- (4) monitor the current session; first fade-onset trigger ---
        sc_hi = h[sst]; oi_peak = oi[sst]; trig = None
        for t in range(sst + 1, sen):
            sc_hi = max(sc_hi, h[t]); oi_peak = max(oi_peak, oi[t])
            if t - sst < WARMUP:
                continue
            off_high = c[t] < sc_hi                                   # came off the session high
            buyer_decay = taker_fast[t] < taker_slow[t]               # buyer pressure fading
            oi_rollover = oi[t] < oi_peak                             # leverage unwinding
            if off_high and buyer_decay and oi_rollover:
                trig = t; break
        if trig is None or trig + g["hor"] + 1 >= n:
            continue

        reward = 0.5 * pump
        if reward < REWARD_MIN:
            continue
        entry = float(c[trig])
        tgt_dn = entry * (1 - reward); tgt_up = entry * (1 + reward)
        res = None
        for j in range(trig + 1, min(trig + g["hor"] + 1, n)):
            hit_dn = lo[j] <= tgt_dn; hit_up = h[j] >= tgt_up
            if hit_dn and hit_up:                                    # both in one bar -> ambiguous, drop
                res = None; break
            if hit_dn:
                res = 1; break
            if hit_up:
                res = 0; break
        if res is None:
            continue

        # --- intraday features AT the trigger (causal window = session open..trigger) ---
        w = slice(sst, trig + 1)
        back30 = max(sst, trig - 6)                                   # 6 bars * 5m = 30m
        oi_peak_val = float(np.max(oi[w]))
        cvd_w = cvd[w]; cvd_peak = float(np.max(cvd_w))
        sess_qv_mean = float(np.mean(qv[w]))
        sess_ats_mean = float(np.mean(avg_ts[w])) + 1e-9
        out.append({
            "symbol": path.stem, "entry_ts": int(ts[trig]),
            "week": pd.Timestamp(int(ts[trig]), unit="ms", tz="UTC").strftime("%G-W%V"),
            "session": BLOCK_BY_SEQ[int(Sc["seq"])].name,
            "pair": f"{BLOCK_BY_SEQ[int(pseq)].name}->{BLOCK_BY_SEQ[int(Sc['seq'])].name}",
            "y": int(res),
            # setup
            "pump": pump, "vol_ratio": vol_ratio, "tc_ratio": tc_ratio, "same_vol_z": same_vol_z,
            "efficiency": efficiency, "n_retrace": n_retrace, "p_close_pos": p_close_pos,
            "p_upper_wick": p_upper_wick, "p_taker": p_taker, "p_atr_pct": p_atr_pct, "cap": cap,
            "p_oi_build": p_oi_build, "gap_to_peak": gap_to_peak,
            "bars_into_session": trig - sst, "reward_frac": reward,
            # intraday at trigger
            "entry_vs_peak": (peak - entry) / peak,
            "dd_from_sc_high": (sc_hi - entry) / (sc_hi + 1e-9),
            "ret_since_sc_open": entry / sc_open - 1.0,
            "ret_30m": entry / float(c[back30]) - 1.0,
            "oi_drop_from_peak": oi[trig] / (oi_peak_val + 1e-9) - 1.0,
            "oi_slope_30m": oi[trig] / (oi[back30] + 1e-9) - 1.0,
            "oi_vs_open": oi[trig] / (oi[sst] + 1e-9) - 1.0,
            "taker_now": float(np.mean(taker[back30:trig + 1])),
            "taker_trend": float(taker_fast[trig] - taker_slow[trig]),
            "cvd_dd_from_peak": (cvd[trig] - cvd_peak) / (abs(cvd_peak) + 1e-9),
            "cvd_slope_30m": (cvd[trig] - cvd[back30]) / (abs(cvd_peak) + 1e-9),
            "vol_vs_sess": float(qv[trig]) / (sess_qv_mean + 1e-9),
            "tc_trend": float(tc[trig]) / (float(np.mean(tc[w])) + 1e-9),
            "avg_trade_size_z": float(avg_ts[trig]) / sess_ats_mean,
        })
    return pd.DataFrame(out)


def _one(args):
    f, g = args
    try:
        return build_symbol(Path(f), g)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def evaluate(t: pd.DataFrame) -> None:
    d = t[t.entry_ts < DEV_END].copy()
    print(f"\n=== DEV setups: n={len(d):,}  base fade-first rate={d.y.mean():.3f} (RR=1 => 0.5 is coinflip) ===")
    print(f"    symbols={d.symbol.nunique()}  weeks={d.week.nunique()}")
    print("  by session-pair:")
    for p, g in d.groupby("pair"):
        if len(g) >= 40:
            print(f"    {p:<16} n={len(g):>5} fade-first={g.y.mean():.3f}")
    if len(d) < 150:
        print("  (too few for a stable model)"); return

    use = [c for c in FEATS if c in d.columns]
    X = d[use].to_numpy(float); y = d.y.to_numpy(int); wk = d.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])

    def run(yv):
        o = np.full(len(yv), np.nan)
        for a, b in GroupKFold(5).split(X, yv, wk):
            m = CatBoostClassifier(depth=5, iterations=500, learning_rate=0.03,
                                   l2_leaf_reg=6, verbose=False, random_seed=0)
            m.fit(X[a], yv[a]); o[b] = m.predict_proba(X[b])[:, 1]
        return o

    oof = run(y)
    rng = np.random.default_rng(0)
    yn = d.groupby("week").y.transform(lambda s: rng.permutation(s.values)).to_numpy(int)
    oofn = run(yn)
    mk = np.isfinite(oof) & np.isfinite(oofn)
    print(f"\n=== week-CV AUC={roc_auc_score(y[mk], oof[mk]):.3f}  vs shuffled null={roc_auc_score(yn[mk], oofn[mk]):.3f} ===")

    s = d[mk].assign(p=oof[mk])
    print("  CONFIDENT-FADE tail (short: win = fade-first), net after 2x cost:")
    for q, lbl in [(0.7, "top30%"), (0.8, "top20%"), (0.9, "top10%")]:
        sel = s[s.p >= s.p.quantile(q)]
        wk2 = sel.groupby("week").y.mean()
        net = (2 * sel.y.mean() - 1) * sel.reward_frac.mean() * 1e4 - COST_BP
        print(f"    {lbl}: n={len(sel):>5} fade-win={sel.y.mean():.3f} posWk={(wk2>0.5).mean()*100:>3.0f}% net~{net:>+6.1f}bp")
    print("  CONFIDENT-NEWHIGH tail (long: win = newhigh-first):")
    for q, lbl in [(0.3, "bot30%"), (0.2, "bot20%"), (0.1, "bot10%")]:
        sel = s[s.p <= s.p.quantile(q)]
        wk2 = sel.groupby("week").y.mean()
        net = (2 * (1 - sel.y.mean()) - 1) * sel.reward_frac.mean() * 1e4 - COST_BP
        print(f"    {lbl}: n={len(sel):>5} newhigh-win={1-sel.y.mean():.3f} posWk={(wk2<0.5).mean()*100:>3.0f}% net~{net:>+6.1f}bp")

    imp = pd.Series(CatBoostClassifier(depth=5, iterations=500, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(np.nan_to_num(d[use].to_numpy(float)), y).get_feature_importance(),
                    index=use).sort_values(ascending=False)
    print("  top features:", ", ".join(f"{k}={v:.1f}" for k, v in imp.head(14).items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/session_pump_rr1.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="cap symbols for a smoke test")
    ap.add_argument("--pump-min", type=float, default=PUMP_MIN)
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT)
    ap.add_argument("--maxdd", type=float, default=MAXDD_FRAC)
    ap.add_argument("--max-ep", type=int, default=MAX_EPISODES)
    ap.add_argument("--near", type=float, default=NEAR_HIGH_MAX)
    ap.add_argument("--hor", type=int, default=HORIZON)
    args = ap.parse_args()
    g = {"pump_min": args.pump_min, "vol_mult": args.vol_mult, "maxdd": args.maxdd,
         "max_ep": args.max_ep, "near": args.near, "hor": args.hor}
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"symbols={len(files)}  tf={TF}m  cfg={g}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, g)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 150 == 0:
                print(f"  {done}/{len(files)} setups={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(args.out)
    print(f"\nwrote {len(t):,} setups -> {args.out}")
    if len(t):
        evaluate(t)


if __name__ == "__main__":
    main()
