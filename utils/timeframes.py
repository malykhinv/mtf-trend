from __future__ import annotations

from datetime import timedelta

from domain.timeframe import Timeframe


def to_timedelta(tf: Timeframe) -> timedelta:
    minutes = tf.minutes
    return timedelta(minutes=minutes)

