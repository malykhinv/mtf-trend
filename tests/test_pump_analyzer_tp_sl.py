import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from analysis.pump_analyzer import Candle, find_pumps
from config.pump_analysis import PumpAnalysisConfig


def test_tp_sl_calculation():
    candles = [
        Candle(0, 100.0, 101.0, 99.0, 100.0, 100.0),
        Candle(1, 100.0, 101.0, 99.0, 100.0, 100.0),
        Candle(2, 100.0, 120.0, 98.0, 108.0, 200.0),
        Candle(3, 108.0, 109.0, 104.0, 105.0, 100.0),
        Candle(4, 105.0, 106.0, 100.0, 101.0, 100.0),
        Candle(5, 101.0, 102.0, 99.0, 100.0, 100.0),
    ]
    config = PumpAnalysisConfig(
        growth_pct=5.0,
        wick_pct=0.3,
        volume_mult=1.5,
        volume_window=2,
        rehigh_lookahead=2,
        atr_window=1,
    )
    pumps = find_pumps(candles, config, tp_bars=3, sl_bars=3)
    assert len(pumps) == 1
    pump = pumps[0]
    assert pump["rehigh_hit"] is False
    assert pump["max_tp_pct"] == pytest.approx(8.333, rel=1e-3)
    assert pump["stop_loss_pct"] == pytest.approx(11.111, rel=1e-3)

