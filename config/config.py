from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded."""


@dataclass(frozen=True)
class TimeframeConfig:
    htf: str
    ltf: str


@dataclass(frozen=True)
class AtrConfig:
    atr_period: int
    atr_mult: float
    winsor_mult: float
    p_low: float
    p_high: float


@dataclass(frozen=True)
class VolumeConfig:
    n_vol_h: int
    z_min: float


@dataclass(frozen=True)
class LevelConfig:
    consol_min_bars: int
    band_max_mode: str


@dataclass(frozen=True)
class RiskRewardConfig:
    rr_min: float


@dataclass(frozen=True)
class MinTakeProfitConfig:
    min_tp_pct: float


@dataclass(frozen=True)
class OrderConfig:
    stop_trigger: float


@dataclass(frozen=True)
class PositioningConfig:
    position_fraction: float
    position_min_usdt: float


@dataclass(frozen=True)
class ScanConfig:
    market_scan_interval_h: int


@dataclass(frozen=True)
class RestDataConfig:
    fetch_htf: bool
    fetch_ltf: bool


@dataclass(frozen=True)
class SymbolFiltersConfig:
    n_avg_days: int
    min_volume_new_listing: float
    min_volume_established: float
    min_trades_established: int


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str


@dataclass(frozen=True)
class ExchangeConfig:
    name: str
    api_key: str
    api_secret: str
    breakout_price_source: str


@dataclass(frozen=True)
class LeverageConfig:
    leverage: int
    margin_mode: str


@dataclass(frozen=True)
class CooldownConfig:
    minutes: int


@dataclass(frozen=True)
class RuntimeConfig:
    timezone: str


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    timeframes: TimeframeConfig
    atr: AtrConfig
    volume: VolumeConfig
    level: LevelConfig
    risk_reward: RiskRewardConfig
    min_take_profit: MinTakeProfitConfig
    order: OrderConfig
    positioning: PositioningConfig
    scan: ScanConfig
    rest_data: RestDataConfig
    symbol_filters: SymbolFiltersConfig
    telegram: TelegramConfig
    exchange: ExchangeConfig
    leverage: LeverageConfig
    cooldown: CooldownConfig


def load_env(path: Path | str | None = None) -> None:
    """Load environment variables from a .env file."""
    env_path = Path(path) if path is not None else Path(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _get_env(key: str, default: str | None = None) -> str:
    try:
        return os.environ[key]
    except KeyError as exc:
        if default is not None:
            return default
        raise ConfigError(f"Переменная окружения {key} не задана") from exc


def _get_int(key: str, default: int | None = None) -> int:
    raw = _get_env(key, str(default) if default is not None else None)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Невозможно преобразовать {key} к int") from exc


def _get_float(key: str, default: float | None = None) -> float:
    raw = _get_env(key, str(default) if default is not None else None)
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"Невозможно преобразовать {key} к float") from exc


def _get_bool(key: str, default: bool | None = None) -> bool:
    raw_default = None if default is None else ("true" if default else "false")
    raw = _get_env(key, raw_default)
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Невозможно преобразовать {key} к bool")


def load_config() -> AppConfig:
    """Initialise and return the application configuration."""
    runtime = RuntimeConfig(
        timezone=_get_env("TIMEZONE", "Europe/Belgrade"),
    )

    timeframes = TimeframeConfig(
        htf=_get_env("HTF"),
        ltf=_get_env("LTF"),
    )

    atr = AtrConfig(
        atr_period=_get_int("ATR_PERIOD"),
        atr_mult=_get_float("ATR_MULT"),
        winsor_mult=_get_float("WINSOR_MULT"),
        p_low=_get_float("P_LOW"),
        p_high=_get_float("P_HIGH"),
    )

    volume = VolumeConfig(
        n_vol_h=_get_int("N_VOL_H"),
        z_min=_get_float("Z_MIN"),
    )

    level = LevelConfig(
        consol_min_bars=_get_int("CONSOL_MIN_BARS"),
        band_max_mode=_get_env("BAND_MAX_MODE"),
    )

    risk_reward = RiskRewardConfig(
        rr_min=_get_float("RR_MIN"),
    )

    min_take_profit = MinTakeProfitConfig(
        min_tp_pct=_get_float("MIN_TP_PCT"),
    )

    order = OrderConfig(
        stop_trigger=_get_float("STOP_TRIGGER"),
    )

    positioning = PositioningConfig(
        position_fraction=_get_float("POSITION_FRACTION"),
        position_min_usdt=_get_float("POSITION_MIN_USDT"),
    )

    scan = ScanConfig(
        market_scan_interval_h=_get_int("MARKET_SCAN_INTERVAL_H"),
    )

    rest_data = RestDataConfig(
        fetch_htf=_get_bool("REST_FETCH_HTF"),
        fetch_ltf=_get_bool("REST_FETCH_LTF"),
    )

    symbol_filters = SymbolFiltersConfig(
        n_avg_days=_get_int("N_AVG_DAYS"),
        min_volume_new_listing=_get_float("MIN_VOLUME_NEW_LISTING"),
        min_volume_established=_get_float("MIN_VOLUME_ESTABLISHED"),
        min_trades_established=_get_int("MIN_TRADES_ESTABLISHED"),
    )

    telegram = TelegramConfig(
        token=_get_env("TELEGRAM_TOKEN"),
        chat_id=_get_env("TELEGRAM_CHAT_ID"),
    )

    exchange = ExchangeConfig(
        name=_get_env("EXCHANGE"),
        api_key=_get_env("API_KEY"),
        api_secret=_get_env("API_SECRET"),
        breakout_price_source=_get_env("BREAKOUT_PRICE_SOURCE", "Last"),
    )

    leverage = LeverageConfig(
        leverage=_get_int("LEVERAGE"),
        margin_mode=_get_env("MARGIN_MODE"),
    )

    cooldown = CooldownConfig(
        minutes=_get_int("COOLDOWN_MINUTES"),
    )

    return AppConfig(
        runtime=runtime,
        timeframes=timeframes,
        atr=atr,
        volume=volume,
        level=level,
        risk_reward=risk_reward,
        min_take_profit=min_take_profit,
        order=order,
        positioning=positioning,
        scan=scan,
        rest_data=rest_data,
        symbol_filters=symbol_filters,
        telegram=telegram,
        exchange=exchange,
        leverage=leverage,
        cooldown=cooldown,
    )


__all__ = [
    "AppConfig",
    "AtrConfig",
    "CooldownConfig",
    "ConfigError",
    "ExchangeConfig",
    "LeverageConfig",
    "LevelConfig",
    "MinTakeProfitConfig",
    "OrderConfig",
    "PositioningConfig",
    "RestDataConfig",
    "RiskRewardConfig",
    "RuntimeConfig",
    "ScanConfig",
    "SymbolFiltersConfig",
    "TelegramConfig",
    "TimeframeConfig",
    "VolumeConfig",
    "load_config",
    "load_env",
]
