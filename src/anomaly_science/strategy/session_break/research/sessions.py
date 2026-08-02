"""Session tiling for the previous-session-high breakout study.

Crypto trades 24/7 but liquidity tracks where the human/desk participants are
awake. We tile the UTC day into five *non-overlapping* blocks so that (a) the
classical macro sessions are present, (b) the EU-US overlap -- the single most
liquid window and the one the hypothesis cares about -- is its own regime, and
(c) the blocks form a clean temporal sequence so "the immediately preceding
session" is unambiguous.

Boundaries (UTC hour):
    ASIA     00:00-08:00   thin books; where low-caps get manufactured
    EU       08:00-13:00   liquidity ramps, real directional intent
    OVERLAP  13:00-16:00   EU-US overlap: deepest, most volatile
    US       16:00-21:00   US flow / macro prints
    LATE     21:00-24:00   post-US dead zone, low liquidity

Every block instance is keyed by ``(utc_day, seq)`` where ``seq`` is the index
into ``BLOCKS``. The sequence wraps across days: ASIA's predecessor is the
previous day's LATE.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class Block:
    seq: int
    name: str
    start_hour: int
    end_hour: int  # exclusive

    @property
    def is_overlap(self) -> bool:
        return self.name == "OVERLAP"


BLOCKS: tuple[Block, ...] = (
    Block(0, "ASIA", 0, 8),
    Block(1, "EU", 8, 13),
    Block(2, "OVERLAP", 13, 16),
    Block(3, "US", 16, 21),
    Block(4, "LATE", 21, 24),
)
N_BLOCKS = len(BLOCKS)
BLOCK_BY_SEQ = {b.seq: b for b in BLOCKS}


def block_seq_for_ms(ts_ms: np.ndarray) -> np.ndarray:
    """Vectorised block index for an array of epoch-ms timestamps."""

    hour = (ts_ms % MS_PER_DAY) // MS_PER_HOUR
    seq = np.empty(len(ts_ms), dtype=np.int64)
    seq[(hour >= 0) & (hour < 8)] = 0
    seq[(hour >= 8) & (hour < 13)] = 1
    seq[(hour >= 13) & (hour < 16)] = 2
    seq[(hour >= 16) & (hour < 21)] = 3
    seq[(hour >= 21) & (hour < 24)] = 4
    return seq


def utc_day_for_ms(ts_ms: np.ndarray) -> np.ndarray:
    return ts_ms // MS_PER_DAY


def predecessor_sequential(day: int, seq: int) -> tuple[int, int]:
    """Block immediately preceding ``(day, seq)`` in wall-clock order.

    Variant A reference: e.g. the US block's predecessor is that day's OVERLAP;
    ASIA's predecessor is the previous day's LATE.
    """

    if seq == 0:
        return day - 1, N_BLOCKS - 1
    return day, seq - 1


def predecessor_same_type(day: int, seq: int) -> tuple[int, int]:
    """Same block type on the previous day.

    Variant B reference: today's US vs yesterday's US.
    """

    return day - 1, seq
