"""Bee-bite spring detection, outcomes, and spring-success metrics.

Per qualified pump (from the runner lattice), find its high, a tight
consolidation range beneath it, then the first SWEEP below the range low that
RECLAIMS within a few bars. Enter the reclaim; stop either under the sweep WICK
low or under the sweep candle BODY low (tighter); target the range high
("перехай") or a fixed R-multiple, or trail. We record the character of the
grab AND the range geometry (swing counts, ascending/descending slopes) so the
online question - will the sweep reclaim into a new high? - is predictable from
candles alone. Reuses the Core simulator and the shared metrics.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import STATUS_FILLED, causal_atr, simulate_long_path
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.research.metrics import dissect_wins_losses, summarize
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

# Frozen detection constants (registered before inspecting outcomes).
CONSOL_BARS = 30
SWEEP_WINDOW = 90
RECLAIM_LIMIT = 6
MICRO_OFFSET = 0.001
MAX_RANGE_FRAC = 0.20
HOLD_RATIO = 0.5
NEW_HIGH_EPS = 0.002
DORMANT_BARS = 240
ACT_WIN = 15  # bars each side of the sweep for before/after activity

TOUCH = PumpLongExecutionSpec(stop_trigger_close_beyond=False)  # wick/body: intrabar touch
CLOSE = PumpLongExecutionSpec()                                 # range: exit on CLOSE below box
EARLY_K = 5           # bars: cut fast-failers that close back below the box
EARLY_COST = 0.003    # honest ~30bps round trip for the research early-exit sim
FLOW_CHECK = 3        # bar to judge post-entry taker-buy aggression (causal)


def _sim_flow(h, low, c, vol, tk, atr, ei, entry, sweep_low, tbr_base, target, trail):
    """Causal flow-managed long: ride to target/trail with the sweep-low stop,
    but at bar ei+FLOW_CHECK judge the taker-buy share SO FAR; if buyers did not
    show up (share < the consolidation baseline) cut at that close. Uses only
    bars up to the decision bar - no look-ahead."""
    n = len(c); stop = sweep_low; pl = np.inf; pa = np.nan
    chk = min(ei + FLOW_CHECK, n - 1)
    for j in range(ei, n):
        if float(low[j]) <= stop:
            return (stop / entry - 1.0) - EARLY_COST
        if not trail and target is not None and float(h[j]) >= target:
            return (target / entry - 1.0) - EARLY_COST
        if j == chk:
            v = float(vol[ei:chk + 1].sum())
            share = float(tk[ei:chk + 1].sum()) / v if v > 0 else 0.0
            if np.isfinite(tbr_base) and share < tbr_base:
                return (float(c[j]) / entry - 1.0) - EARLY_COST
        if trail:
            if float(low[j]) < pl:
                pl = float(low[j]); pa = float(atr[j])
            if np.isfinite(pa) and pa > 0 and (float(h[j]) - pl) >= 2.5 * pa:
                if stop < pl < float(c[j]):
                    stop = pl
                pl = float(low[j]); pa = float(atr[j])
    return (float(c[-1]) / entry - 1.0) - EARLY_COST


def _sim_early(h, low, c, atr, ei, entry, sweep_low, range_low, target, trail):
    """Early-cut long: if a close falls back below the range low within the
    first EARLY_K bars, exit there (cheap, near breakeven). Otherwise ride to
    `target` (or a structural swing-low trail) with the sweep-low stop."""
    n = len(c); stop = sweep_low; pl = np.inf; pa = np.nan
    for j in range(ei, n):
        if j < ei + EARLY_K and float(c[j]) < range_low:
            return (float(c[j]) / entry - 1.0) - EARLY_COST
        if float(low[j]) <= stop:
            return (stop / entry - 1.0) - EARLY_COST
        if not trail and target is not None and float(h[j]) >= target:
            return (target / entry - 1.0) - EARLY_COST
        if trail:
            if float(low[j]) < pl:
                pl = float(low[j]); pa = float(atr[j])
            if np.isfinite(pa) and pa > 0 and (float(h[j]) - pl) >= 2.5 * pa:
                if stop < pl < float(c[j]):
                    stop = pl
                pl = float(low[j]); pa = float(atr[j])
    return (float(c[-1]) / entry - 1.0) - EARLY_COST
# stop variants x target variants -> net_<stop>_<target>
STOPS = ("wick", "body", "range")
TARGETS = ("perehai", "2R", "3R", "trail")
METRICS = [
    "pump_size", "range_tightness", "consol_len", "n_swing_lows", "n_swing_highs",
    "highs_slope", "lows_slope", "reclaim_bars", "sweep_wick_frac", "sweep_depth_frac",
    "sweep_vol_ratio", "reclaim_vol_ratio", "reclaim_close_loc", "vol_after_vs_before",
    "trade_after_vs_before", "trade_flow_after", "vol_pump_vs_dormant", "held_ratio",
    "n_prior_sweeps",
]


def _slope(idx, vals, norm):
    if len(vals) < 2 or norm <= 0:
        return 0.0
    a = np.polyfit(np.asarray(idx, float), np.asarray(vals, float), 1)[0]
    return float(a / norm)


def _swings(low, high, s, e):
    """1-bar pivots between s and e; returns (n_lows, n_highs, low_slope, high_slope)."""
    li, lv, hi, hv = [], [], [], []
    for p in range(s + 1, e):
        if low[p] <= low[p - 1] and low[p] <= low[p + 1]:
            li.append(p); lv.append(float(low[p]))
        if high[p] >= high[p - 1] and high[p] >= high[p + 1]:
            hi.append(p); hv.append(float(high[p]))
    norm = float(high[s:e + 1].max()) if e > s else 1.0
    return len(lv), len(hv), _slope(li, lv, norm), _slope(hi, hv, norm)


def _detect(o, h, low, c, vol, tc, tk, hi_idx, H, B, pump_elapsed):
    hold = B + HOLD_RATIO * (H - B)
    n = len(c)
    consol_end = hi_idx + CONSOL_BARS
    if consol_end + 5 >= n:
        return None
    seg_lo = low[hi_idx + 1:consol_end + 1]; seg_hi = h[hi_idx + 1:consol_end + 1]
    if len(seg_lo) < CONSOL_BARS // 2 or c[hi_idx + 1:consol_end + 1].min() < hold:
        return None
    range_low = float(seg_lo.min())
    range_high = max(float(seg_hi.max()), float(H))
    range_height = range_high - range_low
    if range_height <= 0 or range_height / range_high > MAX_RANGE_FRAC:
        return None
    consol_vol = float(vol[hi_idx + 1:consol_end + 1].mean()) + 1e-9
    consol_tc = float(tc[hi_idx + 1:consol_end + 1].mean()) + 1e-9

    span = int(pump_elapsed) if np.isfinite(pump_elapsed) and pump_elapsed >= 2 else CONSOL_BARS
    p0 = max(0, hi_idx - span)
    dorm = vol[max(0, p0 - DORMANT_BARS):p0]; dorm_m = float(dorm.mean()) if len(dorm) else np.nan
    vpd = (float(vol[p0:hi_idx + 1].mean()) / dorm_m) if np.isfinite(dorm_m) and dorm_m > 0 else np.nan

    n_prior_sweeps = 0
    for k in range(consol_end + 1, min(consol_end + SWEEP_WINDOW, n - 1) + 1):
        if float(c[k]) < hold:
            return None
        if float(low[k]) < range_low:
            sweep_low = float(low[k]); depth = range_low - sweep_low
            if depth >= range_height:
                return None
            for m in range(k, min(k + RECLAIM_LIMIT, n - 1) + 1):
                if float(c[m]) > range_low * (1 + MICRO_OFFSET):
                    ei = m + 1
                    if ei >= n:
                        return None
                    entry = float(o[ei])
                    body_low = min(float(o[k]), float(c[k]))
                    if not (sweep_low < entry < range_high) or not (body_low < entry):
                        return None
                    # post-entry path (for the winner/loser behavior study &
                    # early-exit rule): excursions and how fast it fails.
                    e = entry
                    def _exc(kk):
                        sh = h[ei:min(ei + kk, n)]; slw = low[ei:min(ei + kk, n)]
                        if len(sh) == 0:
                            return 0.0, 0.0
                        return (float(sh.max()) - e) / e, (e - float(slw.min())) / e
                    mfe5, mae5 = _exc(5); mfe10, mae10 = _exc(10)
                    ret5 = float(c[min(ei + 4, n - 1)]) / e - 1.0
                    fail_bar = np.nan
                    for q in range(ei, min(ei + 30, n)):
                        if float(c[q]) < range_low:
                            fail_bar = q - ei; break
                    # long-aggression (taker BUY share; liquidations unavailable
                    # on free data) at the grab, the reclaim, and right after entry.
                    def _tbr(a, b):
                        v = float(vol[a:b].sum())
                        return float(tk[a:b].sum() / v) if v > 0 else np.nan
                    tbr_base = _tbr(hi_idx + 1, consol_end + 1)
                    tbr_after = _tbr(ei, min(ei + 5, n))
                    sweep_close_pos = (float(c[k]) - float(low[k])) / (float(h[k]) - float(low[k]) + 1e-12)
                    reclaim_body = (float(c[m]) - float(o[m])) / (float(h[m]) - float(low[m]) + 1e-12)
                    nl, nh, sl_slope, sh_slope = _swings(low, h, hi_idx + 1, k)
                    a_before = float(vol[max(hi_idx, k - ACT_WIN):k].mean()) + 1e-9
                    a_after = float(vol[k:min(k + ACT_WIN, n)].mean())
                    t_before = float(tc[max(hi_idx, k - ACT_WIN):k].mean()) + 1e-9
                    t_after = float(tc[k:min(k + ACT_WIN, n)].mean())
                    cw = float(h[k]) - float(low[k]) + 1e-12
                    return {
                        "entry_index": ei, "wick_stop": sweep_low, "body_stop": body_low,
                        "range_stop": range_low, "range_high": range_high,
                        "pump_size": (H - B) / B, "range_tightness": range_height / range_high,
                        "consol_len": k - hi_idx, "n_swing_lows": nl, "n_swing_highs": nh,
                        "highs_slope": sh_slope, "lows_slope": sl_slope, "reclaim_bars": m - k,
                        "sweep_wick_frac": (min(float(o[k]), float(c[k])) - float(low[k])) / cw,
                        "sweep_depth_frac": depth / range_height,
                        "sweep_vol_ratio": float(vol[k]) / consol_vol,
                        "reclaim_vol_ratio": float(vol[m]) / consol_vol,
                        "reclaim_close_loc": (float(c[m]) - range_low) / range_height,
                        "vol_after_vs_before": a_after / a_before,
                        "trade_after_vs_before": t_after / t_before,
                        "trade_flow_after": t_after / consol_tc,
                        "vol_pump_vs_dormant": vpd, "held_ratio": (range_low - B) / (H - B + 1e-12),
                        "n_prior_sweeps": n_prior_sweeps,
                        "mfe5": mfe5, "mae5": mae5, "mfe10": mfe10, "mae10": mae10,
                        "ret5": ret5, "fail_bar": fail_bar,
                        "taker_sweep": float(tk[k] / (vol[k] + 1e-9)),
                        "taker_reclaim": float(tk[m] / (vol[m] + 1e-9)),
                        "taker_after": tbr_after, "taker_delta": tbr_after - tbr_base,
                        "sweep_close_pos": sweep_close_pos, "reclaim_body": reclaim_body,
                        "tbr_base": tbr_base,
                    }
            n_prior_sweeps += 1
    return None


def build_spring_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                          end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    idx = lat.groupby("group")["anchor_high"].idxmax()
    ev = lat.loc[idx, ["group", "symbol", "snapshot_time_ms", "anchor_high", "base_level",
                       "pump_elapsed_min"]].reset_index(drop=True)
    stamp = pd.to_datetime(ev["snapshot_time_ms"], unit="ms", utc=True)
    ev["mo"] = stamp.dt.strftime("%Y-%m"); ev["week"] = stamp.dt.strftime("%G-W%V")
    print(f"pump events: {len(ev)} across {ev['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    nsym = ev["symbol"].nunique()
    for gi, (symbol, g) in enumerate(ev.groupby("symbol"), 1):
        if gi % 60 == 0 or gi == nsym:
            print(f"  {gi}/{nsym} symbols, springs {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        vol = frame["quote_volume"].to_numpy(float); tc = frame["trade_count"].to_numpy(float)
        tk = (frame["taker_buy_quote_volume"].to_numpy(float)
              if "taker_buy_quote_volume" in frame.columns else np.zeros(len(c)))
        atr = causal_atr(high=h, low=low, close=c, window=30)
        for row in g.itertuples():
            hi_idx = int(np.searchsorted(ts, int(row.snapshot_time_ms)))
            if hi_idx >= len(ts) or int(ts[hi_idx]) != int(row.snapshot_time_ms):
                continue
            s = _detect(o, h, low, c, vol, tc, tk, hi_idx, float(row.anchor_high),
                        float(row.base_level), float(row.pump_elapsed_min))
            if s is None:
                continue
            ei = s["entry_index"]; entry = float(o[ei])
            perehai = s["range_high"] * (1 + NEW_HIGH_EPS)
            rec = {"symbol": symbol, "mo": row.mo, "week": row.week}
            rec.update({k: s[k] for k in METRICS})
            rec.update({k: s[k] for k in ("mfe5", "mae5", "mfe10", "mae10", "ret5", "fail_bar",
                                          "taker_sweep", "taker_reclaim", "taker_after",
                                          "taker_delta", "sweep_close_pos", "reclaim_body")})
            for stop_name, stop, spec in (("wick", s["wick_stop"], TOUCH),
                                          ("body", s["body_stop"], TOUCH),
                                          ("range", s["range_stop"], CLOSE)):
                if not (stop < entry):
                    for tname in TARGETS:
                        rec[f"net_{stop_name}_{tname}"] = np.nan
                    continue
                r_dist = entry - stop
                tps = {"perehai": perehai, "2R": entry + 2 * r_dist, "3R": entry + 3 * r_dist}
                for tname, tp in tps.items():
                    res = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                             initial_stop_price=stop, spec=spec, take_profit_price=tp)
                    rec[f"net_{stop_name}_{tname}"] = res.net_return if res.status == STATUS_FILLED else np.nan
                res_tr = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                            initial_stop_price=stop, spec=spec, swing_reversal_atr=2.5,
                                            stage_switch_r=2.0, atr=atr)
                rec[f"net_{stop_name}_trail"] = res_tr.net_return if res_tr.status == STATUS_FILLED else np.nan
            # early-cut variants (target new-high, or trailing) - reads the
            # loser signature (fast close back below the box) to exit cheap
            rec["net_early_perehai"] = _sim_early(h, low, c, atr, ei, entry, s["wick_stop"],
                                                  s["range_stop"], perehai, trail=False)
            rec["net_early_trail"] = _sim_early(h, low, c, atr, ei, entry, s["wick_stop"],
                                                s["range_stop"], None, trail=True)
            # causal flow-managed: cut at ei+FLOW_CHECK if buyers didn't show
            rec["net_flow_perehai"] = _sim_flow(h, low, c, vol, tk, atr, ei, entry, s["wick_stop"],
                                                s["tbr_base"], perehai, trail=False)
            rec["net_flow_trail"] = _sim_flow(h, low, c, vol, tk, atr, ei, entry, s["wick_stop"],
                                              s["tbr_base"], None, trail=True)
            rec["new_high"] = bool(rec.get("net_wick_perehai", np.nan) > 0)
            rows.append(rec)
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    print("\n=== EV by stop x target (all springs, honest costs) ===", flush=True)
    for st in STOPS:
        for tg in TARGETS:
            col = f"net_{st}_{tg}"
            net = df[col].dropna().to_numpy()
            s = summarize(net, months=df.loc[df[col].notna(), "mo"].to_numpy(),
                          weeks=df.loc[df[col].notna(), "week"].to_numpy())
            print(s.line(f"{st}/{tg}"), flush=True)
    print(f"\nbase rate P(new high) = {df['new_high'].mean()*100:.1f}%  n={len(df)}", flush=True)
    dissect_wins_losses(df, "net_body_2R", METRICS)


def main() -> None:
    df = build_spring_outcomes()
    out = Path(".output/results/bee_bite_v1/spring_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    report(df)
    print(f"\nsaved {len(df)} springs x {len(df.columns)} cols -> {out}", flush=True)


if __name__ == "__main__":
    main()
