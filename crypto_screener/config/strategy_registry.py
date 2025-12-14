from crypto_screener.config.ppo_config import ppo_cfg
from crypto_screener.domain.strategies.ppo import PpoStrategy
from crypto_screener.domain.strategies.strategy import Strategy

# Стратегии, активированные через конфигурацию приложения.
DEFAULT_STRATEGIES: list[Strategy] = [PpoStrategy()]

# Конфигурации стратегий.
DEFAULT_STRATEGY_CONFIGS: dict[str, object] = {"ppo": ppo_cfg}
