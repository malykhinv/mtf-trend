from __future__ import annotations

from anomaly_science.market_context import (
    EventScopedPositioningContextConfig,
    EventScopedPerpCrowdingContextConfig,
    ReferenceMarketContextConfig,
    ReferenceMarketSpec,
    ReferencePositioningContextConfig,
)


PUMP_FADE_REFERENCE_MARKET_CONTEXT = ReferenceMarketContextConfig(
    schema_version="reference_market_context_btc_eth_v1",
    references=(
        ReferenceMarketSpec(symbol="BTCUSDT", alias="btc"),
        ReferenceMarketSpec(symbol="ETHUSDT", alias="eth"),
    ),
)

PUMP_FADE_REFERENCE_POSITIONING_CONTEXT = ReferencePositioningContextConfig(
    schema_version="reference_positioning_context_btc_eth_v1",
    references=PUMP_FADE_REFERENCE_MARKET_CONTEXT.references,
)

PUMP_FADE_SYMBOL_POSITIONING_CONTEXT = EventScopedPositioningContextConfig(
    schema_version="pump_fade_symbol_positioning_context_v1",
    change_lags_minutes=(15, 60),
    metrics_interval_minutes=5,
    max_age_minutes=10,
    publication_lag_minutes=5,
)

PUMP_FADE_PERP_CROWDING_CONTEXT = EventScopedPerpCrowdingContextConfig(
    schema_version="pump_fade_perp_crowding_context_v1",
)


__all__ = [
    "PUMP_FADE_REFERENCE_MARKET_CONTEXT",
    "PUMP_FADE_REFERENCE_POSITIONING_CONTEXT",
    "PUMP_FADE_SYMBOL_POSITIONING_CONTEXT",
    "PUMP_FADE_PERP_CROWDING_CONTEXT",
]
