"""Build the full session-break event table across the universe.

Runs :func:`build_symbol_events` over every enriched 1m symbol file and
concatenates the results. Events are stamped with ``break_ts`` so the analysis
step can split DEV (< 2026-01-01) from a frozen OOS tail without rebuilding.
"""

from __future__ import annotations

import argparse
import glob
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from anomaly_science.strategy.session_break.research.events import build_symbol_events

DEFAULT_CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_OUT = Path(".output/results/session_break/events.parquet")


def _one(path_str: str) -> pd.DataFrame:
    try:
        return build_symbol_events(Path(path_str))
    except Exception:  # a corrupt symbol file must not sink the whole build
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="0 = all symbols")
    args = ap.parse_args()

    files = sorted(glob.glob(str(args.cache / "*.parquet")))
    if args.limit:
        files = files[: args.limit]
    print(f"symbols: {len(files)}  workers: {args.workers}")

    t0 = time.time()
    frames: list[pd.DataFrame] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, f): f for f in files}
        for fut in as_completed(futs):
            frame = fut.result()
            if len(frame):
                frames.append(frame)
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(files)}  events={sum(len(f) for f in frames):,}  {time.time()-t0:.0f}s")

    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    events.to_parquet(args.out)
    print(f"wrote {len(events):,} events -> {args.out}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
