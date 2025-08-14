from dataclasses import dataclass

from domain.models.bar import Bar
from domain.models.extremum_type import ExtremumType


@dataclass(slots=True)
class Extremum:
    bar: Bar
    type: ExtremumType

