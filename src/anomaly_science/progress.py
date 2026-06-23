from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    done: int
    total: int | None = None
    unit: str = "items"
    detail: str = ""
    force: bool = False


ProgressCallback = Callable[[ProgressUpdate], None]
ProgressSink = Callable[[str], None]


@dataclass(slots=True)
class HumanProgressReporter:
    stage_name: str
    total: int | None = None
    unit: str = "items"
    min_interval_seconds: float = 30.0
    sink: ProgressSink = field(default=lambda message: print(message, file=sys.stderr, flush=True))
    _started_at: float = field(default_factory=perf_counter, init=False)
    _last_emit_at: float = field(default=0.0, init=False)
    _last_done: int = field(default=-1, init=False)

    def update(self, update: ProgressUpdate) -> None:
        done = max(0, int(update.done))
        total = update.total if update.total is not None else self.total
        if total is not None:
            total = max(0, int(total))
        unit = update.unit or self.unit
        now = perf_counter()
        is_done = total is not None and total > 0 and done >= total
        first_emit = self._last_emit_at <= 0.0
        should_emit = (
            update.force
            or first_emit
            or is_done
            or (now - self._last_emit_at) >= self.min_interval_seconds
        )
        if not should_emit or done == self._last_done and not update.force:
            return
        self._last_emit_at = now
        self._last_done = done
        self.sink(
            format_progress_message(
                stage_name=self.stage_name,
                done=done,
                total=total,
                unit=unit,
                elapsed_seconds=max(0.0, now - self._started_at),
                detail=update.detail,
            )
        )


def make_stderr_progress_callback(
    *,
    stage_name: str,
    total: int | None = None,
    unit: str = "items",
    min_interval_seconds: float = 30.0,
) -> ProgressCallback:
    reporter = HumanProgressReporter(
        stage_name=stage_name,
        total=total,
        unit=unit,
        min_interval_seconds=min_interval_seconds,
    )
    return reporter.update


def format_progress_message(
    *,
    stage_name: str,
    done: int,
    total: int | None,
    unit: str,
    elapsed_seconds: float,
    detail: str = "",
) -> str:
    elapsed = format_duration_seconds(elapsed_seconds)
    rate = done / elapsed_seconds if elapsed_seconds > 0 else 0.0
    detail_part = "" if not detail else f" detail={detail}"
    if total is not None and total > 0:
        pct = min(max(done / total * 100.0, 0.0), 100.0)
        remaining = max(total - done, 0)
        eta_seconds = remaining / rate if rate > 0 else None
        eta = "?" if eta_seconds is None else format_duration_seconds(eta_seconds)
        return (
            f"{stage_name} {pct:5.1f}% {done}/{total} {unit} "
            f"elapsed={elapsed} eta={eta} rate={_format_rate(rate, unit)}{detail_part}"
        )
    return f"{stage_name} {done} {unit} elapsed={elapsed} rate={_format_rate(rate, unit)}{detail_part}"


def format_duration_seconds(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _format_rate(rate: float, unit: str) -> str:
    if rate >= 100:
        value = f"{rate:.0f}"
    elif rate >= 10:
        value = f"{rate:.1f}"
    else:
        value = f"{rate:.2f}"
    return f"{value} {unit}/s"
