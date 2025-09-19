from bot.app import build_thresholds
from bot.data.io.config_loader import AppConfig
from bot.data.io.config_models import BinanceProviderConfig, BybitProviderConfig
from bot.domain.enums import Timeframe


def test_symbol_selection_and_overrides_are_typed() -> None:
    config = AppConfig(
        raw={
            "symbols": {
                "selection": {
                    "quote_suffix": "usdc",
                    "min_quote_volume": "2000000",
                    "allow": ["btcusdt", "ethusdt"],
                    "deny": "dogeusdt",
                },
                "providers": {"BTCUSDT": "binance", "default": "bybit"},
            },
            "live": {
                "enabled": "false",
                "provider": "binance",
                "timeframe": "5m",
                "window": "25",
                "allow": ["btcusdt"],
            },
        }
    )

    selection = config.symbol_selection
    assert selection.quote_suffix == "USDC"
    assert selection.min_quote_volume == 2_000_000.0
    assert selection.allow == frozenset({"BTCUSDT", "ETHUSDT"})
    assert selection.deny == frozenset({"DOGEUSDT"})

    overrides = config.selection_overrides_for("live")
    assert overrides.allow == frozenset({"BTCUSDT"})
    assert overrides.symbols == frozenset()
    assert overrides.deny == frozenset()

    live_cfg = config.live
    assert live_cfg.enabled is False
    assert live_cfg.providers == ("binance",)
    assert live_cfg.timeframe == Timeframe.M5
    assert live_cfg.window == 25
    assert config.symbol_provider_mapping["BTCUSDT"] == "binance"
    assert config.symbol_provider_mapping["default"] == "bybit"


def test_metrics_and_dedup_configs_are_typed() -> None:
    config = AppConfig(
        raw={
            "metrics": {
                "atr_period": "21",
                "volume_period": 30.9,
                "momentum_period": 0,
            },
            "dedup": {
                "ttl_seconds": "3600",
                "max_records": "0",
            },
        }
    )

    metrics = config.metrics
    assert metrics.atr_period == 21
    assert metrics.volume_period == 30
    assert metrics.momentum_period == 1

    dedup = config.dedup
    assert dedup.ttl_seconds == 3600
    assert dedup.max_records == 1

    defaults = AppConfig(raw={})
    assert defaults.metrics.atr_period == 14
    assert defaults.metrics.volume_period == 20
    assert defaults.metrics.momentum_period == 5
    assert defaults.dedup.ttl_seconds == 14_400
    assert defaults.dedup.max_records == 1_000


def test_build_thresholds_uses_typed_models() -> None:
    config = AppConfig(
        raw={
            "thresholds": {
                "default": {
                    "min_relative_volume": "1.5",
                    "allow_long": False,
                    "short_pct_move_ranges": [
                        {"min": 0.5, "max": 1.0},
                    ],
                    "metrics": [
                        {"name": "rsi", "min_value": "5", "max_value": 60},
                    ],
                },
                "symbols": {
                    "ETHUSDT": {
                        "min_pct_move": "0.25",
                        "allow_short": False,
                    }
                },
            }
        }
    )

    thresholds = build_thresholds(config)

    default_cfg = thresholds["default"]
    assert default_cfg.min_relative_volume == 1.5
    assert default_cfg.allow_long is False
    assert default_cfg.short_pct_move_ranges == [(0.5, 1.0)]
    assert default_cfg.metrics[0].name == "rsi"
    assert default_cfg.metrics[0].min_value == 5.0
    assert default_cfg.metrics[0].max_value == 60.0

    eth_cfg = thresholds["ETHUSDT"]
    assert eth_cfg.min_pct_move == 0.25
    assert eth_cfg.allow_short is False


def test_provider_configs_are_typed_and_resolve_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", "from-env")
    config = AppConfig(
        raw={
            "providers": {
                "binance": {
                    "api_base": "https://fapi.binance.com",
                    "ws_base": "wss://fstream.binance.com/ws",
                    "rate_limit_per_minute": "1200",
                    "min_quote_volume": "500000",
                    "api_key_env": "BINANCE_KEY",
                },
                "bybit": {
                    "api_base": "https://api.bybit.com",
                    "ws_base": "wss://stream.bybit.com/v5/public/linear",
                    "rate_limit_per_minute": 600,
                    "min_quote_volume": 250000,
                    "api_secret": "super-secret",
                },
            },
            "env": {"BINANCE_KEY": "binance-from-env"},
        }
    )

    providers = config.providers
    assert set(providers) == {"binance", "bybit"}

    binance_cfg = providers["binance"]
    assert isinstance(binance_cfg, BinanceProviderConfig)
    assert binance_cfg.api_base == "https://fapi.binance.com"
    assert binance_cfg.ws_base == "wss://fstream.binance.com/ws"
    assert binance_cfg.rate_limit_per_minute == 1200
    assert binance_cfg.min_quote_volume == 500000.0
    assert binance_cfg.api_key_env == "BINANCE_KEY"
    assert binance_cfg.api_secret_env == "BINANCE_API_SECRET"
    assert binance_cfg.get_api_key(config.env) == "binance-from-env"
    assert binance_cfg.get_api_secret(config.env) is None

    bybit_cfg = providers["bybit"]
    assert isinstance(bybit_cfg, BybitProviderConfig)
    assert bybit_cfg.api_key_env == "BYBIT_API_KEY"
    assert bybit_cfg.api_secret_env == "BYBIT_API_SECRET"
    assert bybit_cfg.get_api_key(config.env) == "from-env"
    assert bybit_cfg.get_api_secret(config.env) == "super-secret"
