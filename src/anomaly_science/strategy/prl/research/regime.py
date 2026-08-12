"""Point-in-time regime labels for the §67 robustness breakdown (§6.3.1).

Deterministic, causal: the sign+band of the market asset's trailing trend. This is
an AUDIT feature (regime-conditioned reporting), never a model feature or a
future-aware label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def regime_label(close: pd.DataFrame, p: PRLCoarsePolicy) -> pd.Series:
    """Per-date {bull, bear, sideways} from the regime asset's trailing trend."""
    if p.regime_asset in close.columns:
        base = close[p.regime_asset]
    else:
        base = close.median(axis=1)
    lp = np.log(base.where(base > 0))
    trend = (lp.shift(p.skip) - lp.shift(p.skip + p.regime_lb)).reindex(close.index)
    out = pd.Series("sideways", index=close.index, dtype=object)
    out[trend > p.regime_band] = "bull"
    out[trend < -p.regime_band] = "bear"
    out[trend.isna()] = "unknown"
    return out
