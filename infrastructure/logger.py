from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from domain.models import Level, LevelPattern, Pump


class Notifier(Protocol):
    """Protocol describing a notification backend (e.g. Telegram)."""

    def send(self, message: str) -> None:  # pragma: no cover - interface definition
        """Dispatch ``message`` to the remote channel."""


_LOG_FORMAT = "%(asctime)s %(message)s"
_TIME_FORMAT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger with the strategy specific format."""

    logger = logging.getLogger()
    logger.setLevel(level)

    # Avoid duplicate handlers when ``setup_logging`` is called multiple times.
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_TIME_FORMAT))
        logger.addHandler(handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a module-specific logger configured for the application."""

    return logging.getLogger(name)


@dataclass(frozen=True)
class BreakoutContext:
    symbol: str
    entry_price: float
    rr: float


@dataclass(frozen=True)
class OrderPlacementContext:
    symbol: str
    timeframe_pair: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    position_size: float
    rr: float


def _emit(level: int, logger: logging.Logger, notifier: Notifier | None, message: str) -> None:
    logger.log(level, message)
    if notifier is None:
        return
    try:
        notifier.send(message)
    except Exception:  # pragma: no cover - defensive, notifier errors should not stop trading
        logger.warning("Не удалось отправить уведомление: %s", message, exc_info=True)


def log_pump_detected(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    pump: Pump,
    atr_value: float,
    atr_mult: float,
) -> None:
    candle = pump.candle
    body = candle.close - candle.open
    message = (
        f"{symbol}: обнаружена свеча C* {candle.timestamp:%Y-%m-%d %H:%M} "
        f"high={candle.high:.4f} low={candle.low:.4f} body={body:.4f} "
        f"ATR={atr_value:.4f}×{atr_mult:.2f}"
    )
    _emit(logging.INFO, logger, notifier, message)


def log_pullback_status(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    l_pullback: float | None,
    valid: bool,
    reason: str | None,
) -> None:
    if valid and l_pullback is not None:
        message = f"{symbol}: подтверждена коррекция, L_pullback={l_pullback:.4f}"
        _emit(logging.INFO, logger, notifier, message)
        return
    if reason is None:
        return
    translated = {
        "higher_high": "перехай",
        "deep_pullback": "глубокая коррекция",
        "invalid_pump_body": "некорректное тело свечи",
    }.get(reason, reason)
    message = f"{symbol}: сценарий отменён — {translated}"
    _emit(logging.INFO, logger, notifier, message)


def log_level_identified(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    level: Level,
    pattern: LevelPattern,
    swings_count: int,
) -> None:
    pattern_label = (
        "A"
        if pattern == LevelPattern.MULTIPLE_SWINGS
        else "B"
    )
    message = (
        f"{symbol}: построен уровень ({pattern_label}) low={level.level_low:.4f} "
        f"top={level.level_top:.4f} width={level.width:.4f} swings={swings_count}"
    )
    _emit(logging.INFO, logger, notifier, message)


def log_breakout(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    context: BreakoutContext,
) -> None:
    message = (
        f"{context.symbol}: пробой уровня, entry={context.entry_price:.4f}, RR={context.rr:.2f}"
    )
    _emit(logging.INFO, logger, notifier, message)


def log_orders_placed(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    context: OrderPlacementContext,
) -> None:
    message = (
        f"Ордер размещён: {context.symbol} ({context.timeframe_pair}) entry={context.entry_price:.4f} "
        f"stop={context.stop_loss:.4f} tp1={context.take_profit_1:.4f} tp2={context.take_profit_2:.4f} "
        f"RR={context.rr:.2f} размер={context.position_size:.4f}"
    )
    _emit(logging.INFO, logger, notifier, message)


def log_take_profit(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    stage: str,
    price: float,
    timestamp: datetime,
    new_stop: float | None = None,
) -> None:
    if new_stop is not None:
        message = (
            f"{symbol}: {stage} исполнен по {price:.4f} в {timestamp:%H:%M:%S}, новый стоп {new_stop:.4f}"
        )
    else:
        message = f"{symbol}: {stage} исполнен по {price:.4f} в {timestamp:%H:%M:%S}"
    _emit(logging.INFO, logger, notifier, message)


def log_stop_loss(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    price: float,
    timestamp: datetime,
) -> None:
    message = f"{symbol}: стоп исполнен по {price:.4f} в {timestamp:%H:%M:%S}"
    _emit(logging.INFO, logger, notifier, message)


def log_scenario_cancelled(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    reason: str | None,
) -> None:
    detail = f": {reason}" if reason else ""
    message = f"{symbol}: сценарий отменён{detail}"
    _emit(logging.INFO, logger, notifier, message)


def log_cooldown_started(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    until: datetime,
) -> None:
    message = f"{symbol}: установлен кулдаун до {until:%H:%M:%S}"
    _emit(logging.WARNING, logger, notifier, message)


def log_cooldown_active(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    until: datetime,
) -> None:
    message = f"{symbol}: пропуск сигнала — кулдаун до {until:%H:%M:%S}"
    _emit(logging.INFO, logger, notifier, message)


def log_order_error(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
    error: Exception,
) -> None:
    message = f"{symbol}: ошибка ордера — {error}"
    _emit(logging.ERROR, logger, notifier, message)


def log_entry_canceled(
    *,
    logger: logging.Logger,
    notifier: Notifier | None,
    symbol: str,
) -> None:
    message = f"{symbol}: вход отменён биржей"
    _emit(logging.INFO, logger, notifier, message)


__all__ = [
    "BreakoutContext",
    "Notifier",
    "OrderPlacementContext",
    "get_logger",
    "log_breakout",
    "log_cooldown_active",
    "log_cooldown_started",
    "log_entry_canceled",
    "log_level_identified",
    "log_order_error",
    "log_orders_placed",
    "log_pump_detected",
    "log_pullback_status",
    "log_scenario_cancelled",
    "log_stop_loss",
    "log_take_profit",
    "setup_logging",
]
