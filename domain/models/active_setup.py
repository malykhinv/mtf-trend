from dataclasses import dataclass
from datetime import datetime

from domain.models.mtf_profile import MTFProfile


@dataclass
class ActiveSetup:
    """Сетап, отслеживаемый после отправки сигнала."""
    symbol: str
    tfs: MTFProfile
    pump_start_time: datetime
    last_checked: datetime
