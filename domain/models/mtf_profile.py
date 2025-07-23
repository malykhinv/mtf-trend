from dataclasses import dataclass

from domain.models.timeframe import Timeframe


@dataclass
class MTFProfile:
    """
    Описывает группы таймфреймов для multi-timeframe анализа:
        macro, context, setup, entry.
    """
    macro: Timeframe
    context: Timeframe
    setup: Timeframe
    entry: Timeframe

    def __iter__(self):
        """
        Позволяет итерироваться по всем таймфреймам в порядке:
        macro → setup → entry.
        """
        return iter((self.macro, self.context, self.setup, self.entry))

    def __str__(self):
        return f"{self.macro.value}-{self.context.value}-{self.setup.value}-{self.entry.value}"