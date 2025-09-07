from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import constants
from .enums import Side


@dataclass(frozen=True)
class PumpWindow:
    high: float
    low: float
    start_ts: int
    end_ts: int


@dataclass
class SymbolMetrics:
    """Container for all per-symbol metrics used by the engine.

    The actual project derives a large number of attributes from the raw
    market data stream.  Only the subset exercised by the simplified runtime
    is modelled here.  All fields are initialised with neutral defaults so
    that missing data is handled gracefully.
    """

    # --- rolling windows for z-score calculations -----------------------
    price_win: Deque[float] = field(
        default_factory=lambda: deque(maxlen=constants.Z_BASE_WINDOW_MIN)
    )
    vol_win: Deque[float] = field(
        default_factory=lambda: deque(maxlen=constants.Z_BASE_WINDOW_MIN)
    )
    liq_win: Deque[float] = field(
        default_factory=lambda: deque(maxlen=constants.Z_BASE_WINDOW_MIN)
    )

    # --- minute candle stats --------------------------------------------
    start_ts: int = 0
    end_ts: int = 0
    high: float = 0.0
    low: float = 0.0
    last_price: float = 0.0

    # --- derived z-scores and price delta metrics -----------------------
    z_px: float = 0.0
    z_vol: float = 0.0
    delta_price_sigma_mult: float = 0.0
    delta_price_abs_pct: float = 0.0
    close_pos: float = 0.0

    # --- orderbook / premium related metrics ---------------------------
    best_bid: float = 0.0
    best_ask: float = 0.0
    premium_pct: float = 0.0

    # --- liquidation and open interest metrics -------------------------
    liqs_z: float = 0.0
    last_liq_side: Side | None = None
    delta_oi_pct: float = 0.0

    # --- taker volume metrics ------------------------------------------
    taker_buy_volume: float = 0.0
    taker_sell_volume: float = 0.0

    # --- entry helpers --------------------------------------------------
    low_break: bool = False
    avwap_loss: bool = False
    entry_price: float = 0.0
    direction: Side | None = None
