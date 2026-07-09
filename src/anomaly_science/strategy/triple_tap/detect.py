"""Triple-tap setup detection + all logically-grounded metrics (15m).

Geometry (per user spec):
  H1  significant swing high: sharp move on BOTH sides, bucketed on the 5/10/
      15/20/25% grid (the pct-reversal zigzag floor is 5%, so every pivot high
      already rose >=5% off the prior low; we record which bucket).
  H2  another high 16h-4d later at the SAME level: |H2-H1| <= N * ATR, where
      ATR is measured over the LAST retrace (not a fixed ATR14).
  H3  a third high 16h-4d after H2, again at the level.
  Retraces tighten: R1 >= R2 >= R3 and 0.5*R1 < R2 <= R1, none microscopic.
      Ri = (Hi - lowest low before the next high) / Hi.
  Entry  first 15m CLOSE above the level (= max(H1,H2,H3)) after H3, during a
      consolidation that hugs the high (R3 <= R2).
  Stop   under the post-H3 consolidation low.
  Take   H1 + (H1 - low_after_H1)  -- measured move above the level.

Volume STEP (____ -> ПППП): on the 4h chart, is the run-up into entry a regime
jump above the quiet baseline? We record the ratio and the percentile rank; no
threshold is applied yet (chosen after eyeballing).

Everything here is causal at the entry bar. Stage 1 = detection + metrics only.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from numba import njit
import json

_LOAD_COLS = ["timestamp", "open", "high", "low", "close", "quote_volume", "trade_count"]


def _load_1m(path: Path) -> dict[str, np.ndarray]:
    """Fast 1m read as a dict of numpy arrays (no pandas, no heavy validation -
    enriched_1m is the clean source enriched_15m was derived from)."""
    table = ds.dataset(path).to_table(columns=_LOAD_COLS)
    cols = {c: table.column(c).to_numpy(zero_copy_only=False) for c in _LOAD_COLS}
    cols["timestamp"] = cols["timestamp"].astype(np.int64)
    for c in _LOAD_COLS[1:]:
        cols[c] = cols[c].astype(np.float64)
    return cols


def _resample_np(cols: dict[str, np.ndarray], tf_min: int) -> dict[str, np.ndarray]:
    """Fast OHLCV resample of a 1m dict to ``tf_min`` bars via numpy reduceat,
    binning by floored timestamp (handles gaps, aligned to the TF boundary)."""
    ts = cols["timestamp"]
    n = len(ts)
    if n == 0:
        return {c: cols[c] for c in _LOAD_COLS}
    b = ts // (tf_min * 60_000)
    starts = np.concatenate(([0], np.nonzero(np.diff(b))[0] + 1))
    ends = np.append(starts[1:], n) - 1
    return {
        "timestamp": b[starts] * (tf_min * 60_000),
        "open": cols["open"][starts],
        "high": np.maximum.reduceat(cols["high"], starts),
        "low": np.minimum.reduceat(cols["low"], starts),
        "close": cols["close"][ends],
        "quote_volume": np.add.reduceat(cols["quote_volume"], starts),
        "trade_count": np.add.reduceat(cols["trade_count"], starts),
    }

# Diagnostic funnel: counts how many candidates die at each gate (per run).
REJECT: Counter = Counter()
REJECT_OUT = Path(".output/results/triple_tap_v1/detector_rejects.json")


def _rej(family: str, tf: str, stage: str, n: int = 1) -> None:
    REJECT[f"{family}.{tf}.{stage}"] += n

from anomaly_science.data.resample import resample_ohlcv
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEV_END_MS

DEFAULT_CACHE = Path(".output/market/binance_vision/um_futures/enriched_15m")
CACHE_1M = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_OUT = Path(".output/results/triple_tap_v1/setups.parquet")
BTC_SYMBOL = "BTCUSDT"  # activity benchmark: a valid tap must out-trade BTC

# --- frozen detection constants (registered before any EV inspection) ---
# Timing is expressed in HOURS so one detector runs on every timeframe; each
# call converts to that TF's bars via bars-per-hour = 60 / bar_minutes.
TFS = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "1h": 60, "4h": 240}  # multi-timeframe: run detection on each
SWING_PCT = 0.05                        # pct-reversal zigzag floor = user's grid floor
GRID = (0.05, 0.10, 0.15, 0.20, 0.25)  # significance buckets for reporting
# --- THE TF CONTRACT ---------------------------------------------------------
# A setup is the SAME bar-structure on every TF, at its own time scale. Structural
# windows are BARS of the detection TF (from the 15m reference 16h-4d), so a 1m
# situation matures in hours and a 4h one over weeks. Volume regime scales x16
# (15m->4h, honoring the user's "объём на 4ч"). Stop/ramp windows scale with the
# detection TF, measured on 1m. BTC activity, the 24h "hot" check and the IS/OOS
# boundary stay WALL-CLOCK (real-time, cross-coin).
# tap spacing per TF (HOURS) - the natural "significant time" isn't constant in
# bars OR wall-clock across TFs: 1m matures in hours, 4h over days (user: a 4h
# formation forms in ~3+ days). Converted to bars per TF via bph.
GAP_HOURS = {"1m": (1, 6), "3m": (2, 12), "5m": (3, 18), "10m": (6, 36), "15m": (12, 48), "1h": (16, 96), "4h": (36, 240)}
LEVEL_ATR_N = 1.5                       # "same level" tolerance in ATR-of-last-retrace units (widened from 1.0)
RETRACE_MIN = 0.05                      # a retrace may not be microscopic
R2_OVER_R1_MIN = 0.50                   # 0.5*R1 < R2 (level must still hold on shoulder 2)
MONO_EPS = 1.001                        # slack on "<=" retrace comparisons (float noise only)
LEVEL_EPS = 0.005                       # tolerance for the fresh-high left scan
FIRST_UNBROKEN_TOL = 0.01               # first-unbroken mode: a later tap may not exceed H1 by >1%
TIGHT_BAND_PCT = 0.03                   # tight-cluster mode: all taps within 3% of the level ("в одну точку")
LEVEL_BAND_ATR = 2.0                    # all taps within ~2 ATR band (3 let taps wander; formation fell apart)
PROM_LOOKBACK_MULT = 6.0                # overhead-supply window left of H1 = 6x the formation (min SLEEP+formation)
PROM_TOL = 0.010                        # NO offer above H1: a high >1% above H1 to the LEFT = a sell zone that
                                        # eats the buyers' energy (user); sub-1% is the same level (noise wick)
HUG_FRAC = 0.05                         # pre-breakout consolidation must pinch within 5% under the level
PINCH_BARS = 24                         # the immediate protergovka window right before the break
PINCH_MAX_DEPTH = 0.08                  # pinch low within 8% under the level (tight protergovka at the high)
STOP_SWING_PCT = 0.02                   # 1m swing size for the structural stop low
STOP_LOOKBACK_BARS = 24                 # find the last 1m structural low within this many detection-TF bars
WICK_MAX_FRAC = 0.75                    # a tap may not be a lone upper-wick spike (KOMA 0.83 out, good taps <=0.70)
LEFT_MULT = 2.0                         # the rise into H1 must be >= 2x the first retrace ("в 2-3+ раза")
LEFT_CLEAR_MULT = 2.0                   # no candle >= H1 price to the LEFT for >= 2x the formation duration (fresh high)
ANOM_VOL_MULT = 3.0                     # "аномально высокий объём сейчас": 24h vol >= 3x the 30d-median 24h
IGNITION_BARS = 40                      # the ignition window into H1: this many detection-TF bars
GRIND_IGN_MAX = 0.10                    # a "grind" has a weak ignition (rise into H1 < 10% over IGNITION_BARS)
SLEEP_BARS = 120                        # the base BEFORE the pump: this many detection-TF bars
GRIND_SLEEP_ATR = 0.007                 # ...and a "grind" base is NOT flat (median bar range/price > 0.7%)
BRK_WIN = 20                            # bars of context for the breakout-bar volume ratio
BRK_VOL_MULT = 1.5                      # "long initiative": breakout bar vol >= 1.5x recent median (provisional)
RAMP_RECENT_BARS = 6                    # volume crescendo INTO the break: recent window in detection-TF bars (on 1m)
RAMP_BASE_BARS = 18                     # ...vs the earlier approach window
VOL_RAMP_MULT = 1.3                     # ramp >= 1.3x = volume building into the break (raises runner odds)
# --- CONSOLIDATION-AFTER-PULLBACK (CAP) variant: a TIGHTER structure than fresh
# highs - the consol 3-tap is fine swings (~2.5%) tapped hours apart, under a
# single culmination high (the offer above = the TARGET). Its own detector.
FINE_SWING_PCT = 0.025                  # finer zigzag for the tight consolidation taps (5% misses them)
CAP_GAP_HOURS = {"1m": (0.25, 3), "3m": (0.33, 5), "5m": (0.5, 8), "10m": (0.75, 16), "15m": (1, 24), "1h": (4, 72), "4h": (12, 240)}
CAP_CONSOL_HOURS = {"1m": 12, "3m": 24, "5m": 48, "10m": 72, "15m": 96, "1h": 336, "4h": 1440}  # max span C -> breakout
CAP_MIN_PULLBACK = 0.05                 # a real pullback from the culmination (>=5%)
CAP_LEVEL_TOL = 0.015                   # consolidation taps within 1.5% of the level (tight)
CAP_CULM_PUMP = 0.18                    # the culmination must be a REAL pump off sleep, not a range spike/noise
CAP_SLEEP_RANGE_MAX = 0.10              # sleep price range must stay compact; no "helicopter" chop before the pump
CAP_SLEEP_BELOW_LEVEL_MIN = 0.05        # sleep must live clearly below the future level, not already trade there
CAP_SLEEP_MIN_BARS = 24                 # enough visible bars to call it sleep, not just a local low
CAP_PUMP_MAX_HOURS = {"1m": 3, "3m": 6, "5m": 10, "10m": 18, "15m": 24, "1h": 72, "4h": 240}
CAP_PUMP_PATH_EFF_MIN = 0.55            # vertical pump: displacement / travelled path, rejects downtrend chop
CAP_PUMP_VOL_OVER_SLEEP_MIN = 3.0       # pump volume must visibly wake up vs sleep
CAP_PUMP_TRADES_OVER_SLEEP_MIN = 2.0    # trade count must wake up too, not only notional volume
CAP_LEVEL_AGE_SHARE_MIN = 0.50          # level must exist for > half of C->entry consolidation
CAP_SUPPORT_LAST_OVER_FIRST_MIN = 0.98  # consolidation lows may be flat/rising, not a degrading downtrend
CAP_SUPPORT_MAX_RESID_PCT = 0.025       # lows should sit near one support line
CAP_SUPPORT_MIN_SLOPE_PCT_PER_BAR = -0.0005
CAP_CORE_TAP_MIN_LEVEL = 0.99           # at least two core taps must be within 1% of the selected level
CAP_LAST_TAP_MIN_LEVEL = 0.995          # the final pre-break tap must still be at the level, not a decayed lower high
CAP_RAMP_RECENT_MS = 10 * 60_000        # causal 1m volume ramp INTO the break: last 10m ...
CAP_RAMP_BASE_MS = 40 * 60_000          # ...vs the prior 40m (all before entry - no look-ahead)
CAP_RAMP_MIN = 1.1                      # require the 1m volume to be RISING into the break ("algo turned on")
STRICT_RAMP = 1.3                       # STRICT requires volume ramp BEFORE the break (user: no pre-break ramp = skip)
DISCOVERY_RAMP = 2.0                    # DISCOVERY: strong pre-break volume lets us relax the geometry
ASCEND_TOL = 1.005                      # reject ascending taps (rising wedge, ZORA): H(i+1) may not exceed H(i) by >0.5%
MIN_TAPS = 3                            # at least 3 obvious taps at the level
MAX_TAPS = 5                            # up to 5 (user: "их может быть и больше, 3-5")
VIOL_TOL = 1.05                         # Ri counts as a rise only if >5% over R(i-1)
VIOL_MAX = 1.25                         # a tolerated violation may not blow out the coil
TRADES_VS_BTC_GATE = 0.50               # soft activity gate: coin trades >= 0.5*BTC (stage-3)
VOL_RANK_GATE = 0.80                    # volume-step gate: vol_pct_rank >= 0.8 (user, refine later)
CONSOL_MIN_BARS = 4                     # 1h @ 15m: a little protergovka before the break
VOL_TF_RATIO = 16                       # volume-regime bar = 16x the detection bar (15m->4h)
VOL_BASE_BARS = 60                      # baseline length in vol-regime bars (~10d for 15m->4h)
VOL_RECENT_K = 3                        # recent "ПППП" heat in vol-regime bars
DORMANT_REGIME_BARS = 180               # sleep window before H1, in vol-regime bars (30d for 15m->4h)
MIN_DORM_BARS = 30                      # need >= this many vol-regime bars to call it "dormant"
FORMATION_VOL_MULT = 2.0                # H1 born of an OUTSTANDING-volume pump: formation >= 2x the prior sleep floor


@njit(cache=True)
def _zigzag_core(high: np.ndarray, low: np.ndarray, pct: float):
    """JIT core: the sequential zigzag state machine over ~500k 1m bars is the hot
    loop; numba makes it ~100x faster. Returns (idx[], price[], kind[]) with
    kind 1=high, 0=low."""
    n = len(high)
    idxs = np.empty(n, np.int64)
    prices = np.empty(n, np.float64)
    kinds = np.empty(n, np.int8)
    m = 0
    if n < 2:
        return idxs[:0], prices[:0], kinds[:0]
    hi_idx = 0; hi = high[0]
    lo_idx = 0; lo = low[0]
    direction = 0
    for i in range(1, n):
        hh = high[i]; ll = low[i]
        if direction >= 0:
            if hh >= hi:
                hi = hh; hi_idx = i
            if hi > 0.0 and (hi - ll) / hi >= pct:
                idxs[m] = hi_idx; prices[m] = hi; kinds[m] = 1; m += 1
                direction = -1; lo = ll; lo_idx = i
                continue
        if direction <= 0:
            if ll <= lo:
                lo = ll; lo_idx = i
            if lo > 0.0 and (hh - lo) / lo >= pct:
                idxs[m] = lo_idx; prices[m] = lo; kinds[m] = 0; m += 1
                direction = 1; hi = hh; hi_idx = i
    return idxs[:m], prices[:m], kinds[:m]


def _pct_zigzag(high: np.ndarray, low: np.ndarray, pct: float) -> list[tuple[int, float, str]]:
    """Alternating swing pivots with a RELATIVE reversal (crypto moves in %) - the
    user's grid. Thin wrapper over the JIT core, preserving the (idx, price, kind)
    tuple-list format the rest of the code (and charts) expect."""
    idxs, prices, kinds = _zigzag_core(
        np.ascontiguousarray(high, np.float64), np.ascontiguousarray(low, np.float64), float(pct))
    return [(int(idxs[k]), float(prices[k]), "high" if kinds[k] == 1 else "low") for k in range(len(idxs))]


def _bucket(move: float) -> float:
    """Largest grid magnitude the move clears (0.0 if below 5%)."""
    hit = [g for g in GRID if move >= g]
    return float(max(hit)) if hit else 0.0


def _mean_tr(high: np.ndarray, low: np.ndarray, close: np.ndarray, i0: int, i1: int) -> float:
    """Average true range over bars [i0, i1] (price units). Used for the
    'same level' tolerance measured over the LAST retrace, not a fixed window."""
    a, b = min(i0, i1), max(i0, i1)
    a = max(a, 1)
    if b < a:
        return float("nan")
    hl = high[a:b + 1] - low[a:b + 1]
    hc = np.abs(high[a:b + 1] - close[a - 1:b])
    lc = np.abs(low[a:b + 1] - close[a - 1:b])
    tr = np.maximum(hl, np.maximum(hc, lc))
    return float(np.mean(tr)) if len(tr) else float("nan")


def _retraces_ok(retr: list[float]) -> tuple[bool, int]:
    """Are the per-tap retraces a tightening coil into the level?

    Rules (K = len(retr) taps): none microscopic; R2 in (0.5*R1, R1] (the level
    holds on the second shoulder); net tightening R_last <= R1. The step-by-step
    "each retrace <= the previous" holds STRICTLY for 3 taps, but for 4-5 taps we
    tolerate up to one small violation (a rise <= 1.25x the previous), per user:
    "если их больше, допустимы небольшие нарушения последовательного уменьшения".
    Returns (ok, n_violations)."""
    k = len(retr)
    if k < MIN_TAPS or any(r < RETRACE_MIN for r in retr):
        return False, 0
    r1 = retr[0]
    if not (retr[1] <= r1 * MONO_EPS and retr[1] > R2_OVER_R1_MIN * r1):
        return False, 0
    if not (retr[-1] <= r1 * MONO_EPS):  # net coil tightens vs the first shoulder
        return False, 0
    allowed = 0 if k == 3 else 1
    viol = 0
    for i in range(2, k):
        if retr[i] > retr[i - 1] * VIOL_TOL:            # a genuine rise
            if retr[i] > retr[i - 1] * VIOL_MAX:        # blowout: never allowed
                return False, viol
            viol += 1
    return (viol <= allowed), viol


def _resample_vol(ts: np.ndarray, qv: np.ndarray, ratio: int) -> tuple[np.ndarray, np.ndarray]:
    """Aggregate quote volume to the volume-regime TF = every ``ratio`` detection
    bars, via a fast numpy bin-sum (no pandas resample). Returns (bar_open_ms, vol)
    where each regime bar sums ``ratio`` consecutive detection bars."""
    n = len(qv)
    if n == 0:
        return ts[:0], qv[:0]
    ng = (n + ratio - 1) // ratio
    pad = ng * ratio - n
    q = np.concatenate([qv, np.zeros(pad, qv.dtype)]) if pad else qv
    v = q.reshape(ng, ratio).sum(axis=1)
    return ts[: ng * ratio: ratio], v


def _volume_step(v4_ts: np.ndarray, v4_v: np.ndarray, entry_ms: int) -> dict[str, float]:
    """4h volume regime at entry, causal. ratio = recent heat / quiet baseline;
    pct_rank = where the entry 4h bar sits in its trailing window."""
    b = int(np.searchsorted(v4_ts, entry_ms, side="right"))  # bars closed at/before entry
    if b < VOL_BASE_BARS + VOL_RECENT_K:
        return dict(vol_step_ratio=np.nan, vol_pct_rank=np.nan, vol_recent=np.nan, vol_base=np.nan)
    recent = float(np.median(v4_v[b - VOL_RECENT_K:b]))
    base = float(np.median(v4_v[b - (VOL_BASE_BARS + VOL_RECENT_K):b - VOL_RECENT_K]))
    window = v4_v[b - (VOL_BASE_BARS + VOL_RECENT_K):b]
    rank = float((window < v4_v[b - 1]).mean())
    return dict(
        vol_step_ratio=recent / base if base > 0 else np.nan,
        vol_pct_rank=rank,
        vol_recent=recent,
        vol_base=base,
    )


def _hot_coin_now(
    tc: np.ndarray, qv: np.ndarray, entry_idx: int, bph: float, entry_ms: int,
    btc_ts: np.ndarray, btc_tc: np.ndarray,
) -> tuple[float, float, bool]:
    """Is the coin HOT right now (at entry)? WALL-CLOCK 24h (TF-independent):
    returns (24h trades / BTC 24h trades, 24h volume / 30d-median 24h volume,
    hot?). Hot = trades24 >= 0.5*BTC OR 24h volume anomalously high. Allows a
    re-test (non-fresh high) when the coin is currently on fire."""
    bars24 = max(1, int(round(24 * bph)))
    lo = max(0, entry_idx - bars24)
    coin_tr = float(tc[lo:entry_idx + 1].sum())
    btc_tr = _sum_between(btc_ts, btc_tc, entry_ms - 86_400_000, entry_ms)
    tr_ratio = coin_tr / btc_tr if btc_tr > 0 else np.nan
    cur24 = float(qv[lo:entry_idx + 1].sum())
    b30 = max(0, entry_idx - int(round(30 * 24 * bph)))
    med = float(np.median(qv[b30:entry_idx])) if entry_idx > b30 else np.nan
    anom = cur24 / (bars24 * med) if np.isfinite(med) and med > 0 else np.nan
    hot = (np.isfinite(tr_ratio) and tr_ratio >= TRADES_VS_BTC_GATE) or (np.isfinite(anom) and anom >= ANOM_VOL_MULT)
    return tr_ratio, anom, bool(hot)


def _structural_stop(
    fine_ts: np.ndarray, fine_high: np.ndarray, fine_low: np.ndarray, entry_ms: int, lookback_ms: int
) -> tuple[float, int]:
    """Causal stop on 1m: the LAST structural swing low before the breakout (a
    real, visible low, not noise), within ``lookback_ms`` (scales with the
    detection TF). If no clean swing formed, the setup is invalid."""
    e = int(np.searchsorted(fine_ts, entry_ms, side="left"))
    b = int(np.searchsorted(fine_ts, entry_ms - lookback_ms, side="left"))
    if e - b < 4:
        return np.nan, entry_ms
    piv = _pct_zigzag(fine_high[b:e], fine_low[b:e], STOP_SWING_PCT)
    lows = [(idx, price) for idx, price, kind in piv if kind == "low"]
    if lows:
        idx, price = lows[-1]
        return float(price), int(fine_ts[b + idx])
    return np.nan, entry_ms


def _pre_breakout_ramp(
    fine_ts: np.ndarray, fine_qv: np.ndarray, entry_ms: int, recent_ms: int, base_ms: int
) -> float:
    """1m volume crescendo INTO the break: median 1m volume over the last
    ``recent_ms`` before entry / median over the earlier ``base_ms``. Windows scale
    with the detection TF. >1 means volume was building into the level."""
    e = int(np.searchsorted(fine_ts, entry_ms, side="left"))       # bars strictly before the break
    r0 = int(np.searchsorted(fine_ts, entry_ms - recent_ms, side="left"))
    b0 = int(np.searchsorted(fine_ts, entry_ms - (recent_ms + base_ms), side="left"))
    if r0 <= b0 or e <= r0:
        return np.nan
    recent = float(np.median(fine_qv[r0:e]))
    base = float(np.median(fine_qv[b0:r0]))
    return recent / base if base > 0 else np.nan


def _path_efficiency(close: np.ndarray, start: int, end: int) -> float:
    """Directional cleanliness: displacement divided by total travelled path."""
    if end <= start:
        return np.nan
    path = float(np.sum(np.abs(np.diff(close[start:end + 1]))))
    disp = abs(float(close[end] - close[start]))
    return disp / path if path > 0 else np.nan


def _support_line_metrics(
    bars: list[int],
    prices: list[float],
    *,
    level: float,
) -> dict[str, float]:
    """How close consolidation lows are to one flat/rising support line."""
    if len(bars) < 2 or level <= 0:
        return {
            "support_lows_slope_pct_per_bar": np.nan,
            "support_lows_max_resid_pct": np.nan,
            "support_lows_last_over_first": np.nan,
        }
    x = np.asarray(bars, dtype=float)
    y = np.asarray(prices, dtype=float)
    x = x - x[0]
    if len(x) >= 3 and float(np.ptp(x)) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        resid = y - (slope * x + intercept)
        max_resid_pct = float(np.max(np.abs(resid)) / level)
    else:
        slope = (y[-1] - y[0]) / (x[-1] - x[0]) if x[-1] != x[0] else np.nan
        max_resid_pct = 0.0
    return {
        "support_lows_slope_pct_per_bar": float(slope / level) if np.isfinite(slope) else np.nan,
        "support_lows_max_resid_pct": max_resid_pct,
        "support_lows_last_over_first": float(y[-1] / y[0]) if y[0] > 0 else np.nan,
    }


def _formation_awakening(
    v4_ts: np.ndarray, v4_v: np.ndarray, h1_ms: int, entry_ms: int, dormant_ms: int
) -> float:
    """Is the whole level formation on RAISED volume vs the prior SLEEP FLOOR?

    Ratio = median 4h volume over the formation window [H1 .. entry] divided by
    the quiet floor (25th pct) of the 30 days before H1. We use the floor, not the
    median, because the awakening pump that BUILDS H1 often falls inside the
    pre-H1 window and would inflate a median - the floor still reflects the sleep.
    inf if the coin has no real dormancy (woke at listing); NaN if no formation."""
    h = int(np.searchsorted(v4_ts, h1_ms, side="left"))
    e = int(np.searchsorted(v4_ts, entry_ms, side="right"))
    if e <= h:
        return np.nan
    form_med = float(np.median(v4_v[h:e]))
    d0 = int(np.searchsorted(v4_ts, h1_ms - dormant_ms, side="left"))
    dorm = v4_v[d0:h]
    if len(dorm) < MIN_DORM_BARS:
        return np.inf
    floor = float(np.percentile(dorm, 25))
    return form_med / floor if floor > 0 else np.inf


@lru_cache(maxsize=2)
def _btc_trades(cache_dir_str: str) -> tuple[np.ndarray, np.ndarray]:
    """BTC 15m (timestamp, trade_count) arrays, loaded once. The activity
    benchmark: over the last retrace a valid tap trades MORE than BTC does."""
    f = _load_symbol(Path(cache_dir_str) / f"{BTC_SYMBOL}.parquet")[0]
    return f["timestamp"].to_numpy(np.int64), f["trade_count"].to_numpy(float)


def _sum_between(ts_arr: np.ndarray, val: np.ndarray, t0: int, t1: int) -> float:
    a = int(np.searchsorted(ts_arr, t0, side="left"))
    b = int(np.searchsorted(ts_arr, t1, side="right"))
    return float(val[a:b].sum())


def _link_ok(pivots, high, low, close, start_pos: int, prev_pos: int, cur_pos: int,
             gap_min: float, gap_max: float) -> bool:
    """Can the high at ``cur_pos`` extend the chain: GAP_MIN_H-GAP_MAX_H after
    ``prev_pos`` and still at the chain's level (within N*ATR of the retrace just
    before it)? Gaps are passed in bars for the current timeframe."""
    gap = pivots[cur_pos][0] - pivots[prev_pos][0]
    if not (gap_min <= gap <= gap_max):
        return False
    lo_pos = prev_pos + 1
    if lo_pos >= len(pivots) or pivots[lo_pos][2] != "low":
        return False
    atr = _mean_tr(high, low, close, pivots[prev_pos][0], pivots[lo_pos][0])
    if not np.isfinite(atr):
        return False
    return abs(pivots[cur_pos][1] - pivots[start_pos][1]) <= LEVEL_ATR_N * atr


def find_setups(
    symbol: str, *, cache_dir: Path = DEFAULT_CACHE, end_ms: int | None = DEV_END_MS,
    strict: bool = True,
) -> list[dict]:
    """Detect triple-tap setups on this symbol across all TFS (1m..4h). ``strict``
    applies the full geometry; ``strict=False`` (DISCOVERY) relaxes the quality
    gates but requires STRONG pre-breakout volume - candidates for manual review."""
    base = _load_1m(CACHE_1M / f"{symbol}.parquet")             # 1m dict of arrays
    btc_ts, btc_tc = _btc_trades(str(cache_dir))
    # ramp + structural stop live on 1m (the finest structure), per user.
    fine_ts, fine_qv = base["timestamp"], base["quote_volume"]
    fine_high, fine_low = base["high"], base["low"]
    out: list[dict] = []
    for tf, minutes in TFS.items():
        cols = base if minutes == 1 else _resample_np(base, minutes)
        out.extend(_detect_frame(cols, symbol, tf, minutes, btc_ts, btc_tc,
                                 fine_ts, fine_qv, fine_high, fine_low, end_ms, strict))
        out.extend(_detect_cap(cols, symbol, tf, minutes, btc_ts, btc_tc,
                               fine_ts, fine_qv, fine_high, fine_low, end_ms, strict))
    return out


def _detect_frame(
    cols: dict[str, np.ndarray], symbol: str, tf: str, bar_minutes: int,
    btc_ts: np.ndarray, btc_tc: np.ndarray,
    fine_ts: np.ndarray, fine_qv: np.ndarray, fine_high: np.ndarray, fine_low: np.ndarray,
    end_ms: int | None, strict: bool = True,
) -> list[dict]:
    ts = cols["timestamp"]
    open_ = cols["open"]
    high = cols["high"]
    low = cols["low"]
    close = cols["close"]
    qv = cols["quote_volume"]
    tc = cols["trade_count"]
    bph = 60.0 / bar_minutes                          # bars per hour for this TF
    bar_ms = bar_minutes * 60_000
    # tap spacing: per-TF hours -> bars for this TF
    gmin_h, gmax_h = GAP_HOURS[tf]
    gap_min, gap_max = gmin_h * bph, gmax_h * bph
    entry_max, consol_min = int(gap_max), CONSOL_MIN_BARS
    # volume regime scaled x16 to this TF; dormancy in regime-bars
    vol_minutes = bar_minutes * VOL_TF_RATIO
    v4_ts, v4_v = _resample_vol(ts, qv, VOL_TF_RATIO)
    dormant_ms = DORMANT_REGIME_BARS * vol_minutes * 60_000
    # 1m stop/ramp windows scaled by this TF
    stop_lookback_ms = STOP_LOOKBACK_BARS * bar_ms
    ramp_recent_ms, ramp_base_ms = RAMP_RECENT_BARS * bar_ms, RAMP_BASE_BARS * bar_ms
    pivots = _pct_zigzag(high, low, SWING_PCT)
    out: list[dict] = []
    seen_entries: set[int] = set()

    # Walk chains of K same-level highs (K in [MIN_TAPS, MAX_TAPS]). We start from
    # EVERY high pivot (preceded by a low) and build the longest chain from it, so
    # if the maximal chain fails validity a shorter sub-chain still gets its shot
    # (high recall - "не пропуская ни один"); duplicate breakouts dedupe by entry.
    for a in range(1, len(pivots) - 2):
        if pivots[a][2] != "high" or pivots[a - 1][2] != "low":
            continue

        chain = [a]                                  # pivot positions of the taps
        while len(chain) < MAX_TAPS:
            nxt = chain[-1] + 2
            if nxt >= len(pivots) or pivots[nxt][2] != "high":
                break
            if not _link_ok(pivots, high, low, close, chain[0], chain[-1], nxt, gap_min, gap_max):
                break
            chain.append(nxt)
        if len(chain) < MIN_TAPS:
            continue

        tap_bar = [pivots[c][0] for c in chain]
        tap_px = [pivots[c][1] for c in chain]
        i0 = pivots[a - 1][0]                        # low before H1 (left impulse base)
        # internal retrace lows (one between each pair of taps)
        lo_bar = [pivots[c + 1][0] for c in chain[:-1]]
        lo_px = [pivots[c + 1][1] for c in chain[:-1]]
        level = max(tap_px)
        last_bar = tap_bar[-1]

        atr_r1 = _mean_tr(high, low, close, tap_bar[0], lo_bar[0])
        if not (np.isfinite(atr_r1) and atr_r1 > 0):
            continue
        # highs must stay in one band: the first tap is not broken by a later one
        # and the whole cluster fits within one ATR ("~one point", not wandering).
        REJECT["0_candidates"] += 1
        # taps must form ONE clean level (user: "в одном диапазоне, желательно в
        # одну точку, ЛИБО первый неперебит" - an OR): EITHER a tight cluster
        # (spread <= 3% of the level) OR H1 is the highest tap (descending taps ok,
        # like GLM). A wide spread with a later tap ABOVE H1 = the level fell apart
        # (PYTH: 4.2% spread, H3 +2.2%).
        tap_spread = (max(tap_px) - min(tap_px)) / level
        first_unbroken = max(tap_px) <= tap_px[0] * (1 + FIRST_UNBROKEN_TOL)
        # ASCENDING taps = rising wedge / narrowing channel (ZORA), not a flat
        # level - reject ALWAYS (both modes).
        if all(tap_px[i + 1] > tap_px[i] * ASCEND_TOL for i in range(len(tap_px) - 1)):
            REJECT["ascending"] += 1
            continue
        # H1 must be the PRIME (highest) tap - later taps test it from below. SOPH
        # had H1 below the others ("так нельзя"); the tight-cluster OR is dropped.
        if strict and not first_unbroken:
            REJECT["band"] += 1
            continue
        # a tap must be a real high, not a lone upper-wick spike (KOMA-type):
        # the tap candle's upper wick may not dominate its range.
        tap_wick_frac = 0.0
        for tb in tap_bar:
            rng = high[tb] - low[tb]
            if rng > 0:
                tap_wick_frac = max(tap_wick_frac, (high[tb] - max(open_[tb], close[tb])) / rng)
        if strict and tap_wick_frac > WICK_MAX_FRAC:
            REJECT["wick"] += 1
            continue

        # scan forward from the LAST tap for the first CLOSE above the level;
        # run_low = deepest post-tap low (for the shrinking-retrace validity).
        entry_idx = -1
        run_low = np.inf
        run_low_idx = last_bar
        for j in range(last_bar + 1, min(last_bar + entry_max, len(close))):
            if (j - last_bar) >= consol_min and close[j] > level:
                entry_idx = j
                break
            if low[j] < run_low:
                run_low, run_low_idx = float(low[j]), j
        if entry_idx < 0 or not np.isfinite(run_low):
            REJECT["no_breakout"] += 1
            continue

        # the PROTERGOVKA = the immediate window right before the break. It must
        # hug the level (its high reaches it) and be TIGHT (its low within
        # PINCH_MAX_DEPTH under it) - a flat pinch at the high, not a rally into
        # it. The stop sits just under this pinch (user: "стоп за проторговку").
        ps = max(last_bar + 1, entry_idx - PINCH_BARS)
        pinch_slice_low = low[ps:entry_idx]
        pinch_high = float(high[ps:entry_idx].max())
        pinch_low = float(pinch_slice_low.min())
        pinch_low_idx = ps + int(np.argmin(pinch_slice_low))
        if strict and pinch_high < level * (1 - HUG_FRAC):
            REJECT["pinch_hug"] += 1
            continue
        if strict and (level - pinch_low) / level > PINCH_MAX_DEPTH:
            REJECT["pinch_depth"] += 1
            continue
        # pinch volatility (HEMI = "поджатие слишком волатильное"): the width of
        # the pinch box and its mean bar range, both vs the level.
        pinch_range_pct = (pinch_high - pinch_low) / level
        pinch_bar_atr_pct = float(np.mean(high[ps:entry_idx] - low[ps:entry_idx])) / level

        # per-tap retraces: R_i after H_i (last one = final consolidation)
        retr = [(tap_px[i] - lo_px[i]) / tap_px[i] for i in range(len(chain) - 1)]
        retr.append((tap_px[-1] - run_low) / tap_px[-1])
        # retrace HEALTH: path efficiency of each down-leg = drop / total travelled
        # (clean impulsive drop -> ~1; sawtooth chop like MLN -> low). Keep the worst.
        segs = [(tap_bar[i], lo_bar[i]) for i in range(len(chain) - 1)] + [(last_bar, run_low_idx)]
        effs = []
        for sa, sb in segs:
            if sb > sa:
                path = float(np.sum(np.abs(np.diff(close[sa:sb + 1]))))
                disp = float(high[sa] - low[sb])
                effs.append(disp / path if path > 0 else np.nan)
        retrace_eff_min = float(np.nanmin(effs)) if effs else np.nan
        retrace_eff_mean = float(np.nanmean(effs)) if effs else np.nan
        ok, n_viol = _retraces_ok(retr)
        if strict and not ok:
            REJECT["retraces"] += 1
            continue

        # FRESH HIGH: no candle traded at/above H1's price to the LEFT for a long
        # stretch (>= LEFT_CLEAR_MULT x the formation duration) - the level is new
        # overhead, born from a sleep, not a re-test of old range.
        formation_bars = entry_idx - tap_bar[0]
        h1p = tap_px[0] * (1 - LEVEL_EPS)
        j = tap_bar[0] - 1
        while j >= 0 and high[j] < h1p:
            j -= 1
        left_clear_bars = (tap_bar[0] - 1) - j
        left_clear_ratio = left_clear_bars / formation_bars if formation_bars > 0 else np.nan
        # NO OVERHEAD SUPPLY (ALWAYS): there must be no clearly-higher high to the
        # LEFT of H1 for a long stretch - an offer above (even slightly higher) is a
        # sell zone that eats the breakout's buying energy (user: RLC/SKATE). Also
        # forces H1 onto the PRIME high, not an internal swing (BANANAS/XNY/PTB).
        # NO OVERHEAD SUPPLY: no clearly-higher high to the LEFT of H1 - an offer
        # above eats the breakout's buying energy (user). The one exception (a
        # single culmination high above a pullback consolidation) is handled by the
        # dedicated CAP detector, not here.
        prom_look = SLEEP_BARS + formation_bars
        pw0 = max(0, tap_bar[0] - prom_look)
        left_max = float(high[pw0:tap_bar[0]].max()) if tap_bar[0] > pw0 else 0.0
        if left_max > tap_px[0] * (1 + PROM_TOL):
            REJECT["overhead_supply"] += 1
            continue
        # FRESH HIGH is the core of the user's examples (sleep -> pump -> new
        # level). EXCEPTION: a re-test is allowed if the coin is HOT right now
        # (anomalous 24h volume OR 24h trades >= 0.5*BTC) - prominence still holds.
        entry_ms_now = int(ts[entry_idx])
        tr24_vs_btc, anom_vol_24h, hot_now = _hot_coin_now(
            tc, qv, entry_idx, bph, entry_ms_now, btc_ts, btc_tc)
        fresh = np.isfinite(left_clear_ratio) and left_clear_ratio >= LEFT_CLEAR_MULT
        if strict and not (fresh or hot_now):
            REJECT["fresh_high"] += 1
            continue
        # rise into H1 from the PUMP BASE (lowest low over a generous pre-H1 window
        # = the awakening base); "рост до хая1" vs the first retrace + its cleanness.
        base0 = max(0, tap_bar[0] - 3 * formation_bars)
        pump_base_idx = base0 + int(np.argmin(low[base0:tap_bar[0] + 1]))
        pump_base = float(low[pump_base_idx])
        left_impulse = (tap_px[0] - pump_base) / pump_base if pump_base > 0 else np.nan
        left_impulse_over_r1 = left_impulse / retr[0] if retr[0] > 0 else np.nan
        left_path = float(np.sum(np.abs(np.diff(close[pump_base_idx:tap_bar[0] + 1]))))
        left_impulse_eff = (tap_px[0] - pump_base) / left_path if left_path > 0 else np.nan
        # NOTE: LEFT_MULT (rise>=2-3x R1) and LEFT_CLEAR_MULT (fresh high) are
        # RECORDED, not hard-gated yet - the numeric proxies still conflict with
        # user's good examples (MOODENG/ACH not fresh; PLUME rise measured low).
        # IGNITION: a real setup is SLEEP -> sharp PUMP -> level (not a slow
        # trend-grind like HUSDT). Find the pump START (= end of sleep) as the
        # lowest low in the IGNITION_BARS just before H1; the pump must rise
        # >= IGN_MIN_PCT into H1 over that short window.
        ign0 = max(0, tap_bar[0] - IGNITION_BARS)
        pump_start_idx = ign0 + int(np.argmin(low[ign0:tap_bar[0] + 1]))
        pump_start = float(low[pump_start_idx])
        ignition_rise = (tap_px[0] - pump_start) / pump_start if pump_start > 0 else np.nan
        ignition_bars = tap_bar[0] - pump_start_idx
        # SLEEP: the base BEFORE the pump must be genuinely FLAT (low volatility).
        sl0 = max(0, pump_start_idx - SLEEP_BARS)
        pre_pump_atr_pct = (
            float(np.median((high[sl0:pump_start_idx + 1] - low[sl0:pump_start_idx + 1]) / close[sl0:pump_start_idx + 1]))
            if pump_start_idx > sl0 else np.nan
        )
        # GRIND filter: a setup is a trend-grind (not sleep->pump) when it has BOTH
        # a weak ignition AND a non-flat base. It passes if EITHER the pump is sharp
        # OR the base is genuinely flat. HUSDT (ign 4.7%, base 0.89%) = grind; DIA
        # (ign 6.8% but flat base 0.54%) and MUBARAK (sharp 20%) pass.
        # (skip for CAP: H1 is a consolidation, not a pump - the pump into the
        # culmination C was validated above.)
        weak_ign = not (np.isfinite(ignition_rise) and ignition_rise >= GRIND_IGN_MAX)
        choppy_base = not (np.isfinite(pre_pump_atr_pct) and pre_pump_atr_pct <= GRIND_SLEEP_ATR)
        if weak_ign and choppy_base:
            REJECT["grind"] += 1
            continue

        entry_ms = int(ts[entry_idx])
        if end_ms is not None and entry_ms >= end_ms:  # IS only; never peek at OOS
            continue
        if entry_ms in seen_entries:
            continue
        # H1 must be born of an OUTSTANDING-volume pump: the formation sits on
        # >= FORMATION_VOL_MULT x the prior sleep floor (____ -> пП). This + the
        # fresh-high gate = the user's screenshot pattern (sleep -> pump -> new
        # level -> breakout). inf/NaN (new coin / no dormancy) pass.
        formation_vol_ratio = _formation_awakening(v4_ts, v4_v, int(ts[tap_bar[0]]), entry_ms, dormant_ms)
        if strict and np.isfinite(formation_vol_ratio) and formation_vol_ratio < FORMATION_VOL_MULT:
            REJECT["form_vol"] += 1
            continue
        REJECT["passed_geometry"] += 1
        seen_entries.add(entry_ms)

        # last-retrace activity vs BTC over the FINAL retrace (last tap -> consol
        # low), same wall-clock window for this coin and for BTC.
        retr_trades = float(tc[last_bar:run_low_idx + 1].sum())
        btc_retr_trades = _sum_between(btc_ts, btc_tc, int(ts[last_bar]), int(ts[run_low_idx]))
        trades_vs_btc = retr_trades / btc_retr_trades if btc_retr_trades > 0 else np.nan
        trades_gt_btc = bool(symbol != BTC_SYMBOL and retr_trades > btc_retr_trades)

        # measured-move take (H1 + first retrace height); stop = LAST structural
        # low on the LOWER TF (15m), causal (user: "стоп на младшем тф за
        # последний структурный лой"). Fall back to the pinch low if none.
        retrace1_height = tap_px[0] - lo_px[0]
        take = tap_px[0] + retrace1_height   # fresh-high measured move
        entry_price = float(level)
        stop, stop_ms = _structural_stop(fine_ts, fine_high, fine_low, entry_ms, stop_lookback_ms)
        if not (np.isfinite(stop) and stop < entry_price):
            continue
        rr = (take - entry_price) / (entry_price - stop) if entry_price > stop else np.nan

        atr_r2 = _mean_tr(high, low, close, tap_bar[1], lo_bar[1])
        gaps_h = [(tap_bar[i + 1] - tap_bar[i]) / bph for i in range(len(chain) - 1)]
        lows_all = lo_px + [run_low]
        vol = _volume_step(v4_ts, v4_v, entry_ms)
        vol_ok = bool(np.isfinite(vol["vol_pct_rank"]) and vol["vol_pct_rank"] >= VOL_RANK_GATE)
        trades_ok = bool(np.isfinite(trades_vs_btc) and trades_vs_btc >= TRADES_VS_BTC_GATE)
        # "long initiative": volume on the breakout bar vs recent median (the
        # obvious spike as price crosses the last high).
        w0 = max(0, entry_idx - BRK_WIN)
        base_v = float(np.median(qv[w0:entry_idx])) if entry_idx > w0 else np.nan
        brk_vol_ratio = float(qv[entry_idx] / base_v) if np.isfinite(base_v) and base_v > 0 else np.nan
        brk_initiative = bool(np.isfinite(brk_vol_ratio) and brk_vol_ratio >= BRK_VOL_MULT)
        # lower-TF (15m) volume crescendo into the break -> raises runner odds
        pre_brk_vol_ramp = _pre_breakout_ramp(fine_ts, fine_qv, entry_ms, ramp_recent_ms, ramp_base_ms)
        vol_ramp_up = bool(np.isfinite(pre_brk_vol_ramp) and pre_brk_vol_ramp >= VOL_RAMP_MULT)
        # THE key signal (user): volume must RAMP UP before the break (not just in
        # it). Required in both modes; discovery demands a stronger ramp in
        # exchange for the relaxed geometry.
        ramp_gate = STRICT_RAMP if strict else DISCOVERY_RAMP
        if not (np.isfinite(pre_brk_vol_ramp) and pre_brk_vol_ramp >= ramp_gate):
            REJECT["pre_brk_ramp"] += 1
            continue

        out.append(dict(
            discovery=bool(not strict),
            setup_family="breakout", setup_type="fresh_high", culmination=np.nan,
            symbol=symbol, tf=tf, n_taps=int(len(chain)), n_viol=int(n_viol),
            # variable-length structure (all taps / retraces)
            tap_times_ms=[int(ts[b]) for b in tap_bar], tap_prices=[float(p) for p in tap_px],
            retrace_low_times_ms=[int(ts[b]) for b in lo_bar] + [int(ts[run_low_idx])],
            retrace_lows=[float(x) for x in lows_all], retraces=[float(r) for r in retr],
            # first-three scalars (take/stop/charts/back-compat)
            h1_time_ms=int(ts[tap_bar[0]]), h2_time_ms=int(ts[tap_bar[1]]), h3_time_ms=int(ts[tap_bar[2]]),
            low12_time_ms=int(ts[lo_bar[0]]), low23_time_ms=int(ts[lo_bar[1]]),
            consol_low_time_ms=int(ts[pinch_low_idx]), entry_time_ms=entry_ms,
            h1=float(tap_px[0]), h2=float(tap_px[1]), h3=float(tap_px[2]), level=float(level),
            level_disp=float(max(tap_px) - min(tap_px)),
            level_disp_atr=float((max(tap_px) - min(tap_px)) / atr_r2) if np.isfinite(atr_r2) and atr_r2 > 0 else np.nan,
            low12=float(lo_px[0]), low23=float(lo_px[1]),
            consol_low=float(pinch_low), pinch_high=float(pinch_high), deepest_low=float(run_low),
            # impulses (grid buckets)
            left_impulse=float(left_impulse), left_bucket=_bucket(left_impulse),
            # retraces (first three + net)
            r1=float(retr[0]), r2=float(retr[1]), r3=float(retr[2]),
            r_last=float(retr[-1]), r_last_over_r1=float(retr[-1] / retr[0]) if retr[0] else np.nan,
            r2_over_r1=float(retr[1] / retr[0]) if retr[0] else np.nan,
            r3_over_r2=float(retr[2] / retr[1]) if retr[1] else np.nan,
            r1_height=float(retrace1_height),
            ascending_lows=bool(all(lows_all[i] < lows_all[i + 1] for i in range(len(lows_all) - 1))),
            # timing (hours)
            gap12_h=float(gaps_h[0]), gap23_h=float(gaps_h[1]),
            gap_taps_h_mean=float(np.mean(gaps_h)), span_h=float((last_bar - tap_bar[0]) / bph),
            gap3entry_h=float((entry_idx - last_bar) / bph),
            consol_bars=int(entry_idx - run_low_idx),
            # atr context
            atr_r1=float(atr_r1), atr_r2=float(atr_r2),
            atr_r1_pct=float(atr_r1 / tap_px[0]) if np.isfinite(atr_r1) else np.nan,
            atr_r2_pct=float(atr_r2 / tap_px[1]) if np.isfinite(atr_r2) else np.nan,
            # trade geometry
            entry=entry_price, stop=float(stop), stop_time_ms=int(stop_ms), take=float(take), rr=float(rr),
            dist_take_pct=float((take - entry_price) / entry_price),
            dist_stop_pct=float((entry_price - stop) / entry_price),
            # last-retrace activity vs BTC (second entry-validity input; gate 0.5x)
            retr_trades=retr_trades, btc_retr_trades=btc_retr_trades,
            trades_vs_btc=float(trades_vs_btc), trades_gt_btc=trades_gt_btc,
            trades_ge_half_btc=trades_ok,
            # quality metrics for eyeball calibration (not hard-gated yet)
            tap_wick_frac=float(tap_wick_frac),
            left_impulse_over_r1=float(left_impulse_over_r1),
            left_impulse_eff=float(left_impulse_eff), left_clear_ratio=float(left_clear_ratio),
            fresh_high=bool(fresh), hot_now=hot_now,
            trades24_vs_btc=float(tr24_vs_btc), anom_vol_24h=float(anom_vol_24h),
            pump_start_ms=int(ts[pump_start_idx]), ignition_rise=float(ignition_rise), ignition_bars=int(ignition_bars),
            pre_pump_atr_pct=float(pre_pump_atr_pct),
            retrace_eff_min=retrace_eff_min, retrace_eff_mean=retrace_eff_mean,
            pinch_range_pct=float(pinch_range_pct), pinch_bar_atr_pct=float(pinch_bar_atr_pct),
            # formation on raised volume vs prior sleep (H1 awakening) + breakout
            # "long initiative" volume (threshold provisional)
            formation_vol_ratio=float(formation_vol_ratio),
            brk_vol_ratio=float(brk_vol_ratio), brk_initiative=brk_initiative,
            pre_brk_vol_ramp=float(pre_brk_vol_ramp), vol_ramp_up=vol_ramp_up,
            # OR entry-validity: volume step (rank>=0.8) OR trades>=0.5*BTC
            vol_ok=vol_ok, valid_entry=bool(vol_ok or trades_ok),
            # volume regime step (4h)
            **vol,
        ))
    return out


DISCOVERY_OUT = Path(".output/results/triple_tap_v1/setups_discovery.parquet")


def _detect_cap(
    cols: dict[str, np.ndarray], symbol: str, tf: str, bar_minutes: int,
    btc_ts: np.ndarray, btc_tc: np.ndarray,
    fine_ts: np.ndarray, fine_qv: np.ndarray, fine_high: np.ndarray, fine_low: np.ndarray,
    end_ms: int | None, strict: bool = True,
) -> list[dict]:
    """CONSOLIDATION AFTER PULLBACK. Sleep -> pump -> single culmination high C
    (fresh, off a flat sleep) -> pullback -> a TIGHT 3-tap level L (fine swings,
    hours apart) in the UPPER HALF of the pullback -> break L on volume. Target =
    C + pullback depth. C is the only offer above; it is the measured-move anchor."""
    ts = cols["timestamp"]; open_ = cols["open"]; high = cols["high"]
    low = cols["low"]; close = cols["close"]; qv = cols["quote_volume"]; tc = cols["trade_count"]
    n = len(ts)
    if n < 50:
        return []
    bph = 60.0 / bar_minutes
    bar_ms = bar_minutes * 60_000
    vol_minutes = bar_minutes * VOL_TF_RATIO
    v4_ts, v4_v = _resample_vol(ts, qv, VOL_TF_RATIO)
    dormant_ms = DORMANT_REGIME_BARS * vol_minutes * 60_000
    stop_lookback_ms = STOP_LOOKBACK_BARS * bar_ms
    ramp_recent_ms, ramp_base_ms = RAMP_RECENT_BARS * bar_ms, RAMP_BASE_BARS * bar_ms
    consol_max = int(CAP_CONSOL_HOURS[tf] * bph)
    cg_min, cg_max = CAP_GAP_HOURS[tf][0] * bph, CAP_GAP_HOURS[tf][1] * bph

    coarse = _pct_zigzag(high, low, SWING_PCT)          # culmination candidates
    fine = _pct_zigzag(high, low, FINE_SWING_PCT)       # tight consolidation taps
    fh_idx = np.array([p[0] for p in fine if p[2] == "high"], dtype=np.int64)
    fh_px = np.array([p[1] for p in fine if p[2] == "high"], dtype=np.float64)
    out: list[dict] = []
    seen: set[int] = set()

    for cp in range(1, len(coarse)):
        if coarse[cp][2] != "high":
            continue
        _rej("cap", tf, "coarse_high_candidate")
        c_idx, c_px = coarse[cp][0], coarse[cp][1]
        # C must be a FRESH culmination: no coarse high clearly above it to the left
        look0 = max(0, c_idx - (SLEEP_BARS + consol_max))
        if any(coarse[k][2] == "high" and look0 <= coarse[k][0] < c_idx and coarse[k][1] > c_px * (1 + PROM_TOL)
               for k in range(cp)):
            _rej("cap", tf, "reject_overhead_before_culmination")
            continue
        # ...born of a PUMP off a FLAT sleep (else it's a grind / no culmination).
        # Base = lowest low over a generous window (captures the WHOLE pump, not an
        # intermediate low of a two-leg pump).
        ci0 = max(0, c_idx - SLEEP_BARS)
        cps_idx = ci0 + int(np.argmin(low[ci0:c_idx + 1]))
        if not (low[cps_idx] > 0 and (c_px - low[cps_idx]) / low[cps_idx] >= CAP_CULM_PUMP):
            _rej("cap", tf, "reject_weak_culmination_pump")
            continue
        sl0 = max(0, cps_idx - SLEEP_BARS)
        if cps_idx - sl0 < CAP_SLEEP_MIN_BARS:
            _rej("cap", tf, "reject_sleep_too_short")
            continue
        sleep_atr = float(np.median((high[sl0:cps_idx + 1] - low[sl0:cps_idx + 1]) / close[sl0:cps_idx + 1])) if cps_idx > sl0 else np.nan
        sleep_low = float(np.min(low[sl0:cps_idx + 1])) if cps_idx > sl0 else np.nan
        sleep_high = float(np.max(high[sl0:cps_idx + 1])) if cps_idx > sl0 else np.nan
        sleep_range = (sleep_high - sleep_low) / sleep_low if np.isfinite(sleep_low) and sleep_low > 0 else np.nan
        if not (np.isfinite(sleep_range) and sleep_range <= CAP_SLEEP_RANGE_MAX):
            _rej("cap", tf, "reject_sleep_range_too_wide")
            continue
        pump_bars = c_idx - cps_idx
        pump_hours = pump_bars / bph if bph > 0 else np.nan
        if not (pump_bars > 0 and np.isfinite(pump_hours) and pump_hours <= CAP_PUMP_MAX_HOURS[tf]):
            _rej("cap", tf, "reject_pump_too_slow")
            continue
        pump_path_eff = _path_efficiency(close, cps_idx, c_idx)
        if not (np.isfinite(pump_path_eff) and pump_path_eff >= CAP_PUMP_PATH_EFF_MIN):
            _rej("cap", tf, "reject_pump_not_vertical")
            continue
        sleep_vol_avg = float(np.mean(qv[sl0:cps_idx])) if cps_idx > sl0 else np.nan
        pump_vol_avg = float(np.mean(qv[cps_idx:c_idx + 1])) if c_idx >= cps_idx else np.nan
        pump_over_sleep_vol = pump_vol_avg / sleep_vol_avg if np.isfinite(sleep_vol_avg) and sleep_vol_avg > 0 else np.nan
        if not (np.isfinite(pump_over_sleep_vol) and pump_over_sleep_vol >= CAP_PUMP_VOL_OVER_SLEEP_MIN):
            _rej("cap", tf, "reject_no_pump_volume_step")
            continue
        sleep_trades_avg = float(np.mean(tc[sl0:cps_idx])) if cps_idx > sl0 else np.nan
        pump_trades_avg = float(np.mean(tc[cps_idx:c_idx + 1])) if c_idx >= cps_idx else np.nan
        pump_over_sleep_trades = pump_trades_avg / sleep_trades_avg if np.isfinite(sleep_trades_avg) and sleep_trades_avg > 0 else np.nan
        if not (np.isfinite(pump_over_sleep_trades) and pump_over_sleep_trades >= CAP_PUMP_TRADES_OVER_SLEEP_MIN):
            _rej("cap", tf, "reject_no_pump_trade_step")
            continue
        # PULLBACK from C: lowest low within the consolidation window
        pe = min(c_idx + consol_max, n - 1)
        if pe - c_idx < 6:
            _rej("cap", tf, "reject_not_enough_post_culmination_bars")
            continue
        pl_rel = int(np.argmin(low[c_idx + 1:pe + 1]))
        pl_idx = c_idx + 1 + pl_rel
        pl_px = float(low[pl_idx])
        pb_depth = c_px - pl_px
        if pb_depth <= 0 or pb_depth / c_px < CAP_MIN_PULLBACK:
            _rej("cap", tf, "reject_pullback_too_shallow")
            continue
        upper_half = c_px - 0.5 * pb_depth
        # fine consolidation highs AFTER the pullback low, in the UPPER HALF, below C
        m = (fh_idx > pl_idx) & (fh_idx <= pe) & (fh_px >= upper_half) & (fh_px < c_px * (1 - LEVEL_EPS))
        cand_idx = fh_idx[m]; cand_px = fh_px[m]
        if len(cand_idx) < MIN_TAPS:
            _rej("cap", tf, "reject_not_enough_upper_half_taps")
            continue
        # cluster into a LEVEL L: taps within CAP_LEVEL_TOL of a top price; pick the
        # highest level that has >=3 taps with gaps in the CAP range.
        chosen = None
        order = np.argsort(-cand_px)  # highest first
        for oi in order:
            top = cand_px[oi]
            g = [(int(cand_idx[j]), float(cand_px[j])) for j in range(len(cand_px))
                 if top * (1 - CAP_LEVEL_TOL) <= cand_px[j] <= top * (1 + LEVEL_EPS)]
            g.sort()
            if len(g) < MIN_TAPS:
                continue
            # keep the longest run with valid consecutive gaps
            run = [g[0]]
            for k in range(1, len(g)):
                if cg_min <= g[k][0] - run[-1][0] <= cg_max:
                    run.append(g[k])
                elif g[k][0] - run[-1][0] > cg_max:
                    if len(run) >= MIN_TAPS:
                        break
                    run = [g[k]]
            if len(run) >= MIN_TAPS:
                chosen = run[:MAX_TAPS]
                break
        if chosen is None:
            _rej("cap", tf, "reject_no_valid_tap_cluster")
            continue

        tap_bar = [t[0] for t in chosen]
        tap_px = [t[1] for t in chosen]
        level = max(tap_px)
        sleep_high_vs_level = (sleep_high - level) / level if np.isfinite(sleep_high) and level > 0 else np.nan
        if not (np.isfinite(sleep_high_vs_level) and sleep_high_vs_level <= -CAP_SLEEP_BELOW_LEVEL_MIN):
            _rej("cap", tf, "reject_sleep_not_below_level")
            continue
        core_taps_near_level = sum(px >= level * CAP_CORE_TAP_MIN_LEVEL for px in tap_px[:MIN_TAPS])
        if core_taps_near_level < 2 or tap_px[-1] < level * CAP_LAST_TAP_MIN_LEVEL:
            _rej("cap", tf, "reject_bad_core_or_last_tap_level")
            continue
        first_tap = tap_bar[0]
        last_tap = tap_bar[-1]
        prior_level_closes = close[pl_idx:last_tap]
        prior_close_above_level_count = int(np.sum(prior_level_closes > level)) if len(prior_level_closes) else 0
        if prior_close_above_level_count > 0:
            _rej("cap", tf, "reject_prior_close_above_level")
            continue
        # breakout: first CLOSE above the level after the last tap
        entry_idx = -1
        run_low = np.inf; run_low_idx = last_tap
        for j in range(last_tap + 1, min(last_tap + consol_max, n)):
            if low[j] < run_low:
                run_low, run_low_idx = float(low[j]), j
            if close[j] > level:
                entry_idx = j
                break
        if entry_idx < 0 or entry_idx >= n:
            _rej("cap", tf, "reject_no_breakout_close")
            continue
        if high[c_idx + 1:entry_idx + 1].max(initial=0.0) > c_px * (1 + LEVEL_EPS):
            _rej("cap", tf, "reject_culmination_not_highest")
            continue
        level_age_share = (entry_idx - first_tap) / (entry_idx - c_idx) if entry_idx > c_idx else np.nan
        if not (np.isfinite(level_age_share) and level_age_share > CAP_LEVEL_AGE_SHARE_MIN):
            _rej("cap", tf, "reject_level_too_young")
            continue
        entry_ms = int(ts[entry_idx])
        if (end_ms is not None and entry_ms >= end_ms) or entry_ms in seen:
            _rej("cap", tf, "reject_boundary_or_duplicate_entry")
            continue
        seen.add(entry_ms)

        # per-tap retraces (small, inside the consolidation)
        lo_bar, lo_px = [], []
        for k in range(len(tap_bar) - 1):
            seg = low[tap_bar[k]:tap_bar[k + 1] + 1]
            li = tap_bar[k] + int(np.argmin(seg))
            lo_bar.append(li); lo_px.append(float(low[li]))
        lo_bar.append(run_low_idx); lo_px.append(run_low)
        support = _support_line_metrics(lo_bar, lo_px, level=level)
        if not (
            np.isfinite(support["support_lows_last_over_first"])
            and support["support_lows_last_over_first"] >= CAP_SUPPORT_LAST_OVER_FIRST_MIN
            and np.isfinite(support["support_lows_max_resid_pct"])
            and support["support_lows_max_resid_pct"] <= CAP_SUPPORT_MAX_RESID_PCT
            and np.isfinite(support["support_lows_slope_pct_per_bar"])
            and support["support_lows_slope_pct_per_bar"] >= CAP_SUPPORT_MIN_SLOPE_PCT_PER_BAR
        ):
            _rej("cap", tf, "reject_bad_support_lows")
            continue
        retr = [(tap_px[k] - lo_px[k]) / tap_px[k] for k in range(len(tap_px))]

        entry_price = float(level)
        take = float(c_px + pb_depth)                    # culmination + pullback depth
        stop, stop_ms = _structural_stop(fine_ts, fine_high, fine_low, entry_ms, stop_lookback_ms)
        if not (np.isfinite(stop) and stop < entry_price):
            _rej("cap", tf, "reject_no_structural_stop")
            continue
        rr = (take - entry_price) / (entry_price - stop) if entry_price > stop else np.nan

        vol = _volume_step(v4_ts, v4_v, entry_ms)
        vol_ok = bool(np.isfinite(vol["vol_pct_rank"]) and vol["vol_pct_rank"] >= VOL_RANK_GATE)
        retr_trades = float(tc[last_tap:run_low_idx + 1].sum())
        btc_retr = _sum_between(btc_ts, btc_tc, int(ts[last_tap]), int(ts[run_low_idx]))
        trades_vs_btc = retr_trades / btc_retr if btc_retr > 0 else np.nan
        trades_ok = bool(np.isfinite(trades_vs_btc) and trades_vs_btc >= TRADES_VS_BTC_GATE)
        w0 = max(0, entry_idx - BRK_WIN)
        base_v = float(np.median(qv[w0:entry_idx])) if entry_idx > w0 else np.nan
        brk_vol_ratio = float(qv[entry_idx] / base_v) if np.isfinite(base_v) and base_v > 0 else np.nan
        # CAUSAL volume: the 1m volume must RISE into the break ("цена летит на
        # алгоритме") - a SHORT 1m window BEFORE entry. The breakout-CANDLE volume
        # is look-ahead (the candle isn't closed at the entry decision).
        pre_brk_vol_ramp = _pre_breakout_ramp(fine_ts, fine_qv, entry_ms, CAP_RAMP_RECENT_MS, CAP_RAMP_BASE_MS)
        if not (np.isfinite(pre_brk_vol_ramp) and pre_brk_vol_ramp >= CAP_RAMP_MIN):
            _rej("cap", tf, "reject_pre_break_ramp")
            continue
        formation_vol_ratio = _formation_awakening(v4_ts, v4_v, int(ts[cps_idx]), entry_ms, dormant_ms)
        c_rise = (c_px - low[cps_idx]) / low[cps_idx]
        gaps_h = [(tap_bar[k + 1] - tap_bar[k]) / bph for k in range(len(tap_bar) - 1)]

        out.append(dict(
            discovery=bool(not strict),
            setup_family="cap", setup_type="consol_after_pullback", culmination=float(c_px),
            symbol=symbol, tf=tf, n_taps=int(len(tap_bar)), n_viol=0,
            tap_times_ms=[int(ts[b]) for b in tap_bar], tap_prices=[float(p) for p in tap_px],
            retrace_low_times_ms=[int(ts[b]) for b in lo_bar], retrace_lows=[float(x) for x in lo_px],
            retraces=[float(r) for r in retr],
            h1_time_ms=int(ts[tap_bar[0]]), h2_time_ms=int(ts[tap_bar[1]]), h3_time_ms=int(ts[tap_bar[2]]),
            low12_time_ms=int(ts[lo_bar[0]]), low23_time_ms=int(ts[lo_bar[1]]),
            consol_low_time_ms=int(ts[run_low_idx]), entry_time_ms=entry_ms,
            h1=float(tap_px[0]), h2=float(tap_px[1]), h3=float(tap_px[2]), level=float(level),
            level_disp=float(max(tap_px) - min(tap_px)), level_disp_atr=np.nan,
            low12=float(lo_px[0]), low23=float(lo_px[1]),
            consol_low=float(run_low), pinch_high=np.nan, deepest_low=float(run_low),
            left_impulse=float(c_rise), left_bucket=_bucket(c_rise),
            r1=float(retr[0]), r2=float(retr[1]), r3=float(retr[2]),
            r_last=float(retr[-1]), r_last_over_r1=float(retr[-1] / retr[0]) if retr[0] else np.nan,
            r2_over_r1=float(retr[1] / retr[0]) if retr[0] else np.nan,
            r3_over_r2=float(retr[2] / retr[1]) if retr[1] else np.nan,
            r1_height=float(tap_px[0] - lo_px[0]),
            ascending_lows=bool(all(lo_px[k] < lo_px[k + 1] for k in range(len(lo_px) - 1))),
            gap12_h=float(gaps_h[0]), gap23_h=float(gaps_h[1]),
            gap_taps_h_mean=float(np.mean(gaps_h)), span_h=float((last_tap - tap_bar[0]) / bph),
            gap3entry_h=float((entry_idx - last_tap) / bph), consol_bars=int(entry_idx - run_low_idx),
            atr_r1=np.nan, atr_r2=np.nan, atr_r1_pct=np.nan, atr_r2_pct=np.nan,
            entry=entry_price, stop=float(stop), stop_time_ms=int(stop_ms), take=float(take), rr=float(rr),
            dist_take_pct=float((take - entry_price) / entry_price),
            dist_stop_pct=float((entry_price - stop) / entry_price),
            retr_trades=retr_trades, btc_retr_trades=btc_retr,
            trades_vs_btc=float(trades_vs_btc), trades_gt_btc=bool(symbol != BTC_SYMBOL and retr_trades > btc_retr),
            trades_ge_half_btc=trades_ok,
            # C-anchored quality metrics (the pump is into the culmination)
            tap_wick_frac=np.nan, left_impulse_over_r1=float(c_rise / retr[0]) if retr[0] else np.nan,
            left_impulse_eff=np.nan, left_clear_ratio=np.nan,
            fresh_high=False, hot_now=False, trades24_vs_btc=np.nan, anom_vol_24h=np.nan,
            pump_start_ms=int(ts[cps_idx]), ignition_rise=float(c_rise), ignition_bars=int(c_idx - cps_idx),
            pre_pump_atr_pct=float(sleep_atr),
            sleep_range_pct=float(sleep_range),
            sleep_high_vs_level=float(sleep_high_vs_level),
            pump_path_eff=float(pump_path_eff),
            pump_hours=float(pump_hours),
            pump_over_sleep_vol=float(pump_over_sleep_vol),
            pump_over_sleep_trades=float(pump_over_sleep_trades),
            level_age_share=float(level_age_share),
            **support,
            core_taps_near_level=int(core_taps_near_level),
            last_tap_level_ratio=float(tap_px[-1] / level) if level > 0 else np.nan,
            prior_close_above_level_count=int(prior_close_above_level_count),
            retrace_eff_min=np.nan, retrace_eff_mean=np.nan,
            pinch_range_pct=np.nan, pinch_bar_atr_pct=np.nan,
            formation_vol_ratio=float(formation_vol_ratio),
            brk_vol_ratio=float(brk_vol_ratio), brk_initiative=bool(np.isfinite(brk_vol_ratio) and brk_vol_ratio >= BRK_VOL_MULT),
            pre_brk_vol_ramp=float(pre_brk_vol_ramp), vol_ramp_up=bool(np.isfinite(pre_brk_vol_ramp) and pre_brk_vol_ramp >= VOL_RAMP_MULT),
            vol_ok=vol_ok, valid_entry=True,   # passed the causal pre-break 1m ramp gate
            **vol,
        ))
        _rej("cap", tf, "accepted")
    return out


def build_setups(
    symbols: list[str] | None = None,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    out_path: Path = DEFAULT_OUT,
    end_ms: int | None = DEV_END_MS,
    strict: bool = True,
) -> pd.DataFrame:
    if symbols is None:
        symbols = sorted(p.stem for p in cache_dir.glob("*.parquet"))
    REJECT.clear()
    rows: list[dict] = []
    total = len(symbols)
    for k, sym in enumerate(symbols, 1):
        try:
            rows.extend(find_setups(sym, cache_dir=cache_dir, end_ms=end_ms, strict=strict))
        except Exception as exc:  # keep going; report at the end
            print(f"  SKIP {sym}: {exc}", flush=True)
        if k % 100 == 0 or k == total:
            print(f"  {k}/{total} symbols, {len(rows)} setups", flush=True)
    table = pd.DataFrame(rows).sort_values(["symbol", "entry_time_ms"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out_path, index=False)
    reject_path = out_path.with_name(f"{out_path.stem}_rejects.json")
    reject_payload = {
        "out_path": str(out_path),
        "strict": strict,
        "symbols_requested": total,
        "setups_saved": int(len(table)),
        "rejects": dict(sorted(REJECT.items())),
    }
    reject_path.write_text(json.dumps(reject_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(table)} {'strict' if strict else 'DISCOVERY'} setups -> {out_path}", flush=True)
    print(f"saved detector reject accounting -> {reject_path}", flush=True)
    return table


def main() -> None:
    build_setups(strict=True, out_path=DEFAULT_OUT)
    build_setups(strict=False, out_path=DISCOVERY_OUT)


if __name__ == "__main__":
    main()
