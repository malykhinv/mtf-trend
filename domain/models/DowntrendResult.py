from dataclasses import dataclass


@dataclass
class DowntrendResult:
    ok: bool
    bar1_idx: int = -1
    sh_last_idx: int = -1
    last_ll_idx: int = -1