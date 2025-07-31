from __future__ import annotations

import yaml
from pathlib import Path

def load_config(path: str | Path = "config.yaml") -> dict:
    """Load configuration from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main() -> None:
    config = load_config()
    print("Configuration loaded:")
    print(config)

if __name__ == "__main__":
    main()
