from dataclasses import dataclass

@dataclass
class EMA:
    """
    Хранит значения скользящих средних EMA20/50/100/200 для любого временного ряда.
    """
    ema20: float = 0.0
    ema50: float = 0.0
    ema100: float = 0.0
    ema200: float = 0.0
