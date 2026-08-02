"""STAGE 3+4 -- reversal ENTRY triggers + fixed-RR causal outcome for sleep->dump setups.

On every expert-validated dump (from dump_review, sleep->dump gated) we place THREE
causal entry triggers after the climax low and race each to a fixed-RR outcome with the
stop just under the dump low. We compare the base buy-back rate / expectancy per trigger
so the winner is chosen from data, not guessed. Honest horizon: unresolved races are
marked as a TIME-STOP exit (exit at horizon close), never dropped.

Triggers (all causal, scanned forward from the climax low b):
  * reclaim  -- first green bar closing above the prior bar's high (micro higher-high);
  * flow     -- first green bar with taker-buy share > 0.5 AND OI no longer falling;
  * higher_low -- first confirmed swing low ABOVE the climax low (structure turn).

Outcome (RR fixed): stop = climax_low*(1-STOP_BUF); entry = close[trigger];
target = entry + RR*(entry-stop). y=1 iff target-first before stop. R = +RR / -1 / mark
at horizon close. Setups where price breaks the stop BEFORE a trigger fires are
'invalidated' (the knife kept falling) and excluded from the traded set but counted.

DEV only. OOS untouched.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol, CONFIRM_BARS
from anomaly_science.strategy.session_break.research.reclaim import _resample
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
DAY_BARS = 288                 # 1 day at 5m

_BTC = {}                      # per-process cache: {"ts":..., "close":...}


def _ema(x, span):
    a = 2.0 / (span + 1.0); out = np.empty_like(x, float); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _btc():
    """Lazy per-process BTC 5m series for concurrent-market (beta) features."""
    if "ts" not in _BTC:
        p = CACHE / "BTCUSDT.parquet"
        try:
            raw = pd.read_parquet(p, columns=["timestamp", "open", "high", "low", "close",
                                              "quote_volume", "trade_count"]).sort_values("timestamp")
            df = _resample(raw.reset_index(drop=True), TF)
            _BTC["ts"] = df["timestamp"].to_numpy(np.int64); _BTC["close"] = df["close"].to_numpy(float)
        except (OSError, ValueError, KeyError):
            _BTC["ts"] = np.array([], np.int64); _BTC["close"] = np.array([], float)
    return _BTC["ts"], _BTC["close"]


def _btc_ret(t0_ms, t1_ms):
    bt, bc = _btc()
    if len(bt) < 2:
        return np.nan
    i0 = int(np.searchsorted(bt, t0_ms)); i1 = int(np.searchsorted(bt, t1_ms))
    if i0 <= 0 or i1 <= 0 or i0 >= len(bc) or i1 >= len(bc) or bc[i0] <= 0:
        return np.nan
    return float(bc[i1] / bc[i0] - 1.0)

TF = 5
RR = 2.0
STOP_BUF = 0.004           # stop this far below the climax low
TRIGGER_WIN = 24           # bars after the climax low to find the reversal trigger (~2h)
HORIZON = 96               # bars to resolve the trade (~8h)
PIVK = 2
TRIGGERS = ("reclaim", "flow", "higher_low")


def _find_trigger(kind, b, hi, lo, cl, op, taker, oi, n):
    end = min(b + TRIGGER_WIN + 1, n)
    if kind == "reclaim":
        for j in range(b + 1, end):
            if cl[j] > hi[j - 1] and cl[j] > op[j]:
                return j
        return None
    if kind == "flow":
        for j in range(b + 1, end):
            if taker[j] > 0.5 and cl[j] > op[j] and oi[j] >= oi[j - 1]:
                return j
        return None
    if kind == "higher_low":
        seg = min(TRIGGER_WIN + 4, n - b)
        if seg < 5:
            return None
        pl, _ = causal_pivots(hi[b:b + seg], lo[b:b + seg], PIVK)
        low_b = lo[b]
        for jj in range(3, seg):
            if np.isfinite(pl[jj]) and pl[jj] > low_b:
                return b + jj
        return None
    return None


def _race(e, entry, stop, hi, lo, cl, n):
    """RR-agnostic path summary: max favorable excursion (in R) reached STRICTLY before
    the stop, whether the stop was hit, and the horizon-close R. Any RR target is then
    resolved downstream via `resolve_rr` -- no re-detection needed to sweep RR."""
    risk = entry - stop
    we = min(e + HORIZON, n - 1)
    rmax = -np.inf; stop_hit = False
    for j in range(e + 1, we + 1):
        if lo[j] <= stop:                    # pessimistic: stop wins a same-bar tie
            stop_hit = True; break
        if hi[j] > rmax:
            rmax = hi[j]
    mfe_before_stop_R = (rmax - entry) / risk if np.isfinite(rmax) else 0.0
    close_R = (cl[we] - entry) / risk if risk > 0 else 0.0
    return float(mfe_before_stop_R), bool(stop_hit), float(close_R)


def resolve_rr(mfe_R, stop_hit, close_R, rr):
    """Outcome for a given RR from the stored path summary. y=1 iff target-first."""
    if mfe_R >= rr:
        return 1, float(rr)
    if stop_hit:
        return 0, -1.0
    return (1 if close_R >= rr else 0), float(close_R)   # time-stop exit


def build_symbol(path: Path) -> list[dict]:
    try:
        dumps = [d for d in _scan_symbol(path) if d.culmination_ms < DEV_END]
    except (OSError, ValueError, KeyError):
        return []
    if not dumps:
        return []
    raw = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "quote_volume",
        "trade_count", "taker_buy_quote_volume", "open_interest"])
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, TF)
    ts = df["timestamp"].to_numpy(np.int64)
    op = df["open"].to_numpy(float); hi = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    tc = df["trade_count"].to_numpy(float)
    cvd = np.cumsum((2 * taker - 1) * qv)
    atr = causal_atr(high=hi, low=lo, close=cl, window=64)
    base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    ema60 = _ema(cl, 60); ema240 = _ema(cl, 240)
    with np.errstate(invalid="ignore", divide="ignore"):
        ats = np.where(tc > 0, qv / tc, np.nan)          # average trade size (USDT)
    rng_bar = hi - lo
    seqs = block_seq_for_ms(ts)
    n = len(ts)
    prior_bottoms = np.array(sorted(int(np.searchsorted(ts, d.culmination_ms)) for d in dumps), dtype=np.int64)

    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms))
        if b <= 0 or b >= n - 5 or ts[b] != d.culmination_ms:
            continue
        low_b = lo[b]; top = d.pump_start_price
        week = pd.Timestamp(int(ts[b]), unit="ms", tz="UTC").strftime("%G-W%V")
        for kind in TRIGGERS:
            e = _find_trigger(kind, b, hi, lo, cl, op, taker, oi, n)   # e = SIGNAL bar
            row = {"symbol": d.symbol, "trigger": kind, "bottom_ts": int(ts[b]),
                   "week": week, "drop_pct": d.drop_pct, "sleep_range_pct": d.sleep_range_pct,
                   "top_spike_above_sleep_pct": d.top_spike_above_sleep_pct}
            # LOOK-AHEAD HARDENING: the bottom is only confirmable at b+CONFIRM_BARS, so no
            # entry may be decided before that; enter at the NEXT bar's OPEN; the stop is the
            # causal running-min low through the signal bar (never a forward-confirmed low).
            if e is None or e < b + CONFIRM_BARS or e + 1 >= n - 1:
                row.update({"entered": 0, "invalidated": 0, "y": np.nan, "R": np.nan})
                out.append(row); continue
            eb = e + 1                                                 # entry bar = next open
            stop = float(lo[b:e + 1].min()) * (1 - STOP_BUF)
            entry = float(op[eb])
            if entry <= stop:
                row.update({"entered": 0, "invalidated": 1, "y": np.nan, "R": np.nan})
                out.append(row); continue
            mfe_R, stop_hit, close_R = _race(e, entry, stop, hi, lo, cl, n)   # races from eb=e+1
            y, r = resolve_rr(mfe_R, stop_hit, close_R, RR)
            row.update({"entered": 1, "invalidated": 0, "y": int(y), "R": float(r),
                        "mfe_R": mfe_R, "stop_hit": int(stop_hit), "close_R": close_R,
                        "bars_to_entry": e - b, "risk_pct": (entry - stop) / entry})
            row.update(_features(d, b, e, float(cl[e]), low_b, top, ts, op, hi, lo, cl, qv, tc,
                                  taker, cvd, oi, atr, base_qv, ema60, ema240, ats, rng_bar,
                                  seqs, prior_bottoms, n))
            out.append(row)
    return out


def _features(d, b, e, px, low_b, top, ts, op, hi, lo, cl, qv, tc, taker, cvd, oi,
              atr, base_qv, ema60, ema240, ats, rng_bar, seqs, prior_bottoms, n):
    """EXHAUSTIVE causal market-logical feature grid, evaluated at the SIGNAL bar e using
    ONLY data <= e. `px` = close of the signal bar (the price known at decision time); the
    actual fill is next-bar open and is used only for the outcome, never as a feature."""
    back = max(b, e - 6)                                  # ~30m recent window
    tt = int(np.searchsorted(ts, d.pump_start_ms)); tt = min(max(tt, 0), b - 1)
    d0 = max(0, tt - DAY_BARS); d240 = max(0, b - 240)
    sw_lo = float(lo[max(0, tt - 200):tt + 1].min())
    prior_win = float(lo[max(0, tt - 2 * DAY_BARS):tt + 1].min())
    base_tc = float(np.nanmedian(tc[d240:b])) if b > d240 else np.nan
    base_ats = float(np.nanmedian(ats[d240:b])) if b > d240 else np.nan
    sleep_qv = float(np.nanmean(qv[max(0, tt - 96):tt])) if tt > 0 else np.nan
    dump_qv = float(np.nanmean(qv[tt:b + 1])); dump_rng = float(np.nanmean(rng_bar[tt:b + 1]))
    cvd_trough = float(np.min(cvd[tt:e + 1])); cvd_dumpmag = abs(cvd[tt]) + 1e-9
    ret_since_bottom = px / low_b - 1.0
    oi_since_bottom = oi[e] / (oi[b] + 1e-9) - 1.0
    green = cl[b + 1:e + 1] > op[b + 1:e + 1]
    consec_green = 0
    for j in range(e, b, -1):
        if cl[j] > op[j]:
            consec_green += 1
        else:
            break
    prior = prior_bottoms[prior_bottoms < b]
    n_prior_48h = int(np.sum(ts[b] - ts[prior] <= 48 * 3_600_000)) if len(prior) else 0
    bars_since_prior = int(b - prior.max()) if len(prior) else -1
    btc_dump = _btc_ret(int(ts[tt]), int(ts[b]))
    return {
        # --- A. dump geometry ---
        "f_drop_pct": d.drop_pct, "f_drop_atr_mult": d.drop_atr_mult, "f_drop_bars": d.drop_bars,
        "f_speed": d.speed_pct_per_min, "f_path_eff": d.path_efficiency,
        "f_biggest_candle": d.biggest_candle_share, "f_n_upretrace": d.n_upretrace,
        "f_max_upretrace": d.max_upretrace_frac, "f_climax_lwick": d.climax_lower_wick_share,
        "f_climax_closepos": d.climax_close_pos,
        # --- B. sleep / pre-dump ---
        "f_sleep_range": d.sleep_range_pct, "f_base_to_top": d.base_to_top_pct,
        "f_pre_runup": d.pre_top_runup_pct, "f_top_spike": d.top_spike_above_sleep_pct,
        "f_sleep_slope": float(cl[tt] / (cl[max(0, tt - 96)] + 1e-9) - 1.0),
        # --- C. overextension / position ---
        "f_price_vs_ema240_bottom": d.price_vs_ema_pct,
        "f_price_vs_ema60_entry": float(cl[e] / (ema60[e] + 1e-12) - 1.0),
        "f_price_vs_ema240_entry": float(cl[e] / (ema240[e] + 1e-12) - 1.0),
        "f_dist_below_swinglow_atr": float((sw_lo - low_b) / (atr[b] + 1e-12)),
        "f_new_day_low": int(low_b <= float(lo[max(0, b - DAY_BARS):b].min())) if b > 0 else 0,
        "f_pct_prior_range_given": float((top - low_b) / (top - prior_win + 1e-9)),
        "f_cap": float(np.log10(base_qv[b] + 1)) if np.isfinite(base_qv[b]) else np.nan,
        "f_z_price": float((low_b - np.nanmean(cl[d240:b])) / (np.nanstd(cl[d240:b]) + 1e-9)) if b > d240 else np.nan,
        # --- D. flow / buyer activity ---
        "f_taker_e": float(np.nanmean(taker[back:e + 1])),
        "f_taker_turn": float(np.nanmean(taker[back:e + 1]) - np.nanmean(taker[b:e + 1])),
        "f_taker_bottom": float(taker[b]), "f_taker_dump": d.taker_share_drop,
        "f_cvd_since_bottom": (cvd[e] - cvd[b]) / (abs(cvd[b]) + 1e-9),
        "f_cvd_recover": float((cvd[e] - cvd_trough) / cvd_dumpmag),
        "f_cvd_slope": float((cvd[e] - cvd[back]) / cvd_dumpmag),
        "f_buyvol_spike": float(qv[e] * taker[e] / (0.5 * np.nanmedian(base_qv[b:e + 1]) + 1e-9)),
        "f_tc_climax": float(tc[b] / (base_tc + 1e-9)) if np.isfinite(base_tc) else np.nan,
        "f_tc_entry": float(tc[e] / (base_tc + 1e-9)) if np.isfinite(base_tc) else np.nan,
        "f_ats_bottom": float(ats[b] / (base_ats + 1e-9)) if np.isfinite(base_ats) else np.nan,
        "f_ats_entry": float(ats[e] / (base_ats + 1e-9)) if np.isfinite(base_ats) else np.nan,
        "f_ats_change": float(ats[e] / (ats[b] + 1e-9)),
        # --- E. open interest / deleveraging ---
        "f_oi_dump": d.oi_change_pct, "f_oi_since_bottom": oi_since_bottom,
        "f_oi_slope": float(oi[e] / (oi[back] + 1e-9) - 1.0),
        "f_oi_min_dump": float(np.min(oi[tt:b + 1]) / (oi[tt] + 1e-9) - 1.0),
        "f_oi_bottom_vs_top": float(oi[b] / (oi[tt] + 1e-9) - 1.0),
        "f_oi_price_div": float(oi_since_bottom - ret_since_bottom),
        # --- F. reversal microstructure ---
        "f_bars_to_entry": e - b, "f_ret_since_bottom": ret_since_bottom,
        "f_entry_vs_top": (top - px) / top if top > 0 else np.nan,
        "f_bounce_vel3": float(cl[e] / (cl[max(b, e - 3)] + 1e-9) - 1.0),
        "f_entry_lwick": float((min(op[e], cl[e]) - lo[e]) / (rng_bar[e] + 1e-12)),
        "f_entry_body": float(abs(cl[e] - op[e]) / (rng_bar[e] + 1e-12)),
        "f_entry_closepos": float((cl[e] - lo[e]) / (rng_bar[e] + 1e-12)),
        "f_green_since_bottom": float(green.mean()) if e > b else 0.0,
        "f_consec_green": consec_green,
        "f_higher_low": int(float(lo[b + 1:e + 1].min()) > low_b) if e > b else 0,
        "f_reclaim_high": int(cl[e] > hi[e - 1]),
        "f_atr_contract": float(atr[e] / (atr[b] + 1e-12)),
        "f_range_contract": float(np.nanmean(rng_bar[back:e + 1]) / (dump_rng + 1e-12)),
        # --- G. volume / activity ---
        "f_vol_entry": float(qv[e] / (np.nanmedian(base_qv[b:e + 1]) + 1e-9)),
        "f_vol_vs_sleep": float(qv[e] / (sleep_qv + 1e-9)) if np.isfinite(sleep_qv) else np.nan,
        "f_vol_decline": float(np.nanmean(qv[back:e + 1]) / (dump_qv + 1e-9)),
        "f_vol_climax": d.vol_climax_ratio,
        "f_rvol": float(qv[e] / (base_qv[e] + 1e-9)) if np.isfinite(base_qv[e]) else np.nan,
        # --- H. market context / regime ---
        "f_session": int(seqs[b]), "f_hour": int((ts[b] % 86_400_000) // 3_600_000),
        "f_dow": int(pd.Timestamp(int(ts[b]), unit="ms", tz="UTC").dayofweek),
        "f_btc_ret_dump": btc_dump,
        "f_btc_ret_recent": _btc_ret(int(ts[max(0, e - DAY_BARS)]), int(ts[e])),
        "f_idio_dump": float(-d.drop_pct - btc_dump) if np.isfinite(btc_dump) else np.nan,
        # --- I. recurrence ---
        "f_n_prior_dumps_48h": n_prior_48h, "f_bars_since_prior_dump": bars_since_prior,
    }


def _one(f):
    try:
        return build_symbol(Path(f))
    except Exception:  # noqa: BLE001
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare reversal triggers + fixed-RR outcome on sleep->dump setups.")
    ap.add_argument("--out", type=Path, default=Path(".output/results/knife_catch/dump_reversal.parquet"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"symbols={len(files)} tf={TF}m RR={RR} stop_buf={STOP_BUF} trig_win={TRIGGER_WIN} horizon={HORIZON}")
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, f): f for f in files}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 150 == 0:
                print(f"  {done}/{len(files)} rows={len(rows):,}")
    t = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(args.out)
    n_dumps = t.bottom_ts.nunique() if len(t) else 0
    print(f"\nwrote {len(t):,} trigger-rows over {n_dumps:,} DEV dumps -> {args.out}")
    if not len(t):
        return
    print(f"\n{'trigger':<12} {'fire%':>6} {'inval%':>7} {'trades':>7} {'win%':>6} {'meanR':>7} {'medBars':>7} {'medRisk%':>8} {'posWk%':>7}")
    for kind in TRIGGERS:
        g = t[t.trigger == kind]
        fired = g.entered.mean() * 100
        inval = g.invalidated.mean() * 100
        tr = g[g.entered == 1]
        if len(tr) < 30:
            print(f"{kind:<12} {fired:>6.1f} {inval:>7.1f} {len(tr):>7} (too few)"); continue
        wk = tr.groupby("week").R.mean()
        print(f"{kind:<12} {fired:>6.1f} {inval:>7.1f} {len(tr):>7} {tr.y.mean()*100:>6.1f} "
              f"{tr.R.mean():>+7.3f} {tr.bars_to_entry.median():>7.0f} {tr.risk_pct.median()*100:>7.1f} "
              f"{(wk>0).mean()*100:>7.0f}")
    print(f"\n(RR={RR}: breakeven win% = {100/(1+RR):.1f}. meanR includes time-stop marks. costs NOT yet applied.)")

    # --- RR sweep from the stored path summary (no re-detection) ---
    ent = t[t.entered == 1].dropna(subset=["mfe_R"]).copy()
    print("\n=== RR sweep (win% / meanR / posWk%) per trigger ===")
    print(f"{'RR':>4} | " + " | ".join(f"{k:^26}" for k in TRIGGERS))
    for rr in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        cells = []
        for kind in TRIGGERS:
            g = ent[ent.trigger == kind]
            res = [resolve_rr(m, bool(s), cr, rr) for m, s, cr in zip(g.mfe_R, g.stop_hit, g.close_R)]
            y = np.array([r[0] for r in res]); R = np.array([r[1] for r in res])
            gg = g.assign(R2=R); wk = gg.groupby("week").R2.mean()
            cells.append(f"{y.mean()*100:4.0f}% {R.mean():+5.3f} {(wk>0).mean()*100:3.0f}%")
        print(f"{rr:>4} | " + " | ".join(f"{c:^26}" for c in cells))
    print("(breakeven win% for RR: 0.5->66.7  0.75->57.1  1->50  1.5->40  2->33.3  3->25)")


if __name__ == "__main__":
    main()
