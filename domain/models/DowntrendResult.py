from dataclasses import dataclass, field

@dataclass
class DowntrendResult:
    has_downtrend: bool
    bar1_idx: int | None = None
    sh_last_idx: int | None = None
    last_ll_idx: int | None = None
    folds: list[tuple[int, int, int, int]] = field(default_factory=list)
