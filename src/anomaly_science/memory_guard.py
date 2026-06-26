from __future__ import annotations

import os
import sys


class MemoryBudgetError(RuntimeError):
    """Raised when free RAM drops into the danger zone during a heavy build.

    Failing fast here is deliberate: an unbounded in-memory build (e.g. holding the
    whole candle universe) would otherwise push the machine into hours of swap
    thrash before the OS kills it. A clear early error lets the caller pick a
    smaller window / anchor-only mode instead of silently hanging.
    """


def _memory_status_windows() -> tuple[int, int, int] | None:
    """Return (total_bytes, avail_bytes, load_percent) or None."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return int(status.ullTotalPhys), int(status.ullAvailPhys), int(status.dwMemoryLoad)


def _memory_status_posix() -> tuple[int, int, int] | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        avail_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None
    if page_size <= 0 or total_pages <= 0:
        return None
    total = int(page_size) * int(total_pages)
    avail = int(page_size) * int(avail_pages) if avail_pages and avail_pages > 0 else 0
    load = int(round(100.0 * (total - avail) / total)) if total else 0
    return total, avail, load


def memory_status() -> tuple[int, int, int] | None:
    """(total_bytes, available_bytes, load_percent), or None if unavailable."""
    return _memory_status_windows() if os.name == "nt" else _memory_status_posix()


def total_ram_bytes() -> int | None:
    status = memory_status()
    return status[0] if status else None


def check_memory_budget(
    *,
    label: str,
    max_load_percent: int = 90,
    min_available_bytes: int = 1_500_000_000,
) -> None:
    """Raise MemoryBudgetError when free RAM enters the swap-thrash danger zone.

    Triggers if the system memory load is at/above ``max_load_percent`` or free
    physical memory falls at/below ``min_available_bytes``. No-op when memory
    cannot be measured (never block on a missing metric).
    """
    status = memory_status()
    if status is None:
        return
    total, available, load = status
    if load >= max_load_percent or available <= min_available_bytes:
        raise MemoryBudgetError(
            f"{label}: only {available / 1_000_000_000:.1f} GB free of "
            f"{total / 1_000_000_000:.1f} GB ({load}% used) - aborting before swap thrash. "
            "Reduce --days, reduce --state-window-minutes, or use --supervised-anchor-only; "
            "the per-minute cross-section build holds the full candle universe in memory."
        )


def process_rss_bytes() -> int | None:
    if os.name == "nt":
        return _process_rss_bytes_windows()
    try:
        import resource
    except ImportError:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (OSError, ValueError):
        return None
    rss = int(usage.ru_maxrss)
    if rss <= 0:
        return None
    return rss if sys.platform == "darwin" else rss * 1024


def _process_rss_bytes_windows() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    for lib_name in ("psapi", "kernel32"):
        try:
            library = getattr(ctypes.windll, lib_name)
            getter = getattr(library, "GetProcessMemoryInfo", None) or getattr(library, "K32GetProcessMemoryInfo", None)
            if getter is None:
                continue
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if getter(handle, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
        except (AttributeError, OSError):
            continue
    return None
