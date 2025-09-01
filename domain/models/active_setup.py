from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from domain.models.mtf_profile import MTFProfile


@dataclass
class ActiveSetup:
    """Сетап, отслеживаемый после отправки сигнала ИЛИ заведённый в режим наблюдения (watch).

    Когда is_watch=True — это «наблюдение» от радара (без первоначального сигнала).
    В этом режиме запись живёт до наступления STRONG / таймаута / перелоя.
    При появлении сигнала запись может быть переведена в обычный режим (is_watch=False).
    """
    symbol: str
    tfs: MTFProfile
    pump_start_time: datetime
    last_checked: datetime

    # --- поля watch-режима ---
    is_watch: bool = False
    watch_expire_at: Optional[datetime] = None
    watch_base_low: Optional[float] = None
