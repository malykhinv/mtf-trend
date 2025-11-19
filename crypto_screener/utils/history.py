from crypto_screener.domain.models.timeframe import Timeframe


_REFERENCE_TIMEFRAME_MINUTES = Timeframe.M5.minutes
_MIN_LIMIT = 100
_MIN_WINDOW = 40


def _scale_by_timeframe(base_value: int, timeframe: Timeframe, minimum: int) -> int:
    coverage_minutes = base_value * _REFERENCE_TIMEFRAME_MINUTES
    scaled_value = max(minimum, coverage_minutes // timeframe.minutes)

    if timeframe.minutes < _REFERENCE_TIMEFRAME_MINUTES:
        return min(base_value, scaled_value)

    return scaled_value


def calculate_limit(base_limit: int, timeframe: Timeframe) -> int:
    """Подбирает limit на основе таймфрейма так, чтобы сохранять одинаковую глубину истории."""

    return _scale_by_timeframe(base_limit, timeframe, _MIN_LIMIT)


def calculate_window(base_window: int, timeframe: Timeframe, limit: int) -> int:
    """Подбирает window на основе таймфрейма, не позволяя окну превышать limit."""

    window = _scale_by_timeframe(base_window, timeframe, _MIN_WINDOW)
    return min(window, limit)
