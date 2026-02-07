"""Open interest alignment helpers."""

from __future__ import annotations

import pandas as pd


class OiAligner:
    """Align OI values to OHLCV timestamps using forward-fill."""

    def align(self, ohlcv: pd.DataFrame, open_interest: pd.DataFrame) -> pd.DataFrame:
        if ohlcv.empty:
            return ohlcv.copy()

        base = ohlcv.copy()
        if "timestamp" not in base.columns:
            msg = "OHLCV data must contain 'timestamp' column"
            raise ValueError(msg)

        if open_interest.empty or "timestamp" not in open_interest.columns:
            if "open_interest" not in base.columns:
                base["open_interest"] = pd.NA
            return base

        oi = open_interest.copy()
        if "open_interest" not in oi.columns:
            oi_cols = [c for c in oi.columns if c != "timestamp"]
            if not oi_cols:
                base["open_interest"] = pd.NA
                return base
            oi = oi.rename(columns={oi_cols[0]: "open_interest"})

        aligned = pd.merge_asof(
            base.sort_values("timestamp"),
            oi[["timestamp", "open_interest"]].sort_values("timestamp"),
            on="timestamp",
            direction="backward",
        )
        return aligned
