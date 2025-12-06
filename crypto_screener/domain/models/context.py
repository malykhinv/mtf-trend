from enum import Enum


class Context(Enum):
    # region Листинг.
    # Листинг: символ был добавлен на биржу недавно.
    LISTING = "LISTING"
    # endregion

    # region Низкая капитализация.
    # Низкая капитализация A: объем торгов или количество сделок за 24ч превышает показатели BTC.
    LOW_CAP_A = "LOW_CAP_A"
    # Низкая капитализация B: объем торгов за 24ч превышает порог.
    LOW_CAP_B = "LOW_CAP_B"
    # Низкая капитализация C: количество сделок за 24ч превышает 50% от количества сделок BTC.
    LOW_CAP_C = "LOW_CAP_C"
    # Низкая капитализация D: количество сделок за 24ч превышает порог.
    LOW_CAP_D = "LOW_CAP_D"
    # endregion

    # region Средняя капитализация.
    # Средняя капитализация A: объем торгов или количество сделок за 24ч превышает показатели BTC.
    MIDDLE_CAP_A = "MIDDLE_CAP_A"
    # Средняя капитализация B: объем торгов за 24ч превышает порог.
    MIDDLE_CAP_B = "MIDDLE_CAP_B"
    # Средняя капитализация C: количество сделок за 24ч превышает 50% от количества сделок BTC.
    MIDDLE_CAP_C = "MIDDLE_CAP_C"
    # endregion

    # region Высокая капитализация.
    # Высокая капитализация A: объем торгов и количество сделок за 24ч превышает показатели BTC.
    HIGH_CAP_A = "HIGH_CAP_A"
    # endregion

    # Стандартные условия: все остальные случаи.
    FROZEN = "FROZEN"

    # Тест: используется для тестовых запусков.
    TEST = "TEST"

    @property
    def is_top(self) -> bool:
        return self in {
            Context.LOW_CAP_A,
            Context.LOW_CAP_B,
            Context.MIDDLE_CAP_A,
            Context.MIDDLE_CAP_B,
            Context.HIGH_CAP_A,
            Context.TEST
        }

    @property
    def is_test(self) -> bool:
        return self == Context.TEST
