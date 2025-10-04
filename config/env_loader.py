from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path = Path(".env")) -> None:
    """Load key-value pairs from a dotenv file into the environment.

    The loader skips empty lines and comments and does not overwrite values
    that are already present in the environment.
    """

    try:
        with path.open("r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if key:
                    os.environ.setdefault(key, value)
    except FileNotFoundError:
        return
