"""Session-break aliases for the shared versioned Core session calendar."""

from anomaly_science.market_context.sessions import (
    MS_PER_DAY,
    MS_PER_HOUR,
    UTC_SESSION_BLOCKS,
    UTC_SESSION_BY_SEQ,
    UTC_SESSION_COUNT,
    UtcSessionBlock,
    block_seq_for_ms,
    predecessor_same_type,
    predecessor_sequential,
    utc_day_for_ms,
)

Block = UtcSessionBlock
BLOCKS = UTC_SESSION_BLOCKS
BLOCK_BY_SEQ = UTC_SESSION_BY_SEQ
N_BLOCKS = UTC_SESSION_COUNT

__all__ = [
    "BLOCKS",
    "BLOCK_BY_SEQ",
    "Block",
    "MS_PER_DAY",
    "MS_PER_HOUR",
    "N_BLOCKS",
    "block_seq_for_ms",
    "predecessor_same_type",
    "predecessor_sequential",
    "utc_day_for_ms",
]
