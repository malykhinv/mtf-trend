from dataclasses import dataclass


@dataclass(frozen=True)
class PpoConfig:
    # region Сетапы.
    CAPTURE_TIMEOUT_MULTIPLIER: int = 12
    # endregion

    # region Повышенный объем.
    # Минимальная длина окна для нахождения зоны повышенного объема.
    VOLUME_TRIM_SIDE_BARS_MIN: int = 10
    # Порог изменения объема для нахождения зоны повышенного объема.
    HIGH_VOLUME_THRESHOLD: int = 5
    # Порог свечей с повышенным объемом в зоне повышенного объема.
    HIGH_VOLUME_FRACTION_MIN: float = 0.5
    # endregion

    # region Движение цены.
    # Максимальная доля теней относительно общего диапазона свечей.
    SHADOW_RANGE_PCT_MAX: float = 70.0
    # Максимальная доля свечей в предпиковом окне, находящихся выше цен отката.
    PRE_LOW_ABOVE_CORRECTION_LOW_FRACTION_MAX: float = 0.05
    # Максимальное соотношение длительности роста к длительности коррекции.
    RISE_AGE_LIMIT_MULTIPLIER: float = 2.5
    # Рост цены на основных свингах в зоне повышенного объема.
    PRICE_RISE_PCT_MIN: float = 15.0
    # Максимально допустимый откат на участке роста (относительно размера роста)
    MAX_RETRACE_RATIO: float = 0.7
    # Соотношение глубины отката к росту.
    RETRACE_RATIO_MAX: float = 0.75
    # Минимальный уровень отката (снизу-вверх), на котором может располагаться каскад.
    CASCADE_RETRACE_RATIO_MIN: float = 0.4
    # Максимальный разброс цены в каскаде относительно размера отката.
    CASCADE_RANGE_RATIO_MAX: float = 0.2
    # Размер окна ATR для анализа лонгового каскада.
    CASCADE_ATR_WINDOW: int = 50
    # Допуск по касаниям в долях ATR.
    CASCADE_TOUCH_EPS_NATR: float = 1.0
    # Минимальная глубина отката в долях ATR.
    CASCADE_MIN_PULLBACK_NATR: float = 2.5
    # Минимальная длительность отката в барах.
    CASCADE_MIN_PULLBACK_BARS: int = 3
    # Минимальный разрыв между касаниями в барах.
    CASCADE_MIN_GAP_BARS: int = 5
    # Минимальное число свингов для образования каскада.
    CASCADE_LENGTH_MIN: int = 3
    # Минимальный разрыв от верха каскада до начала сопротивления (в долях отката).
    RESISTANCE_GAP_RATIO_MIN: float = 0.1
    # Максимальное количество свингов после каскада.
    RESISTANCE_COUNT_MAX: int = 2
    # Минимальное соотношение отступа поддержки от низа проторговки к размеру проторговки.
    SUPPORT_CONSOLIDATION_RATIO_MIN: float = 0.3
    # endregion

    # region Позиции.
    # Минимальное соотношение Entry-TP к Entry-SL.
    REWARD_RISK_RATIO_MIN: float = 1 / 1
    # Минимальное расстояние Entry-TP.
    PROFIT_PCT_MIN: float = 3
    # Минимальное расстояние Entry-SL.
    LOSS_PCT_MIN: float = 0.3
    # Минимальное расстояние от Entry и TP до PC при частичном закрытии позиции.
    PARTIAL_CLOSE_SIDE_PCT_MIN: float = 2.0
    # Соотношение цены участков Entry-BE и Entry-PC при частичном закрытии позиции.
    BREAKEVEN_PARTIAL_CLOSE_RATIO: float = 0.5
    # Размер риска на сделку в USDT.
    RISK_PER_TRADE_USDT: float = 20.0
    # Минимальная нотация позиции в USDT.
    MIN_POSITION_NOTIONAL_USDT: float = 10.0
    # Минимальное количество монет в позиции.
    MIN_POSITION_QUANTITY: float = 0.001
    # endregion

    # region Тест
    TEST_SLIPPAGE_PCT: float = 0.3
    # endregion


ppo_cfg = PpoConfig()
