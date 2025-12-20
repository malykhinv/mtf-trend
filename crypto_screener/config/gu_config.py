from dataclasses import dataclass
from enum import Enum


class CascadeGroupingMode(str, Enum):
    CALENDAR = "calendar"
    ROLLING_WINDOW = "rolling_window"


@dataclass(frozen=True)
class GuConfig:
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
    # Размер окна ATR для анализа лонгового каскада.
    CASCADE_ATR_WINDOW: int = 50
    # Допуск по касаниям в долях ATR.
    CASCADE_TOUCH_EPS_NATR: float = 6.0
    # Максимальное перебитие уровня в долях ATR.
    CASCADE_OVERTOUCH_NATR: float = 0.5
    # Минимальное число свингов для образования каскада.
    CASCADE_LENGTH_MIN: int = 3
    # Минимальная доля отката после второго касания относительно первого.
    CASCADE_PULLBACK_SECOND_RATIO_MIN: float = 0.5
    # Максимальная доля отката после второго касания относительно первого.
    CASCADE_PULLBACK_SECOND_RATIO_MAX: float = 1.0
    # Минимальная доля отката после третьего и далее относительно второго.
    CASCADE_PULLBACK_NEXT_RATIO_MIN: float = 0.1
    # Максимальная доля отката после третьего и далее относительно второго.
    CASCADE_PULLBACK_NEXT_RATIO_MAX: float = 0.9
    # Минимальная глубина первых откатов в долях ATR.
    CASCADE_MIN_PULLBACK_NATR: float = 2.5
    # Минимальное соотношение отступа поддержки от низа проторговки к размеру проторговки.
    SUPPORT_CONSOLIDATION_RATIO_MIN: float = 0.3
    # Минимальная длительность окна группировки каскадов.
    CASCADE_ROLLING_WINDOW_MIN_HOURS: int = 12
    # Максимальная длительность окна группировки каскадов.
    CASCADE_ROLLING_WINDOW_MAX_HOURS: int = 36
    # endregion

    # region Позиции.
    # Минимальное соотношение Entry-TP к Entry-SL.
    REWARD_RISK_RATIO_MIN: float = 1 / 1
    # Минимальное расстояние Entry-TP.
    PROFIT_PCT_MIN: float = 3
    # Минимальное расстояние Entry-SL.
    LOSS_PCT_MIN: float = 0.3
    # Множитель ширины каскада для TP в top-контексте или при повышенном объеме.
    TP_MULTIPLIER_TOP_HIGH_VOLUME: float = 2.0
    # Множитель ширины каскада для TP в остальных случаях.
    TP_MULTIPLIER_DEFAULT: float = 2 / 3
    # Минимальное расстояние от Entry и TP до PC при частичном закрытии позиции.
    PARTIAL_CLOSE_SIDE_PCT_MIN: float = 2.0
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


gu_cfg = GuConfig()
