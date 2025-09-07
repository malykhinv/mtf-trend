"""Profile configuration builders."""

from domain.models.config import (
    ConfirmationParams,
    EntryParams,
    ProfileConfig,
    RiskParams,
    TriggerParams,
)


def make_profile_config_conservative() -> ProfileConfig:
    trigger = TriggerParams(
        z_px=3.5,
        z_vol=3.8,
        delta_price_sigma_mult=2.8,
        delta_price_abs_pct=1.0,
        close_pos=0.75,
        liqs_z=2.5,
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
    return ProfileConfig(trigger=trigger, confirmation=confirmation, entry=entry, risk=risk)


def make_profile_config_balanced() -> ProfileConfig:
    trigger = TriggerParams(
        z_px=3.2,
        z_vol=3.3,
        delta_price_sigma_mult=2.6,
        delta_price_abs_pct=0.9,
        close_pos=0.70,
        liqs_z=2.0,
    )
    confirmation = ConfirmationParams(
        delta_oi_max_pct=0.3,
        taker_ratio_max=1.05,
        premium_max_pct=0.0,
        latency_min_sec=90,
        ask_imb_min=0.72,
        top5ask_vs_base_min=2.2,
        cvd_gap_pct_min=0.05,
        cvd_gap_sec_min=60,
    )
    entry = EntryParams(
        allow_low_break=True,
        allow_avwap_loss=True,
        require_both=False,
    )
    risk = RiskParams(
        stop_abs_pct=0.20,
        stop_sigma_mult=2.8,
        tp1_pct=0.5,
        tp2_pct=0.3,
        tail_pct=0.2,
        trail_abs_pct=0.12,
        trail_sigma_mult=1.25,
    )
    return ProfileConfig(trigger=trigger, confirmation=confirmation, entry=entry, risk=risk)


def make_profile_config_active() -> ProfileConfig:
    trigger = TriggerParams(
        z_px=2.9,
        z_vol=3.0,
        delta_price_sigma_mult=2.3,
        delta_price_abs_pct=0.8,
        close_pos=0.65,
        liqs_z=1.8,
    )
    confirmation = ConfirmationParams(
        delta_oi_max_pct=0.5,
        taker_ratio_max=1.08,
        premium_max_pct=0.01,
        latency_min_sec=60,
        ask_imb_min=0.70,
        top5ask_vs_base_min=2.0,
        cvd_gap_pct_min=0.05,
        cvd_gap_sec_min=60,
    )
    entry = EntryParams(
        allow_low_break=True,
        allow_avwap_loss=True,
        require_both=False,
    )
    risk = RiskParams(
        stop_abs_pct=0.18,
        stop_sigma_mult=2.5,
        tp1_pct=0.45,
        tp2_pct=0.275,
        tail_pct=0.25,
        trail_abs_pct=0.10,
        trail_sigma_mult=1.1,
    )
    return ProfileConfig(trigger=trigger, confirmation=confirmation, entry=entry, risk=risk)
