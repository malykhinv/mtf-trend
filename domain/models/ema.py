from dataclasses import dataclass

@dataclass
class EMA:
    ema20: float = 0.0
    ema50: float = 0.0
    ema100: float = 0.0
    ema200: float = 0.0
