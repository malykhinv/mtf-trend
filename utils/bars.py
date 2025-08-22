from config.constants import HIGH_SHIFT
from domain.models.Bar import Bar


def cut_bars_from_high(bars: list[Bar]) -> list[Bar]:
    if not bars:
        return []

    # Находим индекс бара с максимальным high (при равенстве берём первый)
    max_idx = max(range(len(bars)), key=lambda i: bars[i].high)
    # Левая граница со сдвигом влево, но не меньше нуля
    start_idx = max(0, max_idx - HIGH_SHIFT)

    return bars[start_idx:]