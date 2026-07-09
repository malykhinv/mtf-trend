"""Outcome labels for triple-tap / CAP setups (the CatBoost target).

For each detected setup we simulate FORWARD from the entry on 1m with its own
stop and take, and record the realised outcome. The label is strictly forward
(it never leaks into the causal feature set); features come from ``features.py``
and use only pre-entry data.

Outcomes (long only, entry at the level break):
  ``win``   - price reached ``take`` before ``stop``
  ``loss``  - price hit ``stop`` before ``take``
  ``timeout`` - neither within the horizon
Same-bar ambiguity is resolved PESSIMISTICALLY (stop checked first) so wins are
never over-counted.

Emits, per setup: ``label_win`` (1/0, timeout->0 for the binary gate target),
``r_multiple`` (realised R: +rr win, -1 loss, marked-to-horizon-close otherwise),
``fwd_mfe_r`` / ``fwd_mae_r`` (peak favourable / adverse excursion in R units),
``bars_to_outcome``.
"""

from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.detect import CACHE_1M, TFS, _load_1m, _resample_np
from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.pump_long.research.context import DEV_END_MS

DEFAULT_SETUPS = Path(".output/results/triple_tap_v1/setups_discovery.parquet")
DEFAULT_OUT = Path(".output/results/triple_tap_v1/labels.parquet")
HORIZON_TF_BARS = 200                    # forward horizon = 200 detection-TF bars
RR_MIN = 1.0                             # close-entry: skip if RR from the confirmed fill < 1


def _simulate(ts: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
              entry_ms: int, entry: float, stop: float, take: float, horizon_ms: int,
              future_cutoff_ms: int | None = DEV_END_MS) -> dict:
    """Walk 1m bars forward from entry; return the realised outcome dict."""
    risk = entry - stop
    e = int(np.searchsorted(ts, entry_ms, side="left"))
    requested_end_ms = entry_ms + horizon_ms
    effective_end_ms = min(requested_end_ms, future_cutoff_ms) if future_cutoff_ms is not None else requested_end_ms
    end = int(np.searchsorted(ts, effective_end_ms, side="left"))
    end = min(end, len(ts))
    full_horizon = future_cutoff_ms is None or requested_end_ms <= future_cutoff_ms
    if risk <= 0 or e >= end:
        return dict(label=np.nan, label_win=np.nan, r_multiple=np.nan,
                    fwd_mfe_r=np.nan, fwd_mae_r=np.nan, bars_to_outcome=np.nan,
                    fwd_mfe_pct=np.nan, fwd_mae_pct=np.nan, fwd_ret_pct=np.nan,
                    ran_08=np.nan, ran_15=np.nan, outcome_time_ms=np.nan,
                    target_resolution_time_ms=np.nan)
    # geometry-NEUTRAL forward move over the whole horizon (ignores stop/take -
    # "did the breakout run", the market question) + pessimistic stop/take sim.
    mfe_pct = float(np.max((high[e:end] - entry) / entry)) if full_horizon else np.nan
    mae_pct = float(np.min((low[e:end] - entry) / entry)) if full_horizon else np.nan
    ret_pct = float((close[end - 1] - entry) / entry) if full_horizon else np.nan
    ran08_hits = np.flatnonzero((high[e:end] - entry) / entry >= 0.08) if full_horizon else np.array([])
    target_resolution_time_ms = (
        int(ts[e + int(ran08_hits[0])]) + 60_000
        if len(ran08_hits)
        else (int(ts[end - 1]) + 60_000 if full_horizon else np.nan)
    )
    base = dict(fwd_mfe_pct=mfe_pct, fwd_mae_pct=mae_pct, fwd_ret_pct=ret_pct,
                ran_08=int(mfe_pct >= 0.08) if full_horizon else np.nan,
                ran_15=int(mfe_pct >= 0.15) if full_horizon else np.nan,
                target_resolution_time_ms=target_resolution_time_ms)
    mfe = 0.0
    mae = 0.0
    for j in range(e, end):
        mfe = max(mfe, (high[j] - entry) / risk)
        mae = min(mae, (low[j] - entry) / risk)
        if low[j] <= stop:                              # pessimistic: stop first
            return dict(label="loss", label_win=0, r_multiple=-1.0,
                        fwd_mfe_r=float(mfe), fwd_mae_r=float(mae), bars_to_outcome=int(j - e),
                        outcome_time_ms=int(ts[j]) + 60_000, **base)
        if high[j] >= take:
            return dict(label="win", label_win=1, r_multiple=float((take - entry) / risk),
                        fwd_mfe_r=float(mfe), fwd_mae_r=float(mae), bars_to_outcome=int(j - e),
                        outcome_time_ms=int(ts[j]) + 60_000, **base)
    if not full_horizon:
        return dict(label="unresolved_is_boundary", label_win=np.nan, r_multiple=np.nan,
                    fwd_mfe_r=float(mfe), fwd_mae_r=float(mae), bars_to_outcome=np.nan,
                    outcome_time_ms=np.nan, **base)
    last_r = (close[end - 1] - entry) / risk
    return dict(label="timeout", label_win=0, r_multiple=float(last_r),
                fwd_mfe_r=float(mfe), fwd_mae_r=float(mae), bars_to_outcome=int(end - 1 - e),
                outcome_time_ms=int(ts[end - 1]) + 60_000, **base)


