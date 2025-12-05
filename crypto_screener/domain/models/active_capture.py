from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ActiveCapture:
    added_at: datetime
    deadline: datetime
    message_link: Optional[str]
    is_setup_active: bool = True