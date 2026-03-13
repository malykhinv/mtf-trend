"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfitableVariant:
    rank: int
    bite_profile_id: str
    bite_grid_mode: str
    bite_lookback: int
    bite_volume_mult: float
    bite_retest_window_hours: int
    bite_min_rr: float
    bite_tp2_mult: float
    bite_confirmation_bars: int
    bite_reclaim_mode: str
    bite_retest_mode: str
    profit_factor: float
    pnl_percent: float
    win_rate: float
    trades_count: int
    max_dd: float
    sl_count: int
    be_count: int
    tp1_be_count: int
    tp2_count: int
