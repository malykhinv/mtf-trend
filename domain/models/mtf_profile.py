from dataclasses import dataclass

from domain.models.timeframe import Timeframe


@dataclass
class MTFProfile:
    """
    Профиль мульти-таймфреймов для системного анализа и торговли.

    Атрибуты:
        macro (Timeframe):
            Глобальный контекст (например, D1). Определяет основную фазу рынка (тренд, флет, диапазон).
        trend (Timeframe):
            ТФ тренда (например, H4). Помогает уточнить направление движения внутри глобальной фазы.
        setup (Timeframe):
            ТФ коррекции или подготовки (например, H1). Анализирует структуру откатов и формирование сетапов.
        entry (Timeframe):
            ТФ для входа (например, M15). Используется для поиска точки входа по подтверждённой структуре.
        micro (Timeframe):
            Микро-ТФ (например, M5). Применяется для сопровождения позиции, управления стопами и частичным выходам.
    """
    macro: Timeframe
    trend: Timeframe
    setup: Timeframe
    entry: Timeframe
    micro: Timeframe

    def __iter__(self):
        """
        Позволяет итерироваться по всем таймфреймам в порядке:
        macro → trend → setup → entry → micro.
        """
        return iter((self.macro, self.trend, self.setup, self.entry, self.micro))