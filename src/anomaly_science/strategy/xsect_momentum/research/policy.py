"""Frozen policy for the cross-sectional momentum study.

Every field here is preregistered in docs/strategies/xsect_momentum_protocol.md.
Changing a value is a §11 deviation-log event, not a casual tweak. The dataclass
is frozen and hashable so each executed configuration is stamped into the trial
log with a stable id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class XSectMomentumPolicy:
    version: str = "xsect_momentum_v1_is_2026-08-04"

    # --- signal (§2.3): short/medium lookbacks that fit a 7-month IS window ---
    short_lb: int = 14
    long_lb: int = 42
    skip: int = 1
    alpha: float = 0.5

    # --- ranking -> portfolio (§2.4) ---
    rebalance_days: int = 7
    top_k: int = 5
    require_positive: bool = True
    enter_rank: int = 5
    exit_rank: int = 8
    weighting: str = "capped_inverse_vol"  # equal | inverse_vol | capped_inverse_vol
    vol_lb: int = 20
    max_weight: float = 0.30
    gross_exposure: float = 1.0

    # --- universe (§2.2) ---
    universe_n: int = 50
    liquidity_lb: int = 30
    min_age_days: int = 30

    # --- regime filter (§2.4) ---
    regime_asset: str = "BTCUSDT"
    regime_lb: int = 50
    regime_threshold: float = 0.0
    regime_band: float = 0.0  # symmetric neutral dead-band around threshold

    # --- bidirectional / adaptive extension (user 2026-08-04) ---
    direction_mode: str = "long_only"  # long_only | market_neutral | regime_adaptive | short_only
    n_short: int = 5
    score_gate_long: float = 0.0   # require score percentile >= this to go long (0 = off)
    score_gate_short: float = 1.0  # require score percentile <= this to short (1 = off)

    # --- black-swan protection (user 2026-08-04) ---
    stop_loss_pct: float = 0.0        # per-position hard stop on position P&L (0 = off); caps single-name tail
    crash_vol_cut: float = 0.0        # if universe median daily vol > this, cut gross (0 = off)
    crash_gross_scale: float = 0.5    # gross multiplier when the vol kill-switch fires

    # --- accounting / costs (§3, §4) ---
    start_equity: float = 1000.0
    fee_bps: float = 4.0            # taker futures, one side
    half_spread_bps: float = 2.0
    base_slippage_bps: float = 1.0
    impact_coef_bps: float = 10.0   # * sqrt(order_notional / ADV)
    funding_bps_per_day: float = 1.5  # ~ typical perp carry on held notional
    cost_multiplier: float = 1.0    # scenario knob: 1x / 2x / 3x
    execution_delay_days: int = 1   # fill at t+delay open

    def config_id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True).encode("utf-8")
        return "xsm_" + hashlib.blake2b(raw, digest_size=10).hexdigest()

    def as_row(self) -> dict:
        d = asdict(self)
        d["config_id"] = self.config_id()
        return d


# The single primary specification (§2). Everything else is a control or a
# preplanned neighbour perturbation.
PRIMARY = XSectMomentumPolicy()
