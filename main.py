import asyncio
import logging

from config import make_profile_config_balanced
from app.logger import configure
from app.orchestrator import run


def main() -> None:
    cfg = make_profile_config_balanced()
    configure(level=getattr(cfg, "log_level", logging.INFO))
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