def build_labels(setups_path: Path = DEFAULT_SETUPS, out_path: Path = DEFAULT_OUT,
                 entry_mode: str = "break") -> pd.DataFrame:
    """``entry_mode``: 'break' = fill at the level (intra-bar cross); 'close' =
    fill at the CLOSE of the first bar that closes above the level (confirmed) -
    a worse fill but the breakout bar is then complete (its stats become causal)."""
    setups = pd.read_parquet(setups_path)
    setups = setups[setups["valid_entry"]].reset_index(drop=True)
    rows: list[dict] = []
    rejects: dict[str, int] = {
        "input_valid_setups": int(len(setups)),
        "skip_symbol_load_error": 0,
        "skip_entry_bar_missing": 0,
        "skip_close_rr_below_min": 0,
        "simulated_rows": 0,
        "unresolved_is_boundary": 0,
        "invalid_simulation": 0,
    }
    for symbol, g in setups.groupby("symbol"):
        try:
            base = _load_1m(CACHE_1M / f"{symbol}.parquet")
        except Exception as exc:
            print(f"  SKIP {symbol}: {exc}", flush=True)
            rejects["skip_symbol_load_error"] += int(len(g))
            continue
        ts, hi, lo, cl = base["timestamp"], base["high"], base["low"], base["close"]
        tf_cache = {tf: (base if TFS[tf] == 1 else _resample_np(base, TFS[tf])) for tf in g["tf"].unique()}
        for r in g.itertuples(index=False):
            horizon_ms = HORIZON_TF_BARS * TFS[r.tf] * 60_000
            if entry_mode == "close":
                cols = tf_cache[r.tf]
                ei = int(np.searchsorted(cols["timestamp"], int(r.entry_time_ms), side="left"))
                if ei >= len(cols["timestamp"]):
                    rejects["skip_entry_bar_missing"] += 1
                    continue
                entry_price = float(cols["close"][ei])
                # only take the confirmed entry if the RR still makes sense from the
                # (worse) close fill - else the move is already eaten (user).
                rr_c = (float(r.take) - entry_price) / (entry_price - float(r.stop)) if entry_price > r.stop else -1
                if not (rr_c >= RR_MIN):
                    rejects["skip_close_rr_below_min"] += 1
                    continue
                fill_ms = int(cols["timestamp"][ei]) + TFS[r.tf] * 60_000     # position opens at the bar close
            else:
                entry_price = float(r.entry)
                fill_ms = int(r.entry_time_ms)
            out = _simulate(
                ts, hi, lo, cl, fill_ms, entry_price, float(r.stop), float(r.take),
                horizon_ms, future_cutoff_ms=DEV_END_MS,
            )
            out.update(symbol=symbol, tf=r.tf, entry_time_ms=int(r.entry_time_ms),
                       setup_type=r.setup_type, entry_price=entry_price,
                       fill_time_ms=int(fill_ms), feature_cutoff_time_ms=int(fill_ms),
                       future_start_time_ms=int(fill_ms) + 1)
            rows.append(out)
            rejects["simulated_rows"] += 1
            if out.get("label") == "unresolved_is_boundary":
                rejects["unresolved_is_boundary"] += 1
            if pd.isna(out.get("label")):
                rejects["invalid_simulation"] += 1
    labels = pd.DataFrame(rows)
    if labels.duplicated(TRADE_KEY).any():
        raise ValueError(f"labels violate unique trade identity {TRADE_KEY}")
    if not (labels["feature_cutoff_time_ms"] < labels["future_start_time_ms"]).all():
        raise ValueError("label time contract violated: feature cutoff must precede future start")
    resolved = labels["outcome_time_ms"].notna()
    if not (labels.loc[resolved, "outcome_time_ms"] > labels.loc[resolved, "feature_cutoff_time_ms"]).all():
        raise ValueError("label time contract violated: outcome must follow feature cutoff")
    if not (labels.loc[resolved, "outcome_time_ms"] <= DEV_END_MS).all():
        raise ValueError("label time contract violated: outcome crosses the frozen IS boundary")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(out_path, index=False)
    reject_path = out_path.with_name(f"{out_path.stem}_rejects.json")
    reject_payload = {
        "setups_path": str(setups_path),
        "out_path": str(out_path),
        "entry_mode": entry_mode,
        "rr_min": RR_MIN,
        "accounting": rejects,
    }
    reject_path.write_text(json.dumps(reject_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    n = len(labels)
    print(f"saved {n} '{entry_mode}' labels -> {out_path}  (win-rate {labels['label_win'].mean():.1%}, "
          f"ran08 {labels['ran_08'].mean():.1%}, outcomes {labels['label'].value_counts(dropna=False).to_dict()})", flush=True)
    print(f"saved label reject accounting -> {reject_path}", flush=True)
    return labels


def main() -> None:
    build_labels(out_path=DEFAULT_OUT, entry_mode="break")
    build_labels(out_path=Path(".output/results/triple_tap_v1/labels_close.parquet"), entry_mode="close")


if __name__ == "__main__":
    main()
