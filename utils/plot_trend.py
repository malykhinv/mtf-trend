import os
import matplotlib.pyplot as plt

from domain.models.swing_point import SwingPoint
from domain.models.swing_type import SwingType
from domain.models.bar import Bar
from typing import List

def plot_trend(bars: List[Bar], swings: List[SwingPoint], timeframe_name: str, file_name: str):
    """
    Строит график свечей, отмечает swing points, сохраняет png.
    """
    x = list(range(len(bars)))
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    opens = [bar.open for bar in bars]
    closes = [bar.close for bar in bars]

    plt.figure(figsize=(14, 7))

    for i in range(len(bars)):
        # Рисуем тень (wick)
        plt.plot([x[i], x[i]], [lows[i], highs[i]], color='black', linewidth=1)

        # Определяем цвет тела: зелёный (close > open), красный (close <= open)
        color = 'green' if closes[i] > opens[i] else 'red'

        # Координаты тела
        top = max(opens[i], closes[i])
        bottom = min(opens[i], closes[i])

        plt.plot([x[i], x[i]], [bottom, top], color=color, linewidth=4)

    # Swing points
    high_label_done = False
    low_label_done = False
    for sp in swings:
        if sp.type == SwingType.HIGH:
            label = 'Swing High' if not high_label_done else ""
            high_label_done = True
            plt.scatter(sp.index, sp.price, color='blue', marker='^', s=80, label=label)
        elif sp.type == SwingType.LOW:
            label = 'Swing Low' if not low_label_done else ""
            low_label_done = True
            plt.scatter(sp.index, sp.price, color='orange', marker='v', s=80, label=label)

    # Линия тренда по свингам
    swing_x = [sp.index for sp in swings]
    swing_y = [sp.price for sp in swings]
    plt.plot(swing_x, swing_y, linestyle='--', color='blue', alpha=0.7, label='Trend Structure')

    plt.title(f"Trend Structure — {timeframe_name}")
    plt.xlabel("Bars")
    plt.ylabel("Price")
    plt.legend()
    plt.grid(True)

    out_dir = ".generated/plot/charts"
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, f"{file_name}.png")
    plt.savefig(out_path, dpi=150)
    plt.close()