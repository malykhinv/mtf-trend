# region Общие параметры торговли
from domain.models.timeframe import Timeframe

MTF_PROFILE_GLOBAL = [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15]
MTF_PROFILE_INTRADAY = [Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]

# Минимальный объем сделки в USDT для фильтрации ликвидности
VOLUME_THRESHOLD_USDT = 50_000_000

# Минимальное требуемое соотношение риск/прибыль
MIN_RR = 3

# RR для контроля прогресса сделки (ближайшая цель)
PROGRESS_RR_NEAR = 1

# RR для контроля дальней цели
PROGRESS_RR_FAR = 3

# Минимальный процентный размер стоп-лосса
MIN_SL_PCT = 0.003

# Минимальный процентный размер тейк-профита
MIN_TP_PCT = 0.010

# Включение или выключение торговли
IS_TRADING_ENABLED = True

# Размер позиции в USDT
POSITION_USDT = 10

# Максимальное количество сделок в час
MAX_TRADES_PER_HOUR = 3

# Минимальный интервал между сделками по одному инструменту (в минутах)
MIN_COOLDOWN_PER_SYMBOL_MINUTES = 360  # 6 часов

# endregion

# region Настройки свингов и диапазонов

# Множитель ATR для определения близости к свингу
SWING_PROXIMITY_ATR_MULTIPLIER = 1.2

# Максимальный сдвиг центра диапазона в ATR
FLAT_MAX_CENTER_SHIFT_ATR = 1.0

# Допустимая дистанция для условия касания уровня (ATR)
TOUCH_DISTANCE_ATR = 1.5

# endregion

# region Настройки свечей и объемов

# Множитель объема для сильной реакции
STRONG_REACTION_VOLUME_MULTIPLIER = 1.2

# Максимальное отношение хвоста свечи к общему диапазону для умеренной реакции
MODERATE_REACTION_WICK_RATIO = 0.5

# Максимальное отношение хвоста свечи к общему диапазону для сильной реакции
STRONG_REACTION_WICK_RATIO = 0.3

# Период для расчета среднего объема свечей (кол-во баров)
CANDLE_AVG_VOLUME_PERIOD = 20

# endregion

# region Настройки TP и SL

# Количество баров для поиска тейк-профита вперед
TP_LOOKAHEAD_BARS = 20

# Количество баров для поиска стоп-лосса назад
SL_LOOKBACK_BARS = 15

# endregion

# region Настройки ретеста

# Допустимое отклонение ретеста уровня в ATR
RETEST_TOLERANCE_ATR = 0.75

# endregion
