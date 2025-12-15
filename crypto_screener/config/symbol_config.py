from dataclasses import dataclass


@dataclass(frozen=True)
class CapitalizationThresholds:
    LOW_MIN: float = 20_000_000
    MIDDLE_MIN: float = 1_000_000_000
    HIGH_MIN: float = 10_000_000_000


@dataclass(frozen=True)
class ContextThresholds:
    TRADES_MIN: int = 500_000
    VOLUME_MIN: float = 500_000_000


@dataclass(frozen=True)
class SymbolConfig:
    capitalization: CapitalizationThresholds = CapitalizationThresholds()
    context: ContextThresholds = ContextThresholds()


symbol_cfg = SymbolConfig()
