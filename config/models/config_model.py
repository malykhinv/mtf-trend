"""Aggregated configuration dataclass wiring domain settings."""

from dataclasses import dataclass

from .admin_settings import AdminSettings
from .focus_settings import FocusSettings
from .funding_kill_switch_settings import FundingKillSwitchSettings
from .general_settings import GeneralSettings
from .metrics_settings import MetricsSettings
from .odr_settings import OdrSettings
from .position_settings import PositionSettings
from .subscription_settings import SubscriptionSettings
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
    subscriptions: SubscriptionSettings
    metrics: MetricsSettings
    admin: AdminSettings


__all__ = ["Config"]
