import pandas as pd
from utils.breakout_signals import Signal
import main


class DummyCollector:
    def __init__(self, data):
        self._data = data

    def collect(self):
        return self._data


class DummyScreener:
    def __init__(self, symbol):
        self.symbol = symbol

    def screen(self, data):
        return [self.symbol]


class DummyFilter:
    def filter(self, data):
        return data


class DummyRisk:
    def __init__(self):
        self.opened = False

    def can_open_trade(self):
        return True

    def open_trade(self, entry, stop):
        self.opened = True
        return 1.0

    def close_trade(self, pnl):
        self.opened = False


class DummyTrader:
    def __init__(self):
        self.calls = []

    def place_limit_maker_order(self, symbol, side, amount, price, tp=None, sl=None):
        self.calls.append((symbol, side, amount, price, tp, sl))
        return True

    def place_market_order(self, symbol, side, amount, tp=None, sl=None):
        self.calls.append((symbol, side, amount, None, tp, sl))
        return True


def test_scan_and_enter_executes_long(monkeypatch):
    symbol = "BTC/USDT"
    timestamps = pd.date_range("2024-01-01", periods=2, freq="1T")
    ohlcv = pd.DataFrame({
        "timestamp": timestamps,
        "open": [100, 100],
        "high": [101, 101],
        "low": [99, 99],
        "close": [100, 101],
        "volume": [1000, 1500],
    })
    cvd = pd.Series([1, 2], index=timestamps)
    info = {
        "ohlcv": ohlcv,
        "cvd": cvd,
        "volume_delta": 0.0,
        "open_interest": 10.0,
        "funding_rate": 0.0,
    }
    main.data_collector = DummyCollector({symbol: info})
    main.screener = DummyScreener(symbol)
    main.trend_filter = DummyFilter()
    main.risk_manager = DummyRisk()
    main.trader = DummyTrader()
    btc = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6]})
    eth = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6]})
    monkeypatch.setattr(main, "load_btc_eth_candles", lambda: {"BTC/USDT": btc, "ETH/USDT": eth})

    def fake_clusters(df, atr_multiplier=2.0, min_bars=5, max_bars=30):
        return pd.DataFrame([{"start": timestamps[0], "end": timestamps[-1], "high": 101, "low": 99, "duration": 2}])

    monkeypatch.setattr(main, "find_tight_range_clusters", fake_clusters)
    monkeypatch.setattr(
        main,
        "evaluate_breakout",
        lambda ohlcv, level, cvd, oi, volume_stats, funding: [Signal("long", 101, 99, 103, 105)],
    )

    main.open_long = False
    main.open_short = False

    main.scan_and_enter()

    assert main.open_long
    assert not main.open_short
    assert main.trader.calls
