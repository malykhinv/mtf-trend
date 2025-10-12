"""Aggregated configuration dataclass wiring domain settings."""

from dataclasses import dataclass

from .focus_settings import FocusSettings
from .funding_kill_switch_settings import FundingKillSwitchSettings
from .general_settings import GeneralSettings
from .odr_settings import OdrSettings
from .position_settings import PositionSettings
from .profile_stream_weights import ProfileStreamWeights
from .telegram_settings import TelegramSettings
from .turnover_thresholds import TurnoverThresholds
from .wall_settings import WallSettings
from .wall_shift_settings import WallShiftSettings


@dataclass(frozen=True)
class Config:
    general: GeneralSettings
    turnover: TurnoverThresholds
    odr: OdrSettings
    walls: WallSettings
    wall_shift: WallShiftSettings
    position: PositionSettings
    focus: FocusSettings
    funding_ks: FundingKillSwitchSettings
    telegram: TelegramSettings
    profile_stream_weights: ProfileStreamWeights


__all__ = ["Config"]
