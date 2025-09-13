"""Conservative profile configuration builder."""

from domain.models.config import (
    ConfirmationParams,
    EntryParams,
    ProfileConfig,
    RiskParams,
    TriggerParams,
)
from domain.models.enums import Profile
from constants import RISK_PER_TRADE_USDT

DEFAULT_MARGIN_USDT = 5 * RISK_PER_TRADE_USDT


def make_profile_config_conservative() -> ProfileConfig:
    trigger = TriggerParams(
        z_px=3.5,
        z_vol=3.8,
        delta_price_sigma_mult=2.8,
        delta_price_abs_pct=1.0,
        upper_wick_body_ratio_max=0.15,
    )
    confirmation = ConfirmationParams(
        delta_oi_max_pct=0.2,
        taker_ratio_max=1.03,
        premium_max_pct=0.0,
        latency_min_sec=120,
        ask_imb_min=0.74,
        top5ask_vs_base_min=2.5,
        cvd_gap_pct_min=0.05,
        cvd_gap_sec_min=90,
    )
    entry = EntryParams(
        allow_low_break=True,
        allow_avwap_loss=True,
        require_both=True,
    )
    risk = RiskParams(
        stop_abs_pct=0.22,
        stop_sigma_mult=3.0,
        tp1_pct=0.5,
        tp2_pct=0.3,
        tail_pct=0.2,
        trail_abs_pct=0.12,
        trail_sigma_mult=1.3,
    )
    return ProfileConfig(
        profile=Profile.CONSERVATIVE,
        trigger=trigger,
        confirmation=confirmation,
        entry=entry,
        risk=risk,
        max_margin_usdt=DEFAULT_MARGIN_USDT,
        risk_per_trade_pct=0.02,
    )
