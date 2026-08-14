"""Stack per-symbol klines_1d parquets into one multi-regime daily panel.

Produces `.output/market/binance_vision/um_futures/daily_klines_v1/panel.parquet`
with the exact column names the existing pipeline (panel.pivot, score, features)
expects, so the 2023-2026 study runs with no downstream rewrite. Intraday-derived
features are simply absent here (score/features guard-check each), so the panel
exercises the 1d-computable signals (momentum, daily_vol, avg_trade_size, volume
evenness, regime) across bull/bear/sideways.

Run: python -m anomaly_science.strategy.xsect_momentum.research.build_daily_panel
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

SRC_GLOB = ".output/market/binance_vision/um_futures/klines_1d/*.parquet"
OUT = Path(".output/market/binance_vision/um_futures/daily_klines_v1/panel.parquet")
DAY_MS = 86_400_000


def main() -> None:
    files = sorted(glob.glob(SRC_GLOB))
    if not files:
        raise FileNotFoundError(f"no daily klines at {SRC_GLOB} — run the fetcher first")
    frames = []
    for f in files:
        sym = Path(f).stem
        df = pd.read_parquet(f)
        if df.empty:
            continue
        df = df.rename(columns={"trade_count": "number_of_trades"})
        df["symbol"] = sym
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)

    panel["date"] = pd.to_datetime(panel["timestamp"], unit="ms", utc=True)
    panel["available_time_ms"] = panel["timestamp"].astype("int64") + DAY_MS
    panel["complete_daily_bar"] = True
    for c in ["open", "high", "low", "close", "quote_volume", "number_of_trades", "taker_buy_quote_volume"]:
        panel[c] = pd.to_numeric(panel[c], errors="coerce")

    keep = ["date", "symbol", "open", "high", "low", "close", "quote_volume",
            "number_of_trades", "taker_buy_quote_volume", "available_time_ms", "complete_daily_bar"]
    panel = panel[keep].dropna(subset=["close"]).sort_values(["symbol", "date"]).reset_index(drop=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT, index=False)
    print(f"symbols={panel['symbol'].nunique()}  rows={len(panel):,}  "
          f"dates={panel['date'].min().date()}->{panel['date'].max().date()}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
