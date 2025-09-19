from bot.app import build_thresholds
from bot.data.io.config_loader import AppConfig
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
