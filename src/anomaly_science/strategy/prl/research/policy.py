"""Frozen policy for PRL-COARSE-000 (spec §5.2.1, §32.1.1, §79-note).

Every field is pre-registered. Changing a value is a §60 trial-ledger event, not a
casual tweak. The dataclass is frozen + hashable so each executed configuration is
stamped with a stable config_id.

PRL-COARSE-000 is deliberately the SIMPLEST coarse existence test (§79 rationale):
does *market-residual* multi-horizon momentum rank predict future *residual* return
across the 2023-2026 multi-regime daily panel, after removing broad-market beta?
Peers (§8), persistence (§14) and flow (§19) are LATER ladder modules, not here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class PRLCoarsePolicy:
    version: str = "prl_coarse_v1_2026-08-12"
    strategy_name: str = "prl_daily_v1"
    tier: str = "coarse"

    # --- signal: multi-horizon market-residual momentum (§12, §13) ------------
    # residual cumulative-return lookbacks in trading days; ranked per date and
    # averaged into one composite percentile score.
    mom_lbs: tuple[int, ...] = (7, 14, 28)
    skip: int = 1  # decision at close(t) uses residuals through t-skip (no same-bar)

    # --- primary discovery target (§28, §29, §32.1.1) -------------------------
    # forward market-residual return over fwd_horizon days, frozen-beta (§9.1).
    fwd_horizon: int = 5

    # --- market factor (§7) ---------------------------------------------------
    # robust common factor: a single symbol cannot dominate a trimmed mean, which
    # achieves the leave-one-out objective (§7.1) without per-symbol refit. Exact
    # mean_loo is a pre-registered robustness variant.
    market_factor: str = "trimmed_mean"  # trimmed_mean | median | mean | mean_loo
    trim_frac: float = 0.10

    # --- rolling beta, as-of-t, shrunk to 1 (§7.2, §9.4) ----------------------
    beta_lb: int = 60
    beta_min_periods: int = 30
    beta_shrink: float = 0.30   # beta = shrink*1 + (1-shrink)*beta_raw
    beta_clip_lo: float = -2.0
    beta_clip_hi: float = 4.0

    # --- point-in-time universe (§6, §6.1) ------------------------------------
    universe_n: int = 100
    liquidity_lb: int = 30
    min_age_days: int = 60
    min_xs: int = 20            # min symbols in a cross-section to score its IC

    # --- regime context (§6.3.1, §50, §67) ------------------------------------
    regime_asset: str = "BTCUSDT"
    regime_lb: int = 50
    regime_band: float = 0.05   # |trend| < band -> sideways

    # --- IS/OOS boundary (§6.3.2) — OOS is reserved, never read in this run ----
    oos_start: str = "2026-01-01"

    # --- economic co-primary diagnostic (§32.1.2, §65.1) ----------------------
    # decile long-short spread, netted with a simple round-trip cost model.
    fee_bps: float = 4.0
    half_spread_bps: float = 2.0
    base_slippage_bps: float = 1.0
    cost_multiplier: float = 1.0

    # --- inference (§32.2, §32.4, §33) ----------------------------------------
    boot_block: int = 20        # moving-block length in days (>= horizon overlap)
    boot_n: int = 2000
    power_alpha: float = 0.05   # one-sided
    power_target: float = 0.80
    seed: int = 7

    @property
    def rebalance_days(self) -> int:
        """Non-overlapping cadence for decile/power = the label horizon."""
        return self.fwd_horizon

    def config_id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True).encode("utf-8")
        return "prlc_" + hashlib.blake2b(raw, digest_size=10).hexdigest()

    def as_row(self) -> dict:
        d = asdict(self)
        d["config_id"] = self.config_id()
        return d


# The single primary specification (§32.1.1). Neighbours are §68 perturbations.
PRIMARY = PRLCoarsePolicy()
