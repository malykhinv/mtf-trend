"""Конфиг и параметры стратегии bee_bite."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.entry_trigger import EntryTrigger
from domain.enums.sl_mode import SLMode
from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class BeeBiteParams:
    bite_lookback: int
    bite_volume_mult: float
    bite_retest_window_hours: int
    bite_retest_zone: float
    bite_min_rr: float
    bite_sl_mode: SLMode
    bite_tp2_mult: float
    bite_min_body_ratio: float
    bite_min_move_atr: float
    bite_max_retest_depth: float
    bite_confirmation_bars: int
    bite_entry_trigger: EntryTrigger
    symbol: str
    bite_retest_zone_atr: float | None = None
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
