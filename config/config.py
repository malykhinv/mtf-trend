from __future__ import annotations

from typing import Final

from config.models import (
    BalanceSource,
    Config,
    ExchangeName,
    FocusSettings,
    FundingKillSwitchSettings,
    GeneralSettings,
    MarginMode,
    OdrSettings,
    PositionSettings,
    StopTrigger,
    SubscriptionSettings,
    TelegramSettings,
    TradingProfile,
    TurnoverThresholds,
    WallAbsoluteThresholds,
    WallSettings,
    WallShiftSettings,
)

CONFIG: Final[Config] = Config(
    general=GeneralSettings(
        exchange=ExchangeName.BINANCE,
        profile=TradingProfile.AUTO,
        loop_interval_ms=100,
        ws_silence_timeout_ms=30_000,
        depth_stream_interval_ms=250,
        depth_snapshot_limit=100,
        odr_smooth_samples=3,
        recent_band_s=5,
        recent_band_s_vol_boost=8,
        vol_spike_mult=2.0,
    ),
    turnover=TurnoverThresholds(
        top_usd=700_000,
        alt_usd=120_000,
        listing_usd=250_000,
        market_scan_interval_s=30,
    ),
    odr=OdrSettings(
        odr_in_short=3.1,
        odr_in_long=0.322,
        odr_neutral_low=0.70,
        odr_neutral_high=1.45,
        odr_neutral_hold_ms=800,
        focus_pre_odr_short=2.0,
        focus_pre_odr_long=0.50,
    ),
    walls=WallSettings(
        absolute=WallAbsoluteThresholds(
            top_usd=600_000,
            alt_usd=120_000,
            listing_usd=200_000,
        ),
        relative_multiplier=3.5,
        persist_s_top=2.0,
        persist_s_alt=2.0,
        persist_s_listing=3.0,
    ),
    wall_shift=WallShiftSettings(
        shift_min_ticks=1,
        shift_hold_s=0.8,
        vanish_drop=0.50,
        vanish_grace_ms=600,
    ),
    position=PositionSettings(
        position_fraction=0.10,
        position_min_usdt=10,
        balance_refresh_h=1,
        balance_source=BalanceSource.AVAILABLE_BALANCE,
        margin_mode=MarginMode.ISOLATED,
        leverage=1,
        stop_trigger=StopTrigger.MARK_PRICE,
    ),
    focus=FocusSettings(defocus_timeout_s=30),
    funding_ks=FundingKillSwitchSettings(
        funding_block_s=30,
        max_stops_per_min=3,
        max_drawdown_frac=0.005,
        block_min=5,
    ),
    telegram=TelegramSettings(
        chat_id=739865715,
        silent=False,
        uptick_cooldown_min=5,
    ),
    subscriptions=SubscriptionSettings(
        # При мониторинге сотен инструментов рекомендуется удерживать
        # порядка 80 одновременных подписок и подключать не более четырёх
        # новых потоков за цикл, чтобы избежать бурстов и перегрузки.
        max_total=80,
        max_new_per_cycle=4,
    ),
)

__all__ = ["CONFIG"]
