"""Backward-compatible imports for the strategy-neutral aggTrades projector."""

from anomaly_science.data.aggtrades_minute import *  # noqa: F403
from anomaly_science.data.aggtrades_minute import __all__

PUMP_FADE_AGGTRADES_MINUTE_FEATURES = AGGTRADES_MINUTE_FEATURES
PUMP_FADE_AGGTRADES_MINUTE_SCHEMA_VERSION = AGGTRADES_MINUTE_SCHEMA_VERSION
__all__ = (
    *__all__,
    "PUMP_FADE_AGGTRADES_MINUTE_FEATURES",
    "PUMP_FADE_AGGTRADES_MINUTE_SCHEMA_VERSION",
)
