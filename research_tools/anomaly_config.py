"""Configuration primitives owned by anomaly research/live code."""

from __future__ import annotations

from domain.enums.timeframe import Timeframe

AnomalyTimeframePair = tuple[Timeframe, Timeframe]

ANOMALY_BACKTEST_TIMEFRAME_PAIRS: tuple[AnomalyTimeframePair, ...] = (
    (Timeframe.M5, Timeframe.S30),
    (Timeframe.M5, Timeframe.S15),
    (Timeframe.M1, Timeframe.S5),
)
ANOMALY_LIVE_TIMEFRAME_PAIRS: tuple[AnomalyTimeframePair, ...] = ANOMALY_BACKTEST_TIMEFRAME_PAIRS
ANOMALY_SUPPORTED_TIMEFRAME_PAIRS: tuple[AnomalyTimeframePair, ...] = tuple(
    dict.fromkeys((*ANOMALY_BACKTEST_TIMEFRAME_PAIRS, *ANOMALY_LIVE_TIMEFRAME_PAIRS))
)
ANOMALY_DEFAULT_BACKTEST_TIMEFRAME_PAIR: AnomalyTimeframePair = ANOMALY_BACKTEST_TIMEFRAME_PAIRS[0]
ANOMALY_DEFAULT_LIVE_TIMEFRAME_PAIR: AnomalyTimeframePair = ANOMALY_LIVE_TIMEFRAME_PAIRS[0]


def format_anomaly_timeframe_pairs(timeframe_pairs: tuple[AnomalyTimeframePair, ...]) -> str:
    return ", ".join(f"{context.value}-{execution.value}" for context, execution in timeframe_pairs)


def resolve_anomaly_default_timeframe_pair(*, mode: str = "backtest") -> AnomalyTimeframePair:
    if mode == "backtest":
        return ANOMALY_DEFAULT_BACKTEST_TIMEFRAME_PAIR
    if mode == "live":
        return ANOMALY_DEFAULT_LIVE_TIMEFRAME_PAIR
    raise ValueError(f"unsupported anomaly timeframe mode: {mode}")


def validate_anomaly_timeframe_pair(
    *,
    context_timeframe: Timeframe,
    execution_timeframe: Timeframe,
    supported_pairs: tuple[AnomalyTimeframePair, ...] = ANOMALY_SUPPORTED_TIMEFRAME_PAIRS,
    context: str = "anomaly",
) -> None:
    timeframe_pair = (context_timeframe, execution_timeframe)
    if timeframe_pair in supported_pairs:
        return
    supported = format_anomaly_timeframe_pairs(supported_pairs)
    raise ValueError(
        f"{context} supports only timeframe pairs {{{supported}}}, "
        f"got {context_timeframe.value}-{execution_timeframe.value}"
    )
