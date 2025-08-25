
from __future__ import annotations

from typing import Optional, List

from utils.logger import log
from config.constants import MIN_RR
from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe


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
    Вывод: одна строка — SL% TP% RR | <label>.
    Лейбл:
      * если цена не касалась SL:  Take Profit если RR > MIN_RR, иначе «Недостаточный RR»;
      * если цена касалась SL:     Take Profit если RR > MIN_RR, иначе «Stop Loss».
    """
    if not bars_after_confirm:
        return None

    entry = bars_after_confirm[0].close
    risk = entry - sl
    sl_pct = (entry - sl) / entry * 100.0

    # Поиск первого касания SL и максимум до касания
    stop_hit_idx = None
    max_high = entry
    for idx in range(1, len(bars_after_confirm)):
        b = bars_after_confirm[idx]
        if b.low <= sl:
            stop_hit_idx = idx
            break
        if b.high > max_high:
            max_high = b.high

    take_pct = max(0.0, (max_high - entry) / entry * 100.0)
    rr_best = 0.0 if risk <= 0 else max(0.0, (max_high - entry) / risk)

    # Решение по метке
    stop_hit = (stop_hit_idx is not None)
    result_label = (
        "Take Profit" if rr_best > MIN_RR
        else None if not stop_hit else "Stop Loss"
    )

    if not result_label:
        return None

    # Формат: SL% TP% RR | <label>
    sl_str = f"{sl_pct:.1f}%"
    tp_str = f"{take_pct:+.1f}%"
    rr_str = f"{rr_best:.1f}"

    pref = _log_prefix(symbol, exchange, timeframe)
    line = (
        f"{pref}"
        f"SL {sl_str:<12}"
        f"TP {tp_str:<12}"
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
