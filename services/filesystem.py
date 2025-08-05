from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class Filesystem(Protocol):
    def save_text(self, path: Path, content: str) -> None:
        """Сохранить текст в файл."""

    def exists(self, path: Path) -> bool:
        """Проверить наличие файла."""

