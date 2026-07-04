"""Aggregate the enriched 1m cache into a higher timeframe (e.g. 15m).

Standard OHLCV resample aligned to the timeframe boundary (15m -> :00/:15/
:30/:45), labelled by the bar OPEN time like Binance. Volumes, trade counts,
taker-buy and liquidation columns are SUMMED; open interest is the LAST
snapshot in the window; availability flags carry the end-of-window state. Gaps
are handled naturally (empty buckets dropped); a partial bucket keeps whatever
1m bars are present and records ``n_1m_bars`` for transparency.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_SRC = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_DST = Path(".output/market/binance_vision/um_futures/enriched_15m")

_AGG = {
    "open": "first", "high": "max", "low": "min", "close": "last",
    "volume": "sum", "quote_volume": "sum", "trade_count": "sum",
    "taker_buy_base_volume": "sum", "taker_buy_quote_volume": "sum",
    "open_interest": "last",
    "long_liquidations_vol": "sum", "short_liquidations_vol": "sum",
    "oi_available": "last", "missing_oi_flag": "max",
    "liquidation_available": "last", "missing_liquidation_flag": "max",
}


def resample_ohlcv(frame: pd.DataFrame, rule: str = "15min") -> pd.DataFrame:
    """Resample one symbol's 1m frame to ``rule`` (default 15m). Returns a frame
    with an integer ``timestamp`` (ms, bar open) and ``n_1m_bars``."""

    idx = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    df = frame.set_index(idx).sort_index()
    agg = {col: how for col, how in _AGG.items() if col in df.columns}
    out = df.resample(rule, label="left", closed="left").agg(agg)
    out["n_1m_bars"] = df["close"].resample(rule, label="left", closed="left").count()
    out = out.loc[out["n_1m_bars"] > 0].copy()
    out.insert(0, "timestamp", out.index.view(np.int64) // 10**6)
    return out.reset_index(drop=True)


def build_higher_tf_cache(
    *, src_dir: Path = DEFAULT_SRC, dst_dir: Path = DEFAULT_DST, rule: str = "15min"
) -> int:
    """Resample every symbol parquet in ``src_dir`` into ``dst_dir``."""

    dst_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(src_dir.glob("*.parquet"))
    for i, path in enumerate(files, 1):
        try:
            frame = pd.read_parquet(path)
            resample_ohlcv(frame, rule).to_parquet(dst_dir / path.name, index=False)
        except Exception as exc:  # keep going; report at the end
            print(f"  SKIP {path.name}: {exc}", flush=True)
        if i % 50 == 0 or i == len(files):
            print(f"  {i}/{len(files)} symbols", flush=True)
    return len(files)


def main() -> None:
    n = build_higher_tf_cache()
    print(f"resampled {n} symbols -> {DEFAULT_DST}", flush=True)


if __name__ == "__main__":
    main()
