"""Render one chart per triple-tap setup for eyeball validation (Stage 2).

Draws the three taps (H1/H2/H3), the resistance level, the tightening retrace
lows, the post-H3 consolidation zone, and the proposed entry/stop/take, plus the
15m volume with the 4h regime-step summary in the subtitle. This is QA: we look
at every setup and decide which are real triple-taps vs artefacts before any EV.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.data.resample import resample_ohlcv
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.triple_tap.detect import CACHE_1M, DEFAULT_CACHE, DEFAULT_OUT, SWING_PCT, TFS, _pct_zigzag
from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.candidate_audit import (
    _ledger_for,
    _selected_frame,
    _load_candidate_frame,
)
from anomaly_science.strategy.triple_tap.research.accounting import POLICY
from anomaly_science.strategy.triple_tap.research.audit import _ledger
from anomaly_science.strategy.triple_tap.research.economics import RES
from anomaly_science.visualization import (
    CandleSeries,
    ChartSpec,
    HistogramSeries,
    HorizontalLevel,
    LineSeries,
    PointMarker,
    PriceZone,
    SwingPoint,
    render_chart,
)
from anomaly_science.visualization.theme import DEFAULT_THEME

DEFAULT_CHART_DIR = Path(".output/results/triple_tap_v1/charts")
PRE_H1_MULT = 3          # left context = 3x the formation span (scales with TF; sleep+pump visible)
PRE_H1_MIN_BARS = 60     # ...but at least this many bars of context
OUTCOME_BUFFER_BARS = 8  # stop the window shortly after take/stop is hit (don't distract)
OUTCOME_CAP_BARS = 60    # ...but cap it if neither resolves
RISK_FACE = "#4c1d1d"    # dark red fill for the entry->stop risk box


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(values).ewm(span=span, adjust=False).mean().to_numpy(float)


def _idx(ts: np.ndarray, t_ms: int) -> int:
    return max(0, min(int(np.searchsorted(ts, t_ms, side="left")), len(ts) - 1))


def build_chart_spec(frame: pd.DataFrame, s: pd.Series) -> ChartSpec:
    ts = frame["timestamp"].to_numpy(np.int64)
    i_entry = _idx(ts, int(s.entry_time_ms))
    i_h1 = _idx(ts, int(s.h1_time_ms))
    # left context scales with the formation span (a 1m situation shows hours, a
    # 4h one shows weeks) - a fixed 7d dwarfed fast setups.
    w0 = max(0, i_h1 - max(PRE_H1_MIN_BARS, PRE_H1_MULT * (i_entry - i_h1)))
    # end the window shortly AFTER the outcome (first take/stop touch) so the tail
    # doesn't distract from the formation; cap it if neither resolves.
    hi = frame["high"].to_numpy(float)
    lo = frame["low"].to_numpy(float)
    exit_time_ms = getattr(s, "exit_time_ms", np.nan)
    if np.isfinite(exit_time_ms):
        outcome = _idx(ts, int(exit_time_ms))
    else:
        outcome = i_entry + OUTCOME_CAP_BARS
        for k in range(i_entry + 1, min(i_entry + OUTCOME_CAP_BARS, len(frame))):
            if hi[k] >= float(s["take"]) or lo[k] <= float(s.stop):
                outcome = k
                break
    w1 = min(len(frame) - 1, outcome + OUTCOME_BUFFER_BARS)
    win = frame.iloc[w0:w1 + 1].reset_index(drop=True)
    wts = win["timestamp"].to_numpy(np.int64)
    o = win["open"].to_numpy(float)
    h = win["high"].to_numpy(float)
    low = win["low"].to_numpy(float)
    close = win["close"].to_numpy(float)

    theme = DEFAULT_THEME
    # structural pivots inside the window (same pct grid as detection)
    piv = _pct_zigzag(h, low, SWING_PCT)
    swings = tuple(SwingPoint(int(wts[i]), price, kind) for i, price, kind in piv)

    # two faint volume reference lines split at the PUMP START (= end of sleep):
    # sleep-avg before it, formation-avg after - so the ____->ПППП step is visible.
    vol_win = win["quote_volume"].to_numpy(float)
    i_ps = max(1, min(_idx(wts, int(s.pump_start_ms)), len(vol_win) - 1))
    sleep_avg = float(np.mean(vol_win[:i_ps])) if i_ps > 0 else 0.0
    formation_avg = float(np.mean(vol_win[i_ps:])) if i_ps < len(vol_win) else 0.0

    vol_txt = (
        f"vol step x{s.vol_step_ratio:.2f}  rank {s.vol_pct_rank:.0%}"
        if np.isfinite(s.vol_step_ratio) else "vol step n/a"
    )
    btc_txt = (
        f"trades vs BTC x{s.trades_vs_btc:.2f} {'OK' if s.trades_gt_btc else 'LOW'}"
        if np.isfinite(s.trades_vs_btc) else "trades vs BTC n/a"
    )
    tap_times = list(s.tap_times_ms)
    tap_px = list(s.tap_prices)
    lo_times = list(s.retrace_low_times_ms)
    lo_px = list(s.retrace_lows)
    retr = list(s.retraces)
    tap_markers = tuple(
        PointMarker(int(t), float(p), theme.swing_high, f"H{i+1}", "v")
        for i, (t, p) in enumerate(zip(tap_times, tap_px))
    )
    lo_markers = tuple(
        PointMarker(int(t), float(p), theme.swing_low, f"L{i+1}", "^")
        for i, (t, p) in enumerate(zip(lo_times, lo_px))
    )
    retr_txt = " ".join(f"{r*100:.0f}" for r in retr)
    exit_markers: tuple[PointMarker, ...] = ()
    if np.isfinite(exit_time_ms):
        exit_idx = _idx(wts, int(exit_time_ms))
        exit_label = "exit"
        net_r = getattr(s, "net_r", np.nan)
        if np.isfinite(net_r):
            exit_label = f"exit {net_r:+.2f}R"
        exit_markers = (
            PointMarker(int(wts[exit_idx]), float(close[exit_idx]), theme.target, exit_label, "x", 56.0),
        )

    return ChartSpec(
        candles=CandleSeries(wts, o, h, low, close),
        title=f"[{'VALID' if s.valid_entry else 'INVALID'}]  {s.tf}  {s.symbol}  |  {int(s.n_taps)}-tap  |  {getattr(s, 'setup_family', 'cap' if s.setup_type=='consol_after_pullback' else 'breakout')}  |  {vol_txt}",
        subtitle_lines=(
            f"retraces% [{retr_txt}]  R_last/R1 {s.r_last_over_r1:.2f}  viol {int(s.n_viol)}  "
            f"asc_lows {bool(s.ascending_lows)}",
            f"span {s.span_h:.0f}h  gap~{s.gap_taps_h_mean:.0f}h  left {s.left_impulse*100:.0f}%  "
            f"form_vol x{s.formation_vol_ratio:.1f}  brk_vol x{s.brk_vol_ratio:.1f}{'!' if s.brk_initiative else ''}  "
            f"ramp x{s.pre_brk_vol_ramp:.1f}{'^' if s.vol_ramp_up else ''}  "
            f"RR {s.rr:.2f}  take +{s.dist_take_pct*100:.1f}%  stop -{s.dist_stop_pct*100:.1f}%",
            f"rise x{s.left_impulse_over_r1:.1f}R1  left_eff {s.left_impulse_eff:.2f}  clear x{s.left_clear_ratio:.1f}  sleep_atr {s.pre_pump_atr_pct*100:.2f}%  "
            f"retrace_eff {s.retrace_eff_min:.2f}/{s.retrace_eff_mean:.2f}  "
            f"pinch_bar {s.pinch_bar_atr_pct*100:.2f}%  tap_wick {s.tap_wick_frac:.2f}  "
            f"pump_eff {getattr(s, 'pump_path_eff', np.nan):.2f}  "
            f"lvl_age {getattr(s, 'level_age_share', np.nan):.0%}  "
            f"lows_line {getattr(s, 'support_lows_last_over_first', np.nan):.2f}",
        ),
        lines=(
            LineSeries("EMA 20", _ema(close, 20), theme.ema_slow, 0.9, 0.75),
            LineSeries("EMA 50", _ema(close, 50), theme.ema_fast, 0.9, 0.65),
        ),
        swings=swings,
        levels=(
            HorizontalLevel(float(s.level), theme.zone_edge, "LEVEL", int(s.h1_time_ms), int(s.entry_time_ms), "-", 1.1),
            HorizontalLevel(float(s.entry), theme.entry, "ENTRY", int(s.entry_time_ms)),
            HorizontalLevel(float(s.stop), theme.stop, "STOP", int(s.stop_time_ms)),
            HorizontalLevel(float(s["take"]), theme.target, "TAKE", int(s.entry_time_ms)),
        ),
        zones=(
            # forecast boxes to the right of entry: risk (entry->stop) and reward
            # (entry->take), so the R:R is visible at a glance.
            PriceZone(
                float(s.stop), float(s.entry), int(s.entry_time_ms), int(wts[-1]),
                theme.stop, RISK_FACE, "risk", 0.16,
            ),
            PriceZone(
                float(s.entry), float(s["take"]), int(s.entry_time_ms), int(wts[-1]),
                theme.target, theme.zone_face, "reward", 0.13,
            ),
        ),
        markers=tap_markers + lo_markers + (
            PointMarker(int(s.entry_time_ms), float(s.entry), theme.entry, "entry", "o"),
        ) + exit_markers,
        histograms=(
            HistogramSeries("", vol_win, theme.ema_slow, 0.38, True),
        ),
        histogram_hsegments=(
            (sleep_avg, 0, i_ps, theme.muted),                     # sleep avg: only up to pump start
            (formation_avg, i_ps, len(vol_win) - 1, theme.muted),  # formation avg: only from pump start
        ),
        metadata={"symbol": s.symbol, "entry_time_ms": int(s.entry_time_ms)},
    )


def render_setup_charts(
    *,
    setups_path: Path = DEFAULT_OUT,
    cache_dir: Path = DEFAULT_CACHE,
    output_dir: Path = DEFAULT_CHART_DIR,
    limit: int | None = None,
    top_by_ramp: int | None = None,
) -> pd.DataFrame:
    setups = pd.read_parquet(setups_path)
    setups = setups[setups["valid_entry"]]
    if top_by_ramp is not None:  # keep the strongest pre-break 1m ramp (causal signal, fresh + CAP)
        setups = setups.sort_values("pre_brk_vol_ramp", ascending=False).head(top_by_ramp)
    setups = setups.sort_values(["tf", "symbol", "entry_time_ms"])
    if limit is not None:
        setups = setups.head(limit)
    # wipe stale charts from earlier runs so the dir always mirrors the CURRENT set
    if output_dir.exists():
        shutil.rmtree(output_dir)
    records: list[dict] = []
    base_cache: dict[str, pd.DataFrame] = {}
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    total = len(setups)
    for i, row in enumerate(setups.itertuples(index=False), 1):
        s = pd.Series(row._asdict())
        key = (s.symbol, s.tf)
        if key not in frame_cache:
            if s.symbol not in base_cache:
                base_cache[s.symbol] = _load_symbol(CACHE_1M / f"{s.symbol}.parquet")[0]  # 1m source
            base = base_cache[s.symbol]
            frame_cache[key] = base if s.tf == "1m" else resample_ohlcv(base, f"{TFS[s.tf]}min")
        tf_dir = output_dir / s.tf
        tf_dir.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp(int(s.entry_time_ms), unit="ms", tz="UTC").strftime("%Y%m%d_%H%M")
        path = tf_dir / f"{stamp}_{s.symbol}.png"
        render_chart(build_chart_spec(frame_cache[key], s), path)
        records.append({"symbol": s.symbol, "tf": s.tf, "entry_time_ms": int(s.entry_time_ms), "chart_path": str(path)})
        if i % 20 == 0 or i == total:
            print(f"rendered {i}/{total} charts", flush=True)
    manifest = pd.DataFrame(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest.to_parquet(output_dir / "manifest.parquet", index=False)
    print(f"saved {len(manifest)} charts -> {output_dir}", flush=True)
    return manifest


def render_candidate_trade_charts(
    *,
    setups_path: Path | None = None,
    output_dir: Path = DEFAULT_CHART_DIR,
    limit: int | None = None,
) -> pd.DataFrame:
    """Render charts for the actual frozen-candidate portfolio trades.

    The chart set mirrors the current IS candidate ledger: confirmed-close,
    policy-specific q70 selection, max 3 concurrent positions, one position per
    symbol, and the frozen 0.50x notional cap.
    """
    if setups_path is None:
        setups_path = Path(".output/results/triple_tap_v1/setups_discovery.parquet")
    frame = _load_candidate_frame()
    selected = _selected_frame(frame)
    ledger, _ = _ledger_for(selected, risk_pct=0.02)
    setups = pd.read_parquet(setups_path)
    if "setup_family" not in setups.columns:
        setups = setups.assign(
            setup_family=np.where(
                setups["setup_type"].eq("consol_after_pullback"), "cap", "breakout"
            )
        )
    trades = ledger.merge(
        setups,
        on=TRADE_KEY,
        how="inner",
        validate="one_to_one",
        suffixes=("_trade", ""),
    )
    trades = trades.sort_values(["setup_family", "tf", "symbol", "entry_time_ms"])
    if limit is not None:
        trades = trades.head(limit)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    records: list[dict] = []
    base_cache: dict[str, pd.DataFrame] = {}
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    total = len(trades)
    for i, row in enumerate(trades.itertuples(index=False), 1):
        s = pd.Series(row._asdict())
        key = (s.symbol, s.tf)
        if key not in frame_cache:
            if s.symbol not in base_cache:
                base_cache[s.symbol] = _load_symbol(CACHE_1M / f"{s.symbol}.parquet")[0]
            base = base_cache[s.symbol]
            frame_cache[key] = base if s.tf == "1m" else resample_ohlcv(base, f"{TFS[s.tf]}min")
        family_dir = output_dir / str(s.setup_family) / str(s.tf)
        family_dir.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp(int(s.entry_time_ms), unit="ms", tz="UTC").strftime("%Y%m%d_%H%M")
        path = family_dir / f"{stamp}_{s.symbol}_{s.net_r:+.2f}R.png"
        render_chart(build_chart_spec(frame_cache[key], s), path)
        records.append(
            {
                "symbol": s.symbol,
                "tf": s.tf,
                "setup_family": s.setup_family,
                "entry_time_ms": int(s.entry_time_ms),
                "exit_time_ms": int(s.exit_time_ms),
                "net_r": float(s.net_r),
                "pnl": float(s.pnl),
                "chart_path": str(path),
            }
        )
        if i % 20 == 0 or i == total:
            print(f"rendered {i}/{total} candidate trade charts", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = pd.DataFrame(records)
    manifest.to_parquet(output_dir / "manifest.parquet", index=False)
    print(f"saved {len(manifest)} candidate trade charts -> {output_dir}", flush=True)
    return manifest


def render_unscored_current_trade_charts(
    *,
    setups_path: Path | None = None,
    output_dir: Path = Path(".output/results/triple_tap_v1/charts_unscored_current"),
    limit: int | None = None,
) -> pd.DataFrame:
    """Render explicit current unscored portfolio trades.

    This is not a fallback and not a frozen ML candidate. It requires current
    policy labels and renders the FCFS combined-family portfolio using the
    current detector contract.
    """
    if setups_path is None:
        setups_path = Path(".output/results/triple_tap_v1/setups_discovery.parquet")
    policies = pd.read_parquet(RES / "execution_policies.parquet")
    candidates = policies[
        (policies["entry_mode"] == "close")
        & (policies["exit_policy"] == POLICY)
        & policies["r_multiple"].notna()
    ].copy()
    candidates["model_score_r"] = 0.0
    ledger, _ = _ledger(
        candidates,
        risk_pct=0.02,
        max_open=3,
        slip_pct=0.005,
        priority_by_score=False,
        max_notional_multiple=0.50,
    )
    setups = pd.read_parquet(setups_path)
    trades = ledger.merge(
        setups,
        on=TRADE_KEY,
        how="inner",
        validate="one_to_one",
        suffixes=("_trade", ""),
    )
    trades = trades.sort_values(["setup_family", "tf", "symbol", "entry_time_ms"])
    if limit is not None:
        trades = trades.head(limit)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    records: list[dict] = []
    base_cache: dict[str, pd.DataFrame] = {}
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    total = len(trades)
    for i, row in enumerate(trades.itertuples(index=False), 1):
        s = pd.Series(row._asdict())
        key = (s.symbol, s.tf)
        if key not in frame_cache:
            if s.symbol not in base_cache:
                base_cache[s.symbol] = _load_symbol(CACHE_1M / f"{s.symbol}.parquet")[0]
            base = base_cache[s.symbol]
            frame_cache[key] = base if s.tf == "1m" else resample_ohlcv(base, f"{TFS[s.tf]}min")
        family_dir = output_dir / str(s.setup_family) / str(s.tf)
        family_dir.mkdir(parents=True, exist_ok=True)
        stamp = pd.Timestamp(int(s.entry_time_ms), unit="ms", tz="UTC").strftime("%Y%m%d_%H%M")
        path = family_dir / f"{stamp}_{s.symbol}_{s.net_r:+.2f}R.png"
        render_chart(build_chart_spec(frame_cache[key], s), path)
        records.append(
            {
                "symbol": s.symbol,
                "tf": s.tf,
                "setup_family": s.setup_family,
                "entry_time_ms": int(s.entry_time_ms),
                "exit_time_ms": int(s.exit_time_ms),
                "net_r": float(s.net_r),
                "pnl": float(s.pnl),
                "chart_scope": "unscored_current_combined_portfolio",
                "chart_path": str(path),
            }
        )
        if i % 20 == 0 or i == total:
            print(f"rendered {i}/{total} unscored current trade charts", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = pd.DataFrame(records)
    manifest.to_parquet(output_dir / "manifest.parquet", index=False)
    print(f"saved {len(manifest)} unscored current trade charts -> {output_dir}", flush=True)
    return manifest


DISCOVERY_OUT = Path(".output/results/triple_tap_v1/setups_discovery.parquet")
DISCOVERY_CHART_DIR = Path(".output/results/triple_tap_v1/discovery")


def main() -> None:
    render_candidate_trade_charts(
        output_dir=Path(".output/results/triple_tap_v1/charts_ml_candidate")
    )


if __name__ == "__main__":
    main()
