"""Модуль проекта."""

from __future__ import annotations

import os
import sys
from ctypes import windll

from cli.parser import build_parser, resolve_handler
from config import load_config


# region Приватные
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


def _configure_console_encoding() -> None:
    if os.name == "nt":
        try:
            windll.kernel32.SetConsoleCP(65001)
            windll.kernel32.SetConsoleOutputCP(65001)
        except OSError:
            pass
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


# endregion Приватные
def main() -> int:
    _force_single_thread_mode()
    _configure_console_encoding()
    config = load_config()
    parser = build_parser()
    args = parser.parse_args()
    handler = resolve_handler(args.command)
    return handler(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
