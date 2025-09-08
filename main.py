import asyncio

from config import make_profile_config_balanced
from app.orchestrator import run


def main() -> None:
    asyncio.run(run(make_profile_config_balanced()))


if __name__ == "__main__":
    main()
