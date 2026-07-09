"""Comprehensive CAUSAL feature set for triple-tap / CAP setups.

Every feature here is known at the entry decision (uses only bars up to the
breakout) - nothing from the breakout candle's completion or the future. Built
post-hoc from each setup's stored structure (taps, retrace lows, level,
culmination, pump-start) plus the reloaded TF/1m arrays, so we can add features
without re-running the detector. Grouped by the user's request:

  - retrace legs split into DOWN (level->low) and UP (low->next touch): cleanliness
    (path efficiency), verticality, ATR, aggression, duration, volume, trades
  - do the retrace LOWS lie on one line; how the HIGHS relate (slope / dispersion)
  - time gaps between highs; how highs cross the level (wicks)
  - level as the (trimmed) MEAN of highs vs the max
  - volume at the lows; retrace volume ratios
  - CAP: distance level->culmination, pullback depth, upper-half position, pump
  - avg volume sleep / post-pump / pump->high; trades per segment; trades vs BTC
  - context: TF, independent setup family, setup type, hour, day-of-week, BTC regime
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.detect import CACHE_1M, TFS, _btc_trades, _load_1m, _resample_np, _sum_between

DEFAULT_SETUPS = Path(".output/results/triple_tap_v1/setups_discovery.parquet")
DEFAULT_OUT = Path(".output/results/triple_tap_v1/features.parquet")
CATEGORICAL = ["tf", "setup_family", "setup_type"]


def _idx(ts: np.ndarray, t_ms: int) -> int:
    return int(np.searchsorted(ts, t_ms, side="left"))


def _leg(h, l, c, qv, tc, a: int, b: int, prefix: str) -> dict:
    """Metrics for a price leg [a, b] (a<b): cleanliness / verticality / ATR /
    aggression / duration / volume / trades."""
    a, b = int(min(a, b)), int(max(a, b))
    if b <= a:
        return {f"{prefix}_{k}": np.nan for k in ("eff", "vert", "atr", "aggr", "dur", "vol", "trades")}
    cc = c[a:b + 1]
    net = abs(float(cc[-1] - cc[0]))
    path = float(np.sum(np.abs(np.diff(cc))))
    dur = b - a
    rng = h[a:b + 1] - l[a:b + 1]
    return {
        f"{prefix}_eff": net / path if path > 0 else np.nan,                 # path efficiency (clean vs choppy)
        f"{prefix}_vert": (net / cc[0]) / dur if dur > 0 and cc[0] > 0 else np.nan,  # % move per bar (verticality)
        f"{prefix}_atr": float(np.mean(rng / cc)) if len(cc) else np.nan,    # mean bar range %
        f"{prefix}_aggr": float(np.mean(np.abs(np.diff(cc)))) / float(np.mean(rng)) if np.mean(rng) > 0 else np.nan,
        f"{prefix}_dur": float(dur),
        f"{prefix}_vol": float(np.sum(qv[a:b + 1])),
        f"{prefix}_trades": float(np.sum(tc[a:b + 1])),
    }


def _line_fit(idx: np.ndarray, px: np.ndarray, atr: float) -> tuple[float, float]:
    """Slope (% per bar) and residual dispersion (in ATR units) of points on a line."""
    if len(idx) < 2:
        return np.nan, np.nan
    x = idx.astype(float) - idx[0]
    coef = np.polyfit(x, px, 1)
    slope = coef[0] / px.mean() if px.mean() > 0 else np.nan
    resid = px - np.polyval(coef, x)
    return float(slope), float(np.std(resid) / atr) if atr > 0 else np.nan


def _features_for(r, ts, o, h, l, c, qv, tc, btc_ts, btc_tc) -> dict:
    tap_b = np.array([_idx(ts, int(t)) for t in r.tap_times_ms])
    tap_p = np.array([float(p) for p in r.tap_prices])
    lo_b = np.array([_idx(ts, int(t)) for t in r.retrace_low_times_ms])
    lo_p = np.array([float(p) for p in r.retrace_lows])
    entry_b = _idx(ts, int(r.entry_time_ms))
    level = float(r.level)
    atr = float(np.mean((h[max(0, tap_b[0]):entry_b + 1] - l[max(0, tap_b[0]):entry_b + 1]))) or level * 0.01
    f: dict = {}

    # --- highs: slope / dispersion / how they relate ---
    hs, hres = _line_fit(tap_b, tap_p, atr)
    f["hi_slope"] = hs                                  # <0 decaying, >0 rising, ~0 flat
    f["hi_resid_atr"] = hres                            # dispersion (noisy vs on a line)
    f["hi_spread_pct"] = (tap_p.max() - tap_p.min()) / level
    f["hi_spread_atr"] = (tap_p.max() - tap_p.min()) / atr
    f["hi_first_unbroken"] = (tap_p.max() - tap_p[0]) / tap_p[0]
    trimmed = tap_p[(tap_p >= np.percentile(tap_p, 15)) & (tap_p <= np.percentile(tap_p, 85))]
    lvl_mean = float(trimmed.mean()) if len(trimmed) else float(tap_p.mean())
    f["level_vs_mean"] = (level - lvl_mean) / level     # max vs trimmed-mean level (anomalous spikes)
    f["n_taps"] = float(len(tap_b))

    # --- time gaps between highs ---
    gaps = np.diff(tap_b).astype(float)
    f["gap_mean"] = float(gaps.mean()) if len(gaps) else np.nan
    f["gap_std"] = float(gaps.std()) if len(gaps) else np.nan
    f["gap_cv"] = float(gaps.std() / gaps.mean()) if len(gaps) and gaps.mean() > 0 else np.nan
    f["gap_min"] = float(gaps.min()) if len(gaps) else np.nan
    f["gap_max"] = float(gaps.max()) if len(gaps) else np.nan

    # --- how highs cross the level: tap upper wicks + breakout push ---
    wf = []
    for tb in tap_b:
        rng = h[tb] - l[tb]
        if rng > 0:
            wf.append((h[tb] - max(o[tb], c[tb])) / rng)
    f["tap_wick_mean"] = float(np.mean(wf)) if wf else np.nan
    f["tap_wick_max"] = float(np.max(wf)) if wf else np.nan
    f["brk_close_over_lvl"] = (c[entry_b] - level) / level if entry_b < len(c) else np.nan
    f["brk_wick"] = (h[entry_b] - max(o[entry_b], c[entry_b])) / (h[entry_b] - l[entry_b]) if entry_b < len(h) and h[entry_b] > l[entry_b] else np.nan

    # --- retrace LOWS on one line? ---
    ls, lres = _line_fit(lo_b, lo_p, atr)
    f["lows_slope"] = ls                                # ascending support >0
    f["lows_resid_atr"] = lres
    f["lows_spread_atr"] = (lo_p.max() - lo_p.min()) / atr

    # --- retrace depths ---
    retr = np.array([float(x) for x in r.retraces])
    f["r_first"] = float(retr[0])
    f["r_last"] = float(retr[-1])
    f["r_mean"] = float(retr.mean())
    f["r_last_over_first"] = float(retr[-1] / retr[0]) if retr[0] else np.nan
    f["r_monotonic"] = float(np.mean(np.diff(retr) <= 0)) if len(retr) > 1 else np.nan  # fraction shrinking

    # --- DOWN legs (level->low) and UP legs (low->next touch), aggregated ---
    down, up = [], []
    for k in range(len(tap_b) - 1):
        down.append(_leg(h, l, c, qv, tc, tap_b[k], lo_b[k], "d"))
        up.append(_leg(h, l, c, qv, tc, lo_b[k], tap_b[k + 1], "u"))
    down.append(_leg(h, l, c, qv, tc, tap_b[-1], lo_b[-1], "d"))  # last down into consol low
    for name, legs in (("down", down), ("up", up)):
        for key in ("eff", "vert", "atr", "aggr", "dur", "vol", "trades"):
            vals = [d[f"{'d' if name == 'down' else 'u'}_{key}"] for d in legs]
            vals = [v for v in vals if np.isfinite(v)]
            f[f"{name}_{key}_mean"] = float(np.mean(vals)) if vals else np.nan
            f[f"{name}_{key}_last"] = float(vals[-1]) if vals else np.nan
    # volume on rebounds vs drops
    dv = f.get("down_vol_mean", np.nan)
    uv = f.get("up_vol_mean", np.nan)
    f["up_down_vol_ratio"] = uv / dv if np.isfinite(dv) and dv > 0 else np.nan

    # --- volume AT the lows (relative to the down leg) ---
    lowvol = []
    for k in range(len(lo_b)):
        lb = lo_b[k]
        seg0 = tap_b[k] if k < len(tap_b) else tap_b[-1]
        segv = np.mean(qv[min(seg0, lb):max(seg0, lb) + 1])
        if segv > 0 and lb < len(qv):
            lowvol.append(qv[lb] / segv)
    f["low_vol_rel_mean"] = float(np.mean(lowvol)) if lowvol else np.nan

    # --- CAP geometry ---
    culm = float(r.culmination) if np.isfinite(r.culmination) else np.nan
    f["is_cap"] = float(r.setup_type == "consol_after_pullback")
    if np.isfinite(culm):
        f["lvl_to_culm"] = (culm - level) / level
        pl = float(lo_p.min())
        f["pullback_depth"] = (culm - pl) / culm if culm > 0 else np.nan
        f["upper_half_pos"] = (level - pl) / (culm - pl) if culm > pl else np.nan
    else:
        f["lvl_to_culm"] = f["pullback_depth"] = f["upper_half_pos"] = np.nan

    # --- daily-scale volume: sleep vs pump vs formation ---
    ps_b = _idx(ts, int(r.pump_start_ms))
    def _avgvol(a, b):
        a, b = max(0, int(a)), min(len(qv), int(b))
        return float(np.mean(qv[a:b])) if b > a else np.nan
    sleep_v = _avgvol(ps_b - 120, ps_b)
    pump_v = _avgvol(ps_b, tap_b[0])
    form_v = _avgvol(tap_b[0], entry_b)
    f["pump_over_sleep_vol"] = pump_v / sleep_v if np.isfinite(sleep_v) and sleep_v > 0 else np.nan
    f["form_over_sleep_vol"] = form_v / sleep_v if np.isfinite(sleep_v) and sleep_v > 0 else np.nan
    f["form_over_pump_vol"] = form_v / pump_v if np.isfinite(pump_v) and pump_v > 0 else np.nan

    # --- trades vs BTC on the down / up / breakout-approach segments (wall clock) ---
    def _tr_vs_btc(a, b):
        a, b = int(min(a, b)), int(max(a, b))
        if b <= a:
            return np.nan
        coin = float(np.sum(tc[a:b + 1]))
        btc = _sum_between(btc_ts, btc_tc, int(ts[a]), int(ts[b]))
        return coin / btc if btc > 0 else np.nan
    f["trvbtc_formation"] = _tr_vs_btc(tap_b[0], entry_b)
    f["trvbtc_pump"] = _tr_vs_btc(ps_b, tap_b[0])
    f["trvbtc_lastleg"] = _tr_vs_btc(tap_b[-1], entry_b)

    # --- passthrough causal metrics already on the setup ---
    for col in ("pre_brk_vol_ramp", "formation_vol_ratio", "vol_pct_rank", "vol_step_ratio",
                "trades_vs_btc", "left_impulse", "ignition_rise", "pre_pump_atr_pct",
                "sleep_range_pct", "sleep_high_vs_level", "core_taps_near_level",
                "last_tap_level_ratio", "prior_close_above_level_count",
                "pump_path_eff", "pump_hours", "pump_over_sleep_vol",
                "pump_over_sleep_trades", "level_age_share",
                "support_lows_slope_pct_per_bar", "support_lows_max_resid_pct",
                "support_lows_last_over_first",
                "span_h", "dist_stop_pct", "dist_take_pct", "rr", "left_clear_ratio"):
        f[col] = float(getattr(r, col)) if np.isfinite(getattr(r, col, np.nan)) else np.nan

    # --- context ---
    stamp = pd.Timestamp(int(r.entry_time_ms), unit="ms", tz="UTC")
    f["hour"] = float(stamp.hour)
    f["dow"] = float(stamp.dayofweek)
    f["tf"] = r.tf
    f["setup_family"] = getattr(
        r,
        "setup_family",
        "cap" if r.setup_type == "consol_after_pullback" else "breakout",
    )
    f["setup_type"] = r.setup_type
    return f


def build_features(setups_path: Path = DEFAULT_SETUPS, out_path: Path = DEFAULT_OUT) -> pd.DataFrame:
    setups = pd.read_parquet(setups_path)
    setups = setups[setups["valid_entry"]].reset_index(drop=True)
    btc_ts, btc_tc = _btc_trades(str(Path(".output/market/binance_vision/um_futures/enriched_15m")))
    rows: list[dict] = []
    total = setups["symbol"].nunique()
    for k, (symbol, g) in enumerate(setups.groupby("symbol"), 1):
        try:
            base = _load_1m(CACHE_1M / f"{symbol}.parquet")
        except Exception as exc:
            print(f"  SKIP {symbol}: {exc}", flush=True)
            continue
        by_tf = {tf: _resample_np(base, TFS[tf]) if TFS[tf] != 1 else base for tf in g["tf"].unique()}
        for r in g.itertuples(index=False):
            cols = by_tf[r.tf]
            feats = _features_for(r, cols["timestamp"], cols["open"], cols["high"], cols["low"],
                                  cols["close"], cols["quote_volume"], cols["trade_count"], btc_ts, btc_tc)
            feats.update(symbol=symbol, entry_time_ms=int(r.entry_time_ms), tf_key=r.tf)
            rows.append(feats)
        if k % 100 == 0 or k == total:
            print(f"  {k}/{total} symbols, {len(rows)} feature rows", flush=True)
    feat = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    feat.to_parquet(out_path, index=False)
    print(f"saved {len(feat)} feature rows ({feat.shape[1]} cols) -> {out_path}", flush=True)
    return feat


def main() -> None:
    build_features()


if __name__ == "__main__":
    main()
