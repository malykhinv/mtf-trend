"""Producer for the canonical pump-long dataset ``state_lattice_runner.parquet``.

Attaches a runner-ness TARGET (future-derived, not a feature) to every
registered pump-fade state-lattice decision: ``forward_mfe`` = maximum
favorable excursion from the decision until structural death (first close back
at/below the event base, else a 24h cap), and a binary ``big_runner``
(forward_mfe >= 10%). A row is usable only where the close race already
resolved, so the forward path to death is fully observed (no look-ahead).

This is the single source of the harness input; it lives in the strategy
package (not a throwaway script) so the dataset stays reproducible.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_fade.builder import _load_symbol

DEFAULT_SRC = Path(".output/results/pump_fade_aggtrades_full/state_lattice.parquet")
DEFAULT_DST = Path(".output/results/pump_long_v1/state_lattice_runner.parquet")
_MINUTE_MS = 60_000
_HORIZON_CAP_MIN = 1440  # 24h ceiling on the forward death search
BIG_RUNNER_THRESHOLD = 0.10


def build_runner_label(
    *, src: Path = DEFAULT_SRC, dst: Path = DEFAULT_DST, cache_dir: Path | None = None
) -> Path:
    """Build and write the runner-labelled lattice; returns the output path."""

    if cache_dir is None:
        cache_dir = Path(".output/market/binance_vision/um_futures/enriched_1m")
    lattice = pd.read_parquet(src)
    reg = lattice.loc[lattice["is_registered_state_lattice"].astype(bool)].copy()
    reg = reg.reset_index(drop=True)
    print(f"registered rows: {len(reg)}", flush=True)

    forward_mfe = np.full(len(reg), np.nan)
    for symbol, group in reg.groupby("symbol"):
        frame, _quality = _load_symbol(cache_dir / f"{symbol}.parquet")
        ts = frame["timestamp"].to_numpy(np.int64)
        high = frame["high"].to_numpy(float)
        close = frame["close"].to_numpy(float)
        for idx, row in zip(group.index, group.itertuples()):
            decision_bar = int(np.searchsorted(ts, int(row.snapshot_time_ms) - _MINUTE_MS))
            if decision_bar >= len(ts) or int(ts[decision_bar]) != int(row.snapshot_time_ms) - _MINUTE_MS:
                continue
            base = float(row.base_level)
            entry_ref = float(row.current_close)
            horizon_end = min(decision_bar + _HORIZON_CAP_MIN, len(ts) - 1)
            death = None
            for j in range(decision_bar + 1, horizon_end + 1):
                if close[j] <= base:
                    death = j
                    break
            death = death if death is not None else horizon_end
            peak = float(np.max(high[decision_bar : death + 1]))
            forward_mfe[idx] = peak / max(entry_ref, 1e-12) - 1.0

    reg["forward_mfe"] = forward_mfe
    reg["big_runner"] = (reg["forward_mfe"] >= BIG_RUNNER_THRESHOLD).astype("int64")
    reg["runner_label_available"] = reg["label_available"].astype(bool) & np.isfinite(forward_mfe)
    dst.parent.mkdir(parents=True, exist_ok=True)
    reg.to_parquet(dst, index=False)
    avail = reg.loc[reg["runner_label_available"]]
    print(
        f"RUNNER LABEL DONE: rows={len(reg)} available={len(avail)} "
        f"big_runner_rate={avail['big_runner'].mean():.3f} "
        f"forward_mfe_median={avail['forward_mfe'].median():.4f} "
        f"p90={avail['forward_mfe'].quantile(0.9):.4f}",
        flush=True,
    )
    return dst


def main() -> None:
    build_runner_label()


if __name__ == "__main__":
    main()
