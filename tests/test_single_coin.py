from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import asyncio
import sys

# добавить корень проекта в путь
sys.path.append(str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data.memory_client import MemoryExchangeClient
from domain.bar import Bar
from domain.detection import detect_extremums
from domain.extremum import ExtremumType
from domain.timeframe import Timeframe
from utils.atr import atr


class MatplotlibPlotter:
    def plot(self, bars, extremums, path: str) -> None:
        times = [b.time for b in bars]
        closes = [b.close for b in bars]
        plt.figure(figsize=(6, 4))
        plt.plot(times, closes, label="close")
        for ext in extremums:
            color = "g" if ext.type is ExtremumType.HIGH else "r"
            plt.scatter([ext.time], [ext.price], c=color)
        plt.legend()
        plt.savefig(path)
        plt.close()


def test_single_coin() -> None:
    base = datetime(2023, 1, 1)
    bars = [
        Bar(base, 9.2, 10.0, 9.0, 9.5, 100),
        Bar(base + timedelta(hours=1), 11.0, 13.0, 10.0, 12.0, 150),
        Bar(base + timedelta(hours=2), 12.0, 12.0, 8.0, 9.0, 130),
        Bar(base + timedelta(hours=3), 13.0, 15.0, 11.0, 14.0, 160),
        Bar(base + timedelta(hours=4), 14.0, 14.0, 12.0, 13.0, 140),
    ]
    client = MemoryExchangeClient({"TEST": bars}, {"TEST": 2_000_000_000})
    fetched = asyncio.run(client.fetch_bars("TEST", Timeframe.H1, 10))
    assert fetched == bars

    extremums = detect_extremums(bars)
    assert any(ext.type is ExtremumType.HIGH for ext in extremums)
    assert any(ext.type is ExtremumType.LOW for ext in extremums)

    atr_values = atr(bars, period=14)
    assert len(atr_values) == len(bars)

    plotter = MatplotlibPlotter()
    out_path = Path(".output/test_plot.png")
    plotter.plot(bars, extremums, str(out_path))
    assert out_path.exists()

