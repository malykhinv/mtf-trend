from __future__ import annotations

import pytest

from anomaly_science.runtime import (
    DEFAULT_BOUNDED_CPU_THREAD_COUNT,
    MAX_BOUNDED_CPU_THREAD_COUNT,
    bounded_cpu_thread_count,
    validate_bounded_thread_count,
)


def test_runtime_thread_count_is_single_bounded_default() -> None:
    assert bounded_cpu_thread_count() == DEFAULT_BOUNDED_CPU_THREAD_COUNT
    assert 1 <= DEFAULT_BOUNDED_CPU_THREAD_COUNT <= MAX_BOUNDED_CPU_THREAD_COUNT


@pytest.mark.parametrize("value", [0, MAX_BOUNDED_CPU_THREAD_COUNT + 1])
def test_runtime_thread_count_rejects_unsafe_values(value: int) -> None:
    with pytest.raises(ValueError, match="catboost_thread_count"):
        validate_bounded_thread_count(value, field_name="catboost_thread_count")
