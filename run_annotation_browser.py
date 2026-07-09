"""Launch the anomaly_science browser annotation app from the repository root."""

from pathlib import Path
import os
import sys
import traceback

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _reexec_in_venv() -> None:
    """If launched by the wrong interpreter (e.g. Explorer double-click uses the
    global Python without deps), relaunch this script with the project venv."""
    if os.environ.get("ANNOTATION_BROWSER_REEXEC"):
        return
    if sys.platform == "win32":
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = ROOT / ".venv" / "bin" / "python"
    try:
        same = venv_python.resolve() == Path(sys.executable).resolve()
    except OSError:
        same = False
    if venv_python.exists() and not same:
        os.environ["ANNOTATION_BROWSER_REEXEC"] = "1"
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def _pause_on_exit(message: str) -> None:
    """Keep a double-clicked console window open so the error stays readable."""
    print(message, flush=True)
    try:
        input("Press Enter to close this window... ")
    except (EOFError, KeyboardInterrupt):
        pass


def _run() -> None:
    from anomaly_science.annotation.app import main

    main()


if __name__ == "__main__":
    try:
        _reexec_in_venv()
        _run()
    except KeyboardInterrupt:
        pass
    except BaseException:  # noqa: BLE001 - top-level guard so the window never vanishes
        traceback.print_exc()
        _pause_on_exit("\nThe annotation browser crashed (traceback above).")
        sys.exit(1)
