"""Near-term structural-step hypothesis + exit-rule variants.

Instead of predicting a big runner early (near-impossible: the information
that would make you confident arrives only as the move unfolds), ask an EASIER
question with a high base rate: from a decision, does price advance before
breaking structure? Barrier trade per decision: entry = next 1m open. We test
four exit rules (one pass over the symbol cache), because the killer of the
naive version is negative skew (a far structural stop):

  A base   : stop = last structural swing low (touch), target = entry + 1 ATR
  B beCut  : stop = CLOSE below entry (cut at breakeven), target = entry + 1 ATR
  C newHigh: stop = structural swing low (touch),        target = anchor_high +0.5%
  D beNewHi: stop = CLOSE below entry,                   target = anchor_high +0.5%

winrate is NOT EV: we report avg_win vs avg_loss (RR), PF, and a win/loss
feature dissection (Cohen's d) so we see what separates winners from losers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import (
    STATUS_FILLED,
    causal_atr,
    last_confirmed_swing_low,
    simulate_long_path,
)
from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

SWING_CONFIRMATION = 2
SWING_LOOKBACK = 500
MIN_TARGET_FRAC = 0.005       # skip if 1 ATR < 0.5% (too small to clear costs)
NEW_HIGH_MARGIN = 0.005       # "перехай" = anchor high + 0.5%
STRAT_FEATURES = [
    "state_ordinal", "n_prior_48h", "price_vs_ema_60", "price_vs_ema_240",
    "verticality", "pump_elapsed_min", "event_average_trade_notional",
    "recent_return_3m", "max_1m_close_return", "turnover_top_candle_share",
    "close_drawdown_from_high", "atr_mult",
]
VARIANTS = ("A_base", "B_beCut", "C_newHigh", "D_beNewHi")

TOUCH = PumpLongExecutionSpec(stop_trigger_close_beyond=False)   # structural-low intrabar touch
CLOSE = PumpLongExecutionSpec(stop_trigger_close_beyond=True)    # cut on close below entry


def build_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                   end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    stamp = pd.to_datetime(lat["snapshot_time_ms"], unit="ms", utc=True)
    lat["mo"] = stamp.dt.strftime("%Y-%m"); lat["week"] = stamp.dt.strftime("%G-W%V")
    print(f"decisions: {len(lat)} across {lat['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    groups = list(lat.groupby("symbol"))
    for gi, (symbol, g) in enumerate(groups, 1):
        if gi % 60 == 0 or gi == len(groups):
            print(f"  {gi}/{len(groups)} symbols, outcomes {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        atr = causal_atr(high=h, low=low, close=c, window=30)
        for row in g.itertuples():
            snap = int(row.snapshot_time_ms); d0 = int(np.searchsorted(ts, snap)); ei = d0 + 1
            if ei >= len(ts) or int(ts[d0]) != snap:
                continue
            entry = float(o[ei]); atr_val = float(atr[d0])
            if not np.isfinite(atr_val) or atr_val <= 0 or (atr_val / entry) < MIN_TARGET_FRAC:
                continue
            swing = last_confirmed_swing_low(low=low, start_index=max(0, d0 - SWING_LOOKBACK),
                                             decision_index=d0, confirmation_bars=SWING_CONFIRMATION)
            if swing is None or not (swing < entry):
                continue
            be_stop = entry * (1 - 1e-6)  # "close below entry" cut
            atr_target = entry + atr_val
            hi_target = float(row.anchor_high) * (1 + NEW_HIGH_MARGIN)
            hi_ok = hi_target > entry
            specs = {
                "A_base": (swing, atr_target, TOUCH),
                "B_beCut": (be_stop, atr_target, CLOSE),
                "C_newHigh": (swing, hi_target if hi_ok else None, TOUCH),
                "D_beNewHi": (be_stop, hi_target if hi_ok else None, CLOSE),
            }
            rec = {"symbol": symbol, "mo": row.mo, "week": row.week,
                   "stop_frac": (entry - swing) / entry, "target_frac": atr_val / entry}
            ok = False
            for name, (stop, target, spec) in specs.items():
                if target is None:
                    rec[f"net_{name}"] = np.nan
                    continue
                res = simulate_long_path(open_=o, high=h, low=low, close=c, entry_index=ei,
                                         initial_stop_price=stop, spec=spec, take_profit_price=target)
                rec[f"net_{name}"] = res.net_return if res.status == STATUS_FILLED else np.nan
                ok = ok or res.status == STATUS_FILLED
            if not ok:
                continue
            for f in STRAT_FEATURES:
                rec[f] = float(getattr(row, f)) if hasattr(row, f) else np.nan
            rows.append(rec)
    return pd.DataFrame(rows)


def _line(label: str, net: np.ndarray) -> str:
    net = net[np.isfinite(net)]
    if len(net) == 0:
        return f"{label:<22} (no trades)"
    w = net[net > 0]; l = net[net <= 0]
    pf = w.sum() / (-l.sum() + 1e-9) if len(l) else float("inf")
    return (f"{label:<22} n={len(net):5d} win={(net>0).mean()*100:5.1f}% EV={net.mean()*100:6.2f}% "
            f"avgWin={w.mean()*100 if len(w) else 0:4.2f}% avgLoss={l.mean()*100 if len(l) else 0:6.2f}% PF={pf:4.2f}")


def dissect_wins_losses(df: pd.DataFrame, net_col: str, features: list[str]) -> None:
    """What separates winners from losers: standardized mean gap (Cohen's d)."""

    net = df[net_col].to_numpy()
    win = df.loc[net > 0]; loss = df.loc[net <= 0]
    print(f"\n=== win/loss dissection [{net_col}]  wins={len(win)} losses={len(loss)} ===", flush=True)
    print(f"{'feature':<30}{'mean_win':>12}{'mean_loss':>12}{'cohen_d':>10}", flush=True)
    scored = []
    for f in features:
        a = win[f].to_numpy(); b = loss[f].to_numpy()
        a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
        if len(a) < 30 or len(b) < 30:
            continue
        sd = np.sqrt((a.var() + b.var()) / 2) + 1e-12
        scored.append((f, a.mean(), b.mean(), (a.mean() - b.mean()) / sd))
    for f, mw, ml, d in sorted(scored, key=lambda x: -abs(x[3])):
        print(f"{f:<30}{mw:>12.4f}{ml:>12.4f}{d:>10.2f}", flush=True)


def report(df: pd.DataFrame) -> None:
    print("\n=== exit-rule variants (all decisions, honest costs) ===", flush=True)
    for v in VARIANTS:
        print(_line(v, df[f"net_{v}"].to_numpy()), flush=True)

    best = min(VARIANTS, key=lambda v: -np.nan_to_num(df[f"net_{v}"], nan=0).mean())
    print(f"\n=== '{best}' by state_ordinal ===", flush=True)
    df["ord_b"] = pd.cut(df["state_ordinal"], [0, 1, 2, 3, 5, 100], labels=["1", "2", "3", "4-5", "6+"])
    for b, sub in df.groupby("ord_b", observed=True):
        print(_line(f"ord {b}", sub[f"net_{best}"].to_numpy()), flush=True)

    dissect_wins_losses(df.dropna(subset=[f"net_{best}"]), f"net_{best}", STRAT_FEATURES)


def main() -> None:
    df = build_outcomes()
    out = Path(".output/results/pump_long_v1/near_term_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    report(df)
    print(f"\nsaved {len(df)} outcomes -> {out}", flush=True)


if __name__ == "__main__":
    main()
