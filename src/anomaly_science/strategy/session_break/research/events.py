"""Previous-session-high breakout events, per symbol.

For every session block instance we take the reference high from a *previous*
block -- two variants, kept strictly separate:

    A (sequential):  the immediately preceding block  (US breaks OVERLAP's high)
    B (same_type):   the same block type a day earlier (US breaks yesterday's US)

The first bar of the current block whose high exceeds the reference high is the
break (t0). Everything measured at t0 uses only bars <= t0 (causal). The
outcome is the forward extension above the reference high over the *rest of the
current session*, ATR-normalised so runners and fizzles are comparable across
volatility regimes and cap tiers.

No path/stop simulation happens here -- this is discovery of *what separates
far-runners from fizzles*, per the validity-before-EV discipline. Execution
mechanics (stops, trailing) are a later phase and belong to the frozen core.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.sessions import (
    BLOCK_BY_SEQ,
    block_seq_for_ms,
    predecessor_same_type,
    predecessor_sequential,
    utc_day_for_ms,
)

ATR_WINDOW = 60  # 1h-equivalent ATR on 1m bars, for normalising session-scale moves
BASELINE_BARS = 1440  # trailing 24h median for relative-activity denominators
MIN_REF_BARS = 30  # predecessor block must have this many bars to trust its high
MIN_FWD_BARS = 15  # need this many bars after the break for a meaningful outcome


def _block_instances(day: np.ndarray, seq: np.ndarray, high: np.ndarray, low: np.ndarray):
    """Runs of equal (day, seq). dict[(day,seq)] -> (start, end_excl, max_high, min_low)."""

    key = day * 10 + seq  # seq in 0..4, monotonic non-decreasing with time
    change = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(key)]))
    out: dict[tuple[int, int], tuple[int, int, float, float]] = {}
    for s, e in zip(starts, ends):
        out[(int(day[s]), int(seq[s]))] = (
            int(s), int(e), float(high[s:e].max()), float(low[s:e].min())
        )
    return out


def build_symbol_events(path: Path) -> pd.DataFrame:
    """One row per (block instance x variant) that produced a valid break."""

    cols = [
        "timestamp", "open", "high", "low", "close", "volume",
        "quote_volume", "trade_count", "taker_buy_quote_volume", "open_interest",
        "short_liquidations_vol",
    ]
    df = pd.read_parquet(path)
    have = [c for c in cols if c in df.columns]
    df = df[have].copy()
    if len(df) < BASELINE_BARS + 100:
        return pd.DataFrame()
    df = df.sort_values("timestamp").reset_index(drop=True)

    ts = df["timestamp"].to_numpy(np.int64)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float)
    tc = df["trade_count"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float) if "taker_buy_quote_volume" in df else np.full(len(df), np.nan)
    oi = df["open_interest"].to_numpy(float) if "open_interest" in df else np.full(len(df), np.nan)
    liq = df["short_liquidations_vol"].to_numpy(float) if "short_liquidations_vol" in df else np.full(len(df), np.nan)

    vol = df["volume"].to_numpy(float) if "volume" in df else np.full(len(df), np.nan)
    atr = causal_atr(high=high, low=low, close=close, window=ATR_WINDOW)
    opn = df["open"].to_numpy(float)
    day = utc_day_for_ms(ts)
    seq = block_seq_for_ms(ts)
    inst = _block_instances(day, seq, high, low)

    # Trailing 24h median per-minute activity (causal baseline). Rolling median
    # is expensive; a shifted rolling gives us baseline as-of block start.
    qv_series = pd.Series(qv)
    tc_series = pd.Series(tc)
    base_qv = qv_series.rolling(BASELINE_BARS, min_periods=240).median().to_numpy()
    base_tc = tc_series.rolling(BASELINE_BARS, min_periods=240).median().to_numpy()

    # Higher-timeframe trend context: causal 1-day EMA and rolling 1-day VWAP.
    ema1d = pd.Series(close).ewm(span=BASELINE_BARS, adjust=False).mean().to_numpy()
    roll_quote = qv_series.rolling(BASELINE_BARS, min_periods=240).sum().to_numpy()
    roll_base = pd.Series(vol).rolling(BASELINE_BARS, min_periods=240).sum().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap1d = roll_quote / roll_base
    rh1440 = pd.Series(high).rolling(BASELINE_BARS, min_periods=60).max().to_numpy()  # rolling daily high
    with np.errstate(invalid="ignore", divide="ignore"):
        taker_share_bar = np.where(qv > 0, tbq / qv, np.nan)

    rows: list[dict] = []
    variants = {
        "sequential": predecessor_sequential,
        "same_type": predecessor_same_type,
    }
    for (d, s), (start, end, _mx, _mn) in inst.items():
        block = BLOCK_BY_SEQ[s]
        n_bars = end - start
        if n_bars < MIN_FWD_BARS + 1:
            continue
        # baseline as-of the bar before block start
        b0 = start - 1
        if b0 < 240:
            continue
        bqv = base_qv[b0]
        btc = base_tc[b0]
        if not (np.isfinite(bqv) and bqv > 0 and np.isfinite(btc) and btc > 0):
            continue
        # trailing turnover for cap tier: sum quote_volume over trailing 24h
        tw0 = max(0, start - BASELINE_BARS)
        trail_turnover = float(qv[tw0:start].sum())

        for vname, predf in variants.items():
            pday, pseq = predf(d, s)
            ref = inst.get((pday, pseq))
            if ref is None:
                continue
            r_start, r_end, ref_high, ref_low = ref
            if (r_end - r_start) < MIN_REF_BARS or not np.isfinite(ref_high) or ref_high <= 0:
                continue
            # Genuine intra-session cross: the block must OPEN below the level.
            # Otherwise price already trended/gapped above it before the session
            # and "the break" is a non-event (this is what produced the same_type
            # poke_atr=0.96 leak: a stale level far below current price).
            if not (opn[start] < ref_high):
                continue
            ref_range = ref_high - ref_low
            # first break bar within current block
            block_high = high[start:end]
            crossed = np.flatnonzero(block_high > ref_high)
            if len(crossed) == 0:
                continue
            b = start + int(crossed[0])  # absolute index of break bar
            fwd_end = end  # rest of session (exclusive)
            if fwd_end - b < MIN_FWD_BARS:
                continue
            a = atr[b]
            if not (np.isfinite(a) and a > 0):
                continue

            # ---- outcome: forward extension above ref over rest of session ----
            fwd_high = high[b:fwd_end].max()
            fwd_low = low[b:fwd_end].min()
            close_end = close[fwd_end - 1]
            # Fixed-horizon MFE from the break, to break the mechanical link
            # between "broke early" and "had more session minutes left to run".
            # Horizons may cross into the next session -- that is genuine
            # continuation, not a within-session window artifact.
            n = len(high)
            mfe_fix = {}
            for h in (60, 120, 240):
                j = min(b + h, n)
                mfe_fix[h] = (high[b:j].max() - close[b]) / a

            # ---- path-based (first-passage) target over a fixed 120-min window ----
            # A cleaner "runner": did price reach +up*ATR before falling -dn*ATR?
            j120 = min(b + 120, n)
            fwin_h = high[b:j120]
            fwin_l = low[b:j120]
            c_b = close[b]
            def _first_passage(up: float, dn: float) -> int:
                up_hit = np.flatnonzero(fwin_h >= c_b + up * a)
                dn_hit = np.flatnonzero(fwin_l <= c_b - dn * a)
                ui = up_hit[0] if len(up_hit) else np.iinfo(np.int64).max
                di = dn_hit[0] if len(dn_hit) else np.iinfo(np.int64).max
                if ui == di == np.iinfo(np.int64).max:
                    return -1  # neither side hit in window
                return int(ui < di)
            fp_2_15 = _first_passage(2.0, 1.5)
            fp_3_15 = _first_passage(3.0, 1.5)

            # ---- mean-reversion (fade back to the session mean) ----
            # Session VWAP as-of the break (causal): the "average price of the
            # session" the pop is stretched above. Target for a fade-short.
            sess_vol = vol[start:b + 1].sum()
            vwap_sess = qv[start:b + 1].sum() / sess_vol if sess_vol > 0 else np.nan
            stretch_vwap_atr = (close[b] - vwap_sess) / a if np.isfinite(vwap_sess) else np.nan
            swing_high = high[start:b + 1].max()  # breakout extreme -> short stop anchor
            hz = 240  # fade horizon
            jh = min(b + hz, n)
            sh = high[b + 1:jh]  # forward bars (entry is next bar; exclude break bar)
            sl = low[b + 1:jh]
            # reverter = price returns to session VWAP before a new extreme up-leg.
            # No look-ahead: first-passage over forward bars, stop checked first.
            def _fade_r(stop_buf_atr: float):
                if not (np.isfinite(vwap_sess) and vwap_sess < close[b]) or len(sh) == 0:
                    return np.nan, -1
                entry = close[b]                       # short here (fill modelled later)
                stop = swing_high + stop_buf_atr * a   # above the breakout high
                risk = stop - entry
                if risk <= 0:
                    return np.nan, -1
                up_hit = np.flatnonzero(sh >= stop)     # stopped out (flyer)
                tgt_hit = np.flatnonzero(sl <= vwap_sess)  # reverted to mean (win)
                ui = up_hit[0] if len(up_hit) else np.iinfo(np.int64).max
                ti = tgt_hit[0] if len(tgt_hit) else np.iinfo(np.int64).max
                if ui == ti == np.iinfo(np.int64).max:
                    exit_px = close[jh - 1]             # time-exit at horizon end
                    return (entry - exit_px) / risk, -1
                if ui <= ti:                            # stop first (pessimistic on ties)
                    return -1.0, 0
                return (entry - vwap_sess) / risk, 1    # reverted
            rev_r_tight, _ = _fade_r(0.3)
            # rev_hit off the moderate 1.0-ATR barrier: did price revert to the
            # session mean BEFORE a genuine new up-leg? (population label, less
            # sensitive to a hair-tight stop than the 0.3 variant).
            rev_r_wide, rev_hit = _fade_r(1.0)

            ext_atr = (fwd_high - ref_high) / a
            ext_pct = (fwd_high - ref_high) / ref_high
            ext_range = (fwd_high - ref_high) / ref_range if ref_range > 0 else np.nan
            mfe_atr = (fwd_high - close[b]) / a  # from a realizable entry at break close
            mae_atr = (close[b] - fwd_low) / a
            term_atr = (close_end - ref_high) / a  # held above by session end?

            # ---- causal features at the break bar ----
            qv_b = qv[b]
            tc_b = tc[b]
            block_qv = qv[start:b + 1].sum()
            ats_b = qv_b / tc_b if tc_b > 0 else np.nan
            ats_base = bqv / btc if btc > 0 else np.nan
            tbq_b = tbq[b]
            oi_start = oi[start] if start < len(oi) else np.nan
            oi_b = oi[b]
            liq_block = np.nansum(liq[start:b + 1])

            # ---- participation SHAPE (surge / breadth / climax), causal ----
            lo5 = max(start, b - 4)
            qv5 = qv[lo5:b + 1].mean()
            tc5 = tc[lo5:b + 1].mean()
            prior_lo = max(start, b - 19)
            qv_prior = qv[prior_lo:max(prior_lo, b - 4)].mean() if b - 4 > prior_lo else np.nan
            vol_accel = qv5 / qv_prior if (np.isfinite(qv_prior) and qv_prior > 0) else np.nan
            climax = qv_b / qv[start:b].max() if b > start and qv[start:b].max() > 0 else 1.0
            block_vol_share = qv_b / block_qv if block_qv > 0 else np.nan
            ats_block = block_qv / max(tc[start:b + 1].sum(), 1.0)
            ats_trend = ats_b / ats_block if ats_block > 0 else np.nan
            # consecutive green closes ending at the break bar
            green = 0
            k = b
            while k > start and close[k] > opn[k]:
                green += 1
                k -= 1

            # ---- level / trend context ----
            # how many times the reference block tested the level (proximity touches)
            band = 0.15 * ref_range if ref_range > 0 else a
            ref_touches = int(np.count_nonzero(high[r_start:r_end] >= ref_high - band))
            ema_dist_atr = (close[b] - ema1d[b]) / a if np.isfinite(ema1d[b]) else np.nan
            vwap_dist_atr = (close[b] - vwap1d[b]) / a if np.isfinite(vwap1d[b]) else np.nan

            # ---- extra market-logical metrics (all causal, at/around break) ----
            rng = high[b] - low[b]
            brk_body = (close[b] - opn[b]) / rng if rng > 0 else 0.0
            brk_upwick = (high[b] - max(opn[b], close[b])) / rng if rng > 0 else 0.0
            brk_range_atr = rng / a
            ret_15 = close[b] / close[b - 15] - 1.0 if b >= 15 and close[b - 15] > 0 else np.nan
            ret_60 = close[b] / close[b - 60] - 1.0 if b >= 60 and close[b - 60] > 0 else np.nan
            atr_prev = atr[b - 60] if b >= 60 else np.nan
            vol_ratio = a / atr_prev if (np.isfinite(atr_prev) and atr_prev > 0) else np.nan
            blen = end - start
            sess_progress = (b - start) / blen if blen > 0 else np.nan
            dist_dayhigh_atr = (close[b] - rh1440[b]) / a if np.isfinite(rh1440[b]) else np.nan
            tc_recent = tc[max(start, b - 15):b].mean() if b > start else tc[b]
            rtrades_accel = tc[b] / tc_recent if tc_recent > 0 else np.nan
            block_taker = (np.nansum(tbq[start:b + 1]) / np.nansum(qv[start:b + 1])
                           if np.nansum(qv[start:b + 1]) > 0 else np.nan)
            taker_now = taker_share_bar[b]
            taker_trend = (taker_now - block_taker) if np.isfinite(block_taker) else np.nan
            oi_slope = ((oi_b / oi_start - 1.0) / (b - start + 1)
                        if (np.isfinite(oi_start) and oi_start > 0) else np.nan)
            gap_open_atr = (opn[start] - close[start - 1]) / a if start > 0 else np.nan

            rows.append({
                "symbol": path.stem,
                "variant": vname,
                "utc_day": int(d),
                "seq": int(s),
                "session": block.name,
                "is_overlap": block.is_overlap,
                "break_ts": int(ts[b]),
                "ref_high": ref_high,
                "ref_range_atr": ref_range / a,
                "open_gap_atr": (ref_high - opn[start]) / a,  # how far below ref block opened
                "close_break": close[b],
                "atr": a,
                "bars_to_break": b - start,
                "poke_atr": (high[b] - ref_high) / a,
                "runup_atr": (ref_high - close[start]) / a,  # how far into block ref sat
                # outcome
                "ext_atr": ext_atr,
                "ext_pct": ext_pct,
                "ext_range": ext_range,
                "mfe_atr": mfe_atr,
                "mfe60_atr": mfe_fix[60],
                "mfe120_atr": mfe_fix[120],
                "mfe240_atr": mfe_fix[240],
                "mae_atr": mae_atr,
                "term_atr": term_atr,
                "fp_2_15": fp_2_15,
                "fp_3_15": fp_3_15,
                # mean-reversion fade
                "stretch_vwap_atr": stretch_vwap_atr,
                "rev_hit": rev_hit,
                "rev_r_tight": rev_r_tight,
                "rev_r_wide": rev_r_wide,
                # relative activity (raw ingredients; ratios formed at analysis)
                "qv_break": qv_b,
                "tc_break": tc_b,
                "base_qv": bqv,
                "base_tc": btc,
                "block_qv_to_break": block_qv,
                "ats_break": ats_b,
                "ats_base": ats_base,
                "taker_buy_share": tbq_b / qv_b if qv_b > 0 else np.nan,
                "oi_change": (oi_b / oi_start - 1.0) if (np.isfinite(oi_start) and oi_start > 0) else np.nan,
                "liq_fuel": liq_block,
                # participation shape / context
                "vol_accel": vol_accel,
                "climax": climax,
                "block_vol_share": block_vol_share,
                "ats_trend": ats_trend,
                "green_run": green,
                "ref_touches": ref_touches,
                "ema_dist_atr": ema_dist_atr,
                "vwap_dist_atr": vwap_dist_atr,
                # extra market-logical metrics
                "brk_body": brk_body,
                "brk_upwick": brk_upwick,
                "brk_range_atr": brk_range_atr,
                "ret_15": ret_15,
                "ret_60": ret_60,
                "vol_ratio": vol_ratio,
                "sess_progress": sess_progress,
                "dist_dayhigh_atr": dist_dayhigh_atr,
                "rtrades_accel": rtrades_accel,
                "taker_trend": taker_trend,
                "oi_slope": oi_slope,
                "gap_open_atr": gap_open_atr,
                # tier / regime
                "trail_turnover": trail_turnover,
            })
    return pd.DataFrame(rows)
