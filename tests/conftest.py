import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from bot.utils.clock import init_clock


@pytest.fixture(autouse=True)
def _reset_clock_timezone() -> None:
    init_clock(ZoneInfo("UTC"))
    yield
    init_clock(ZoneInfo("UTC"))
