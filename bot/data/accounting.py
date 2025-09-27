"""Account and balance related interfaces."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from bot import config
from bot.utils.logging import get_logger


class BalanceProvider(Protocol):
    """Abstract interface for retrieving account deposit information."""

    def current_deposit(self) -> float:  # pragma: no cover - interface definition
        ...


class CcxtBalanceProvider(BalanceProvider):
    """Balance provider that retrieves deposit information via a ccxt client."""

    def __init__(self, client: Any, *, currency: str = "USDT", logger=None) -> None:
        self._client = client
        self._currency = currency
        self._logger = logger or get_logger(__name__)
        self._last_deposit: float | None = None
        self._last_updated: datetime | None = None

    def current_deposit(self) -> float:
        now = datetime.now(tz=config.TIMEZONE)
        if self._should_refresh(now):
            try:
                deposit = self._fetch_deposit()
            except Exception:
                self._logger.exception("Не удалось обновить баланс для %s", self._currency)
                if self._last_deposit is None:
                    raise RuntimeError(
                        "Баланс недоступен и отсутствует предыдущее значение депозита"
                    ) from None
                self._logger.warning(
                    "Используем последнее известное значение депозита %.2f %s",
                    self._last_deposit,
                    self._currency,
                )
            else:
                self._last_deposit = deposit
                self._last_updated = now

        if self._last_deposit is None:
            raise RuntimeError("Баланс недоступен и отсутствует предыдущее значение депозита")

        return self._last_deposit

    def _should_refresh(self, now: datetime) -> bool:
        if self._last_updated is None:
            return True
        refresh_delta = timedelta(minutes=config.DEPOSIT_REFRESH_MIN)
        return now - self._last_updated >= refresh_delta

    def _fetch_deposit(self) -> float:
        balance = self._client.fetch_balance()  # type: ignore[attr-defined]
        totals = balance.get("total") if isinstance(balance, dict) else None
        if not isinstance(totals, dict):
            raise ValueError("Некорректный формат баланса: отсутствует секция total")

        raw_value = totals.get(self._currency)
        if raw_value is None:
            raise ValueError(f"В балансе отсутствует валюта {self._currency}")

        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive conversion
            raise ValueError(f"Некорректное значение баланса {raw_value!r}") from exc
