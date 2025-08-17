from dataclasses import dataclass

from domain.models.Bar import Bar
from domain.models.ExtremumType import ExtremumType


@dataclass(slots=True)
class Extremum:
    bar: Bar
    type: ExtremumType

