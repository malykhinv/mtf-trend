from enum import Enum


class Context(Enum):
    # Контекст A: объем торгов или количество сделок за 24ч превышает показатели BTC
    A = "A"
    # Контекст B: объем торгов за 24ч превышает CONTEXT_B_VOLUME_MIN
    B = "B"
    # Контекст C: количество сделок за 24ч превышает 50% от количества сделок BTC
    C = "C"
    # Контекст D: символ был добавлен на биржу не более listing_period_days дней назад
    D = "D"
    # Контекст E: количество сделок за 24ч превышает CONTEXT_E_TRADES_MIN
    E = "E"
    # Контекст F: все остальные случаи (по умолчанию)
    F = "F"
    # Контекст TEST: используется для тестовых прогонов
    TEST = "TEST"

    @property
    def is_top(self) -> bool:
        return self in {Context.A, Context.B, Context.C, Context.TEST}