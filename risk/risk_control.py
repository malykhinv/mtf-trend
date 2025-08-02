"""Простые помощники для управления риском.

Модуль отслеживает размеры позиций, накопленные убытки и количество
открытых сделок. При превышении лимитов торговля может быть приостановлена,
а затем автоматически возобновлена после паузы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set
import time
from decimal import Decimal, getcontext

getcontext().prec = 10


@dataclass
class RiskLimits:
    """Конфигурация ограничений риска."""

    max_position_size: Decimal = Decimal("Infinity")
    max_daily_loss: Decimal = Decimal("Infinity")
    max_consecutive_losses: float = float("inf")
    max_open_positions: float = float("inf")
    deposit_cap: Decimal = Decimal("Infinity")


@dataclass
class RiskState:
    """Текущее состояние риска, обновляемое после сделок."""

    total_notional: Decimal = Decimal("0")
    daily_loss: Decimal = Decimal("0")
    consecutive_losses: int = 0
    paused: bool = False
    open_symbols: Set[str] = field(default_factory=set)
    open_positions: int = 0
    pause_until: Optional[float] = None


_limits = RiskLimits()
_state = RiskState()


def configure(config: Dict[str, Optional[float]], deposit_size: Optional[float] = None) -> None:
    """Настраивает пределы риска из словаря.

    Параметры
    ---------
    config:
        Словарь с параметрами контроля риска.
    deposit_size:
        Размер депозита. Используется для расчёта абсолютного лимита, если
        ``deposit_cap`` явно не задан и указан процент ``deposit_cap_pct``.
    """

    global _limits

    dep_cap_value = config.get("deposit_cap")
    deposit_cap = (
        Decimal("Infinity") if dep_cap_value is None else Decimal(str(dep_cap_value))
    )
    if deposit_cap.is_infinite() and deposit_size is not None:
        pct = config.get("deposit_cap_pct")
        if pct is not None:
            deposit_cap = Decimal(str(pct)) * Decimal(str(deposit_size))

    max_pos_size = config.get("max_position_size")
    max_daily_loss = config.get("max_daily_loss")
    max_consecutive_losses = config.get("max_consecutive_losses")
    max_open_positions = config.get("max_open_positions")

    _limits = RiskLimits(
        max_position_size=
            Decimal("Infinity") if max_pos_size is None else Decimal(str(max_pos_size)),
        max_daily_loss=
            Decimal("Infinity") if max_daily_loss is None else Decimal(str(max_daily_loss)),
        max_consecutive_losses=
            float("inf") if max_consecutive_losses is None else max_consecutive_losses,
        max_open_positions=
            float("inf") if max_open_positions is None else max_open_positions,
        deposit_cap=deposit_cap,
    )


def can_open_position(notional: Decimal) -> bool:
    """Возвращает ``True``, если позицию на ``notional`` USD можно открыть."""

    if _state.paused:
        return False
    if _state.open_positions >= _limits.max_open_positions:
        return False
    if _state.total_notional + notional > _limits.deposit_cap:
        return False
    if _state.total_notional + notional > _limits.max_position_size:
        return False
    if _state.daily_loss >= _limits.max_daily_loss:
        return False
    return True


def update_position(delta_notional: Decimal) -> None:
    """Обновляет учёт текущей нагрузки на депозит на ``delta_notional`` USD."""

    _state.total_notional = max(_state.total_notional + delta_notional, Decimal("0"))


def is_symbol_open(symbol: str) -> bool:
    """Возвращает ``True``, если позиция по ``symbol`` уже открыта."""

    return symbol in _state.open_symbols


def mark_symbol_open(symbol: str) -> None:
    """Помечает ``symbol`` как открытую позицию."""

    _state.open_symbols.add(symbol)
    _state.open_positions = len(_state.open_symbols)


def mark_symbol_closed(symbol: str) -> None:
    """Удаляет ``symbol`` из набора открытых позиций."""

    _state.open_symbols.discard(symbol)
    _state.open_positions = len(_state.open_symbols)


def pause(duration: Optional[float] = None) -> None:
    """Приостанавливает торговлю на ``duration`` секунд или бессрочно."""

    _state.paused = True
    _state.pause_until = time.time() + duration if duration else None


def record_pnl(pnl: Decimal) -> None:
    """Фиксирует прибыль или убыток по завершённой сделке."""

    if pnl < 0:
        _state.daily_loss += abs(pnl)
        _state.consecutive_losses += 1
        if _state.consecutive_losses >= _limits.max_consecutive_losses:
            pause(24 * 3600)
    else:
        _state.consecutive_losses = 0


def is_paused() -> bool:
    """Возвращает ``True``, если торговля сейчас приостановлена."""

    if _state.paused and _state.pause_until and time.time() >= _state.pause_until:
        # Истёк таймер паузы — возобновляем торговлю
        _state.paused = False
        _state.pause_until = None
        _state.consecutive_losses = 0
    return _state.paused
