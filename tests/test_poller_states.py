import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from app.pollers.base import _BasePoller
from domain.models.enums import BotState


def test_base_poller_watched_states_excludes_confirming():
    assert _BasePoller._WATCHED_STATES == {BotState.WATCHING, BotState.ENTERED}

