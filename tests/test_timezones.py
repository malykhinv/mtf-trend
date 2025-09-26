from zoneinfo import ZoneInfo

from bot.data.io.config_loader import AppConfig
from bot.data.io.config_types import parse_app_config_payload
from bot.data.mappers.ohlcv_mapper import map_ohlcv
from bot.data.models import BinanceKline, BinanceKlineData
from bot.domain.enums import Exchange, Timeframe
from bot.utils.clock import get_timezone, init_clock


def test_app_config_timezone_defaults_to_utc() -> None:
    config = AppConfig(parse_app_config_payload({}))
    assert config.time.zone == "UTC"


def test_map_ohlcv_uses_configured_timezone() -> None:
    tz = ZoneInfo("Europe/Belgrade")
    init_clock(tz)
    base_timestamp = 1_700_000_000
    raw = [base_timestamp, 1.0, 2.0, 0.5, 1.5, 100.0]

    candle = map_ohlcv(
        BinanceKline.from_payload(BinanceKlineData.decode(raw)),
        "BTCUSDT",
        Exchange.BINANCE,
        Timeframe.M1,
    )

    assert candle.started_at.tzinfo == tz
    assert candle.closed_at.tzinfo == tz
    assert int(candle.started_at.timestamp()) == base_timestamp
    assert candle.id.endswith(str(base_timestamp))
    assert get_timezone() is tz
