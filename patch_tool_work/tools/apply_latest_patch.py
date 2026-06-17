from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


class PatchApplyError(RuntimeError):
    """Raised when the latest patch cannot be safely applied and committed."""


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        command = " ".join(args)
        details = result.stderr.strip() or result.stdout.strip()
        raise PatchApplyError(f"Command failed: {command}\n{details}")
    return result


def _git(repo_root: Path, args: Sequence[str], *, check: bool = True) -> str:
    result = _run(["git", *args], cwd=repo_root, check=check)
    return result.stdout


def _find_repo_root(start: Path) -> Path:
    start = start.resolve()
    result = _run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
    )
    if result.returncode != 0:
        raise PatchApplyError(
            "Not inside a git repository. Run this script from the project repo."
        )
    return Path(result.stdout.strip()).resolve()


def _status_path(line: str) -> str:
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.replace("\\", "/")


def _is_patch_store_path(path: str, patch_dir: str) -> bool:
    normalized_patch_dir = patch_dir.strip("/")
    return path == normalized_patch_dir or path.startswith(f"{normalized_patch_dir}/")


def _status_lines(repo_root: Path) -> list[str]:
    output = _git(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    )
    return [line for line in output.splitlines() if line.strip()]


def _assert_clean_except_patch_store(repo_root: Path, patch_dir: str) -> None:
    dirty = [
        line
        for line in _status_lines(repo_root)
        if not _is_patch_store_path(_status_path(line), patch_dir)
    ]
    if dirty:
        formatted = "\n".join(dirty)
        raise PatchApplyError(
            "Working tree has changes outside the patch store. "
            "Commit/stash them before applying a generated patch.\n"
            f"{formatted}"
        )


def _latest_patch(patch_root: Path) -> Path:
    if not patch_root.is_dir():
        raise PatchApplyError(f"Patch directory does not exist: {patch_root}")

    patches = sorted(
        (path for path in patch_root.glob("*.patch") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name.lower()),
    )
    if not patches:
        raise PatchApplyError(f"No .patch files found in {patch_root}")
    return patches[-1]


def _commit_message_from_patch_name(patch_path: Path) -> str:
    message = patch_path.stem.strip()
    if not message:
        raise PatchApplyError(f"Patch filename is not a valid commit message: {patch_path}")
    if "\n" in message or "\r" in message:
        raise PatchApplyError(f"Patch filename contains a newline: {patch_path}")
    return message


def _stage_all_except_patch_store(repo_root: Path, patch_dir: str) -> None:
    _git(repo_root, ["add", "-A", "--", "."])
    patch_root = repo_root / patch_dir
    if patch_root.exists():
        _git(repo_root, ["reset", "-q", "--", patch_dir])


def _has_staged_changes(repo_root: Path) -> bool:
    result = _run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 1


def apply_latest_patch(*, start: Path, patch_dir: str, dry_run: bool) -> Path:
    repo_root = _find_repo_root(start)
    patch_root = (repo_root / patch_dir).resolve()
    patch_path = _latest_patch(patch_root)
    commit_message = _commit_message_from_patch_name(patch_path)

    _assert_clean_except_patch_store(repo_root, patch_dir)
    _git(repo_root, ["apply", "--check", str(patch_path)])

    if dry_run:
        print(f"OK: patch can be applied: {patch_path.relative_to(repo_root)}")
        print(f"Commit message: {commit_message}")
        return patch_path

    _git(repo_root, ["apply", str(patch_path)])
    _stage_all_except_patch_store(repo_root, patch_dir)

    if not _has_staged_changes(repo_root):
        raise PatchApplyError(
            f"Patch applied but produced no staged changes: {patch_path}"
        )

    _git(repo_root, ["commit", "-m", commit_message])
    print(f"Applied and committed: {patch_path.relative_to(repo_root)}")
    print(f"Commit message: {commit_message}")
    return patch_path


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the newest .patch file from .patches and commit it."
    )
    parser.add_argument(
        "--patch-dir",
        default=".patches",
        help="Patch directory relative to the git repository root. Default: .patches",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check that the newest patch applies cleanly, but do not apply or commit it.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        apply_latest_patch(
            start=Path.cwd(),
            patch_dir=args.patch_dir,
            dry_run=args.dry_run,
        )
    except PatchApplyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
