from config import make_profile_config_balanced
from app.orchestrator import run


def main() -> None:
    cfg = make_profile_config_balanced()
    run(cfg)


if __name__ == "__main__":
    main()
