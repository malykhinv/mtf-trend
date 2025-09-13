import time
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.metrics import MetricAggregator
from config.profiles.balanced import make_profile_config_balanced
from domain.models.market_data import AggTrade
from domain.models.state import SymbolState
from domain.models.enums import BotState
from domain.services.signal_engine import SignalEngine
from domain.services.symbol_registry import SymbolRegistry


def _setup_registry() -> SymbolRegistry:
    registry = SymbolRegistry()
    state = SymbolState(symbol="ABCUSDT", state=BotState.IDLE)
    registry.put(state)
    return registry


def test_stable_price_minute(monkeypatch):
    registry = _setup_registry()
    agg = MetricAggregator(registry)
    base_ts = 1_000_000
    price = 100.0
    agg.process_trade(AggTrade("ABCUSDT", price, 1.0, base_ts))
    agg.process_trade(AggTrade("ABCUSDT", price, 1.0, base_ts + 30_000))
    config = make_profile_config_balanced()
    engine = SignalEngine(config, registry)
    monkeypatch.setattr(time, "time", lambda: (base_ts + 30_000 + 1) / 1000)
    signal = engine.on_minute_close("ABCUSDT")
    metrics = registry.get("ABCUSDT").metrics
    assert signal is None
    assert metrics.delta_price_abs_pct < config.trigger.delta_price_abs_pct


def test_delta_resets_when_no_trades(monkeypatch):
    registry = _setup_registry()
    agg = MetricAggregator(registry)
    base_ts = 1_000_000
    agg.process_trade(AggTrade("ABCUSDT", 100.0, 1.0, base_ts))
    agg.process_trade(AggTrade("ABCUSDT", 105.0, 1.0, base_ts + 30_000))
    metrics = registry.get("ABCUSDT").metrics
    assert metrics.delta_price_abs_pct > 0
    config = make_profile_config_balanced()
    engine = SignalEngine(config, registry)
    monkeypatch.setattr(time, "time", lambda: (base_ts + 120_000) / 1000)
    signal = engine.on_minute_close("ABCUSDT")
    assert signal is None
    assert registry.get("ABCUSDT").metrics.delta_price_abs_pct == 0.0
