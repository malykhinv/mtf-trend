"""Command scheduling utilities for websocket stream workers."""
from __future__ import annotations

import asyncio
import heapq
import math
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from .base import ExchangeLogger


@dataclass(slots=True, frozen=True)
class CommandRetryPolicy:
    """Retry configuration for a websocket command."""

    max_attempts: int = 3
    delay_seconds: float = 0.5
    backoff_multiplier: float = 2.0


@dataclass(slots=True)
class ScheduledCommand:
    """Represents an outbound websocket command with retry metadata."""

    method: str
    params: tuple[Any, ...]
    timeout: float
    retry_policy: CommandRetryPolicy
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: time.monotonic())
    attempt: int = 0
    request_id: Optional[int] = None
    last_sent: float = 0.0
    deadline: float = 0.0
    not_before: float = 0.0

    def clone_for_retry(self, *, not_before: float) -> "ScheduledCommand":
        copy = ScheduledCommand(
            method=self.method,
            params=self.params,
            timeout=self.timeout,
            retry_policy=self.retry_policy,
            metadata=self.metadata,
        )
        copy.created_at = self.created_at
        copy.attempt = self.attempt
        copy.not_before = not_before
        return copy


@dataclass(slots=True)
class CommandResult:
    """Outcome of a scheduled command."""

    command: ScheduledCommand
    success: bool
    latency: float
    attempts: int
    payload: Optional[Mapping[str, Any]] = None
    error_details: Optional[str] = None


