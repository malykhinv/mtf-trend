from __future__ import annotations

# One bounded CPU runtime source for local research runs.
# Target machine: i5 13th gen, 16GB RAM. Four CatBoost workers keep the box
# usable while avoiding fake speedups that simply move the bottleneck to RAM.
DEFAULT_BOUNDED_CPU_THREAD_COUNT = 4
MAX_BOUNDED_CPU_THREAD_COUNT = 8


def validate_bounded_thread_count(value: int, *, field_name: str = "thread_count") -> None:
    """Reject unsafe or ambiguous CPU thread counts."""
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 1 or value > MAX_BOUNDED_CPU_THREAD_COUNT:
        raise ValueError(f"{field_name} must be within [1, {MAX_BOUNDED_CPU_THREAD_COUNT}]")


def bounded_cpu_thread_count() -> int:
    """Return the single project default for bounded CPU ML stages."""
    validate_bounded_thread_count(
        DEFAULT_BOUNDED_CPU_THREAD_COUNT,
        field_name="DEFAULT_BOUNDED_CPU_THREAD_COUNT",
    )
    return DEFAULT_BOUNDED_CPU_THREAD_COUNT
