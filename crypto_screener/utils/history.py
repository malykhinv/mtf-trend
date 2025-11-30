from crypto_screener.domain.models.timeframe import Timeframe

# region Private.
_REFERENCE_TIMEFRAME_MINUTES = Timeframe.M15.minutes
_LIMIT_MIN = 300
_WINDOW_MIN = 100


def _scale_by_timeframe(base_value: int, timeframe: Timeframe, minimum: int) -> int:
    coverage_minutes = base_value * _REFERENCE_TIMEFRAME_MINUTES
    scaled_value = max(minimum, coverage_minutes // timeframe.minutes)
    if timeframe.minutes < _REFERENCE_TIMEFRAME_MINUTES:
        return min(base_value, scaled_value)
    return scaled_value


# endregion

def calculate_limit(
        base_limit: int,
        timeframe: Timeframe
) -> int:
    return _scale_by_timeframe(base_limit, timeframe, _LIMIT_MIN)


def calculate_limit_grid(
        base_limit: int,
        timeframe: Timeframe
) -> list[int]:
    variants = [base_limit * 3, base_limit * 2, base_limit]
    limits: list[int] = []

    for variant in variants:
        limit = calculate_limit(variant, timeframe)
        if limit not in limits:
            limits.append(limit)

    return limits


def calculate_window(
        base_window: int,
        timeframe: Timeframe,
        limit: int
) -> int:
    window = _scale_by_timeframe(base_window, timeframe, _WINDOW_MIN)
    return min(window, limit)