class StreamCommandScheduler:
    """Coordinates websocket command dispatch and retry handling."""

    def __init__(
        self,
        *,
        logger: ExchangeLogger,
        metrics_sink: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
    ) -> None:
        self._logger = logger
        self._metrics_sink = metrics_sink
        self._pending: List[tuple[float, int, ScheduledCommand]] = []
        self._outstanding: Dict[int, ScheduledCommand] = {}
        self._counter = count()
        self._next_request_id = 1

    def reset(self) -> None:
        """Clears all pending and outstanding commands."""

        self._pending.clear()
        self._outstanding.clear()
        self._next_request_id = 1

    def schedule(
        self,
        method: str,
        params: Iterable[Any],
        *,
        timeout: float,
        retry_policy: CommandRetryPolicy,
        metadata: Optional[Mapping[str, Any]] = None,
        delay: float = 0.0,
    ) -> ScheduledCommand:
        """Enqueue a command for dispatch."""

        normalized_params = tuple(params)
        command = ScheduledCommand(
            method=method,
            params=normalized_params,
            timeout=max(float(timeout), 0.1),
            retry_policy=retry_policy,
            metadata=dict(metadata or {}),
        )
        loop_now = self._loop_time()
        command.not_before = loop_now + max(0.0, float(delay))
        heapq.heappush(self._pending, (command.not_before, next(self._counter), command))
        return command

    async def dispatch(
        self,
        *,
        throttle: Callable[[], Awaitable[None]],
        send: Callable[[Mapping[str, Any]], Awaitable[None]],
        limit: Optional[int] = None,
        after_send: Optional[Callable[[ScheduledCommand], None]] = None,
    ) -> None:
        """Send pending commands that are ready for dispatch."""

        dispatched = 0
        while self._pending:
            if limit is not None and dispatched >= limit:
                break
            not_before, _, command = self._pending[0]
            now = self._loop_time()
            if not_before > now:
                break
            heapq.heappop(self._pending)
            await throttle()
            request_id = self._next_request_id
            self._next_request_id += 1
            payload = {"method": command.method, "params": list(command.params), "id": request_id}
            try:
                await send(payload)
            except Exception as exc:  # pragma: no cover - network safety
                self._logger.log(
                    (
                        "Команда {method} не отправлена (id={id}): {error}"  # noqa: ISC003
                    ).format(method=command.method, id=request_id, error=exc)
                )
                command.request_id = None
                command.attempt += 1
                retry_at = self._loop_time() + self._retry_delay(command)
                clone = command.clone_for_retry(not_before=retry_at)
                heapq.heappush(self._pending, (clone.not_before, next(self._counter), clone))
                continue
            command.request_id = request_id
            command.attempt += 1
            command.last_sent = now
            command.deadline = now + command.timeout
            self._outstanding[request_id] = command
            dispatched += 1
            if after_send is not None:
                try:
                    after_send(command)
                except Exception:
                    pass

    def expire(self) -> List[CommandResult]:
        """Requeue timed out commands and return permanent failures."""

        now = self._loop_time()
        expired: List[CommandResult] = []
        for request_id, command in list(self._outstanding.items()):
            if command.deadline > now:
                continue
            self._outstanding.pop(request_id, None)
            if command.attempt >= command.retry_policy.max_attempts:
                result = CommandResult(
                    command=command,
                    success=False,
                    latency=max(0.0, now - command.last_sent),
                    attempts=command.attempt,
                    payload=None,
                    error_details="timeout",
                )
                expired.append(result)
                self._publish_metrics(result)
                self._logger.log(
                    (
                        "Команда {method} не получила подтверждение после {attempts} попыток"  # noqa: ISC003
                    ).format(method=command.method, attempts=command.attempt)
                )
            else:
                retry_at = now + self._retry_delay(command)
                clone = command.clone_for_retry(not_before=retry_at)
                clone.attempt += 0  # preserve attempt count for logging
                heapq.heappush(self._pending, (clone.not_before, next(self._counter), clone))
                self._logger.log(
                    (
                        "Повторная отправка команды {method} (попытка {attempt}) через {delay:.2f}с"  # noqa: ISC003
                    ).format(
                        method=command.method,
                        attempt=command.attempt + 1,
                        delay=max(0.0, retry_at - now),
                    )
                )
        return expired

    def handle_response(self, payload: Mapping[str, Any]) -> Optional[CommandResult]:
        """Process a websocket response and return the command result if recognised."""

        raw_id = payload.get("id")
        if raw_id is None:
            return None
        try:
            request_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        command = self._outstanding.pop(request_id, None)
        if command is None:
            return None
        now = self._loop_time()
        latency = max(0.0, now - command.last_sent)
        error_details = self._extract_error(payload)
        success = error_details is None
        result = CommandResult(
            command=command,
            success=success,
            latency=latency,
            attempts=command.attempt,
            payload=payload,
            error_details=error_details,
        )
        self._publish_metrics(result)
        if success:
            self._logger.log(
                (
                    "Команда {method} подтверждена за {latency:.3f}с (попыток {attempts})"
                ).format(method=command.method, latency=latency, attempts=command.attempt)
            )
        else:
            self._logger.log(
                (
                    "Команда {method} завершилась ошибкой: {details} (попыток {attempts})"  # noqa: ISC003
                ).format(
                    method=command.method,
                    details=error_details,
                    attempts=command.attempt,
                )
            )
        return result

    def cancel(self, predicate: Callable[[ScheduledCommand], bool]) -> List[ScheduledCommand]:
        """Cancel pending and outstanding commands that match *predicate*."""

        cancelled: List[ScheduledCommand] = []
        new_pending: List[tuple[float, int, ScheduledCommand]] = []
        while self._pending:
            entry = heapq.heappop(self._pending)
            _, _, command = entry
            if predicate(command):
                cancelled.append(command)
            else:
                new_pending.append(entry)
        for entry in new_pending:
            heapq.heappush(self._pending, entry)
        for request_id, command in list(self._outstanding.items()):
            if predicate(command):
                cancelled.append(command)
                self._outstanding.pop(request_id, None)
        return cancelled

    def has_activity(self) -> bool:
        return bool(self._pending or self._outstanding)

    def _retry_delay(self, command: ScheduledCommand) -> float:
        policy = command.retry_policy
        base = max(policy.delay_seconds, 0.05)
        exponent = max(command.attempt - 1, 0)
        return base * math.pow(max(policy.backoff_multiplier, 1.0), exponent)

    def _loop_time(self) -> float:
        try:
            loop = asyncio.get_running_loop()
            return loop.time()
        except RuntimeError:
            return time.monotonic()

    def _publish_metrics(self, result: CommandResult) -> None:
        if self._metrics_sink is None:
            return
        tags = dict(result.command.metadata)
        tags.setdefault("command", result.command.method)
        tags["success"] = "1" if result.success else "0"
        payload = {
            "latency": result.latency,
            "attempts": result.attempts,
        }
        if result.error_details:
            payload["error"] = result.error_details
        try:
            self._metrics_sink("stream_command", {"values": payload, "tags": tags})
        except Exception:
            pass

    def _extract_error(self, payload: Mapping[str, Any]) -> Optional[str]:
        result = payload.get("result")
        if result is None or result is True:
            error_section = payload.get("error")
            if not error_section:
                return None
        parts: List[str] = []
        error_section = payload.get("error")
        if isinstance(error_section, Mapping):
            code = error_section.get("code")
            if code is not None:
                parts.append(f"code={code}")
            message = error_section.get("msg") or error_section.get("message")
            if message:
                parts.append(str(message))
        if not parts:
            for key in ("code", "msg", "message", "error"):
                value = payload.get(key)
                if value is not None:
                    parts.append(f"{key}={value}")
        if not parts:
            return None
        return ", ".join(parts)


__all__ = [
    "CommandResult",
    "CommandRetryPolicy",
    "ScheduledCommand",
    "StreamCommandScheduler",
]
