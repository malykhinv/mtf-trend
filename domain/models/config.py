from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerParams:
    z_px: float
    z_vol: float
    delta_price_sigma_mult: float
    delta_price_abs_pct: float
    close_pos: float
    liqs_z: float


@dataclass(frozen=True)
class ConfirmationParams:
    delta_oi_max_pct: float
    taker_ratio_max: float
    premium_max_pct: float
    latency_min_sec: int
    ask_imb_min: float
    top5ask_vs_base_min: float
    cvd_gap_pct_min: float
    cvd_gap_sec_min: int


@dataclass(frozen=True)
class EntryParams:
    allow_low_break: bool
    allow_avwap_loss: bool
    require_both: bool


@dataclass(frozen=True)
class RiskParams:
    stop_abs_pct: float
    stop_sigma_mult: float
    tp1_pct: float
    tp2_pct: float
    tail_pct: float
    trail_abs_pct: float
    trail_sigma_mult: float


@dataclass(frozen=True)
class ProfileConfig:
    trigger: TriggerParams
    confirmation: ConfirmationParams
    entry: EntryParams
    risk: RiskParams
