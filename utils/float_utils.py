from typing import Optional

from config.constants import FLOAT_UNDEFINED


def is_defined(*values: Optional[float], eps: float = 1e-5) -> bool:
    """
    Проверяет, что все переданные значения отличны от FLOAT_UNDEFINED с учётом погрешности eps.
    """
    return all(v is not None and abs(v - FLOAT_UNDEFINED) > eps for v in values)

def get_pct(value1: float, value2: float) -> float:
    if not is_defined(value1):
        return FLOAT_UNDEFINED

    return abs(value1 - value2) / value1 * 100

def precision(value: float) -> int:
    if not is_defined(value):
        return 0
    s = f"{value:.20f}".rstrip('0')
    if '.' in s:
        return len(s.split('.')[1])
    return 0
