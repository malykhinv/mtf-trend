"""Модуль проекта."""

from __future__ import annotations

import os

from cli.parser import build_parser, resolve_handler
from config import load_config


# область Приватные
def _force_single_thread_mode() -> None:
    single_thread_env = {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    for key, value in single_thread_env.items():
        os.environ[key] = value


# конец области Приватные
def main() -> int:
    _force_single_thread_mode()
    config = load_config()
    parser = build_parser()
    args = parser.parse_args()
    handler = resolve_handler(args.command)
    return handler(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
