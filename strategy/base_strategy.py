"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import pandas as pd

from domain.models.trade_result import TradeResult

if TYPE_CHECKING:
    from vectorbt_runner.mtf_frames import SymbolMtfFrames


StrategyParamsT = TypeVar("StrategyParamsT")


class BaseStrategy(ABC, Generic[StrategyParamsT]):
    """Класс."""
    @abstractmethod
    def validate_config(self, params: StrategyParamsT) -> None:
        """Метод."""
    @abstractmethod
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def generate_events(self, data: pd.DataFrame, params: StrategyParamsT) -> list[TradeResult]:
        """Метод."""
    @abstractmethod
    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: StrategyParamsT,
        **context: Any,
    ) -> list[TradeResult]:
        """Метод."""


    @staticmethod
    def generate_events_portfolio(
        *,
        symbol_frames: dict[str, SymbolMtfFrames],
        params: StrategyParamsT,
    ) -> list[TradeResult] | None:
        """Опциональный портфельный запуск, когда стратегия должна выбирать кандидатов между символами синхронно."""
        del symbol_frames, params
        return None

    @abstractmethod
    def build_parameter_grid(self) -> list[StrategyParamsT]:
        """Возвращает полный набор параметров стратегии для бэктеста."""

    @abstractmethod
    def params_to_row(self, params: StrategyParamsT) -> dict[str, int | float | str | None]:
        """Преобразует параметры стратегии в базовые колонки результирующей строки."""

    def prepare_symbol_context(
        self,
        *,
        symbol: str,
        mtf_frames: SymbolMtfFrames,
        params: StrategyParamsT,
    ) -> dict[str, Any] | None:
        """Опциональная подготовка symbol-specific контекста перед генерацией сделок."""
        return None
