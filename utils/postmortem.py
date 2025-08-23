
from __future__ import annotations

from typing import Optional, List

from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe
from utils.logger import log


def _log_prefix(symbol: str, exchange: Exchange, timeframe: Timeframe) -> str:
    # compact, fixed width columns for easier scanning in logs
    return f"{symbol.split('/', 1)[0]:<12}{exchange.value:<10}{timeframe.value:<4}"


def log_postmortem_from_confirm(
    symbol: str,
    exchange: Exchange,
    timeframe: Timeframe,
    bars_after_confirm: List[Bar],
    sl: float,
) -> Optional[dict]:
    """
    Постмортем по эпохе (лонг) после её «закрепления».
    bars_after_confirm[0] — подтверждающая свеча (вход = её close).
    sl — стоп-уровень (лой на участке [%последний_значимый_хай:]).
    Вывод: одна строка с TP%, SL%, RR и меткой события, которое произошло раньше (Take Profit | Stop Loss).
    """
    if not bars_after_confirm:
        return None

    entry = bars_after_confirm[0].close
    risk = entry - sl
    sl_pct = (entry - sl) / entry * 100.0  # может быть <= 0, если SL невалиден

    # Поиск первого срабатывания SL и одновременный сбор максимума до этого момента
    stop_hit_idx = None
    max_high = entry
    for idx in range(1, len(bars_after_confirm)):
        b = bars_after_confirm[idx]
        if b.low <= sl:
            stop_hit_idx = idx
            break
        if b.high > max_high:
            max_high = b.high

    # Если стоп сработал — максимум уже учтён до стопа
    take_pct = max(0.0, (max_high - entry) / entry * 100.0)
    rr_best = 0.0 if risk <= 0 else max(0.0, (max_high - entry) / risk)

    # Что произошло раньше?
    result_label = "Stop Loss" if stop_hit_idx is not None else "Take Profit"

    # Формат: TP +18.3%:<12 SL 5.4%:<12 RR 3.9 :<12 | Take Profit
    tp_str = f"{take_pct:+.1f}%"
    sl_str = f"{sl_pct:.1f}%"
    rr_str = f"{rr_best:.1f}"

    pref = _log_prefix(symbol, exchange, timeframe)
    line = (
        f"{pref}"
        f"TP {tp_str:<12}"
        f"SL {sl_str:<12}"
        f"RR {rr_str:<12}"
        f"| {result_label}"
    )
    log(line)

    return {
        "entry": entry,
        "sl": sl,
        "stop_hit_after_bars": stop_hit_idx,
        "max_high_before_stop": max_high,
        "take_pct": take_pct,
        "sl_pct": sl_pct,
        "rr_best": rr_best,
        "result": result_label,
    }
