import pandas as pd

from utils.market_analysis import has_consecutive_move, price_above_ema
import main


class Dummy:
    def collect(self, symbols=None):
        return {}

    def screen(self):
        return []


class DummyFilter:
    def __init__(self, allow_long=True, allow_short=True):
        self.allow_long = allow_long
        self.allow_short = allow_short

    def filter(self, data):
        return data


def test_has_consecutive_move():
    df_up = pd.DataFrame({"close": [1, 2, 3, 4, 5, 6]})
    assert has_consecutive_move(df_up, "up")
    assert not has_consecutive_move(df_up, "down")
    df_down = pd.DataFrame({"close": [6, 5, 4, 3, 2, 1]})
    assert has_consecutive_move(df_down, "down")
    assert not has_consecutive_move(df_down, "up")


def test_price_above_ema():
    df = pd.DataFrame({"close": list(range(1, 41))})
    assert price_above_ema(df)
    df2 = pd.DataFrame({"close": list(range(40, 0, -1))})
    assert not price_above_ema(df2)


def test_scan_and_enter_cancels_long(monkeypatch):
    main.data_collector = Dummy()
    main.screener = Dummy()
    main.trend_filter = DummyFilter(allow_long=False)
    main.open_long = True
    main.open_short = False
    main.scan_and_enter()
    assert not main.open_long


def test_scan_and_enter_cancels_short(monkeypatch):
    main.data_collector = Dummy()
    main.screener = Dummy()
    main.trend_filter = DummyFilter(allow_short=False)
    main.open_short = True
    main.open_long = False
    main.scan_and_enter()
    assert not main.open_short
