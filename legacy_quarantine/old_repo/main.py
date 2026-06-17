"""Модуль проекта."""

from __future__ import annotations

import os
import signal
import sys
import threading

from cli.parser import build_parser, resolve_handler
from config import load_config


_SIGINT_COUNT = 0


def _install_responsive_sigint_handler() -> None:
    """Make Ctrl+C visible and make repeated Ctrl+C force process exit.

    Backtests can be inside pandas/parquet work or unwinding ThreadPoolExecutor
    workers when the first KeyboardInterrupt is raised.  The first Ctrl+C asks the
    command to stop normally; the second one is an operator override that exits
    the process without waiting for non-daemon worker threads.
    """

    if threading.current_thread() is not threading.main_thread():
        return

    def _handle_sigint(signum: int, frame: object) -> None:  # noqa: ARG001 - signal handler signature
        global _SIGINT_COUNT
        _SIGINT_COUNT += 1
        if _SIGINT_COUNT <= 1:
            print(
                "\nCtrl+C получен: останавливаю команду. Повторный Ctrl+C — принудительный выход.",
                file=sys.stderr,
                flush=True,
            )
            raise KeyboardInterrupt
        print(
            "\nПовторный Ctrl+C: принудительный выход без ожидания рабочих потоков.",
            file=sys.stderr,
            flush=True,
        )
        os._exit(130)

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except (OSError, ValueError):
        return


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
            from ctypes import windll as ctypes_windll
        except ImportError:
            ctypes_windll = None
        if ctypes_windll is not None:
            try:
                kernel32 = getattr(ctypes_windll, "kernel32", None)
                set_console_cp = getattr(kernel32, "SetConsoleCP", None)
                set_console_output_cp = getattr(kernel32, "SetConsoleOutputCP", None)
                if callable(set_console_cp):
                    set_console_cp(65001)
                if callable(set_console_output_cp):
                    set_console_output_cp(65001)
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
    try:
        _install_responsive_sigint_handler()
        _force_single_thread_mode()
        _configure_console_encoding()
        config = load_config()
        parser = build_parser()
        args = parser.parse_args()
        handler = resolve_handler(args.command)
        return handler(config, args)
    except KeyboardInterrupt:
        print("Команда остановлена пользователем.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
