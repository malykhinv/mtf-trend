#!/usr/bin/env python3
"""Create a clean project zip from the repository root.

Rules:
- skip any file or directory whose name starts with "." or "_";
- skip existing .zip files unless --include-existing-zip-files is passed;
- optionally skip legacy_quarantine;
- do not follow symlinks.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def _allowed_name(name: str) -> bool:
    return bool(name) and not name.startswith(".") and not name.startswith("_")


def _iter_project_files(
    *,
    root: Path,
    zip_path: Path,
    include_existing_zip_files: bool,
    exclude_legacy_quarantine: bool,
) -> tuple[list[Path], dict[str, int]]:
    files: list[Path] = []
    counters = {
        "skipped_dirs_by_name": 0,
        "skipped_files_by_name": 0,
        "skipped_zip_files": 0,
        "skipped_legacy_quarantine": 0,
        "skipped_symlinks": 0,
    }

    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)

        kept_dirs: list[str] = []
        for dirname in dirnames:
            if not _allowed_name(dirname):
                counters["skipped_dirs_by_name"] += 1
                continue
            if exclude_legacy_quarantine and current == root and dirname == "legacy_quarantine":
                counters["skipped_legacy_quarantine"] += 1
                continue
            full_dir = current / dirname
            if full_dir.is_symlink():
                counters["skipped_symlinks"] += 1
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            if not _allowed_name(filename):
                counters["skipped_files_by_name"] += 1
                continue

            path = current / filename

            if path.is_symlink():
                counters["skipped_symlinks"] += 1
                continue

            try:
                resolved = path.resolve()
            except OSError:
                counters["skipped_files_by_name"] += 1
                continue

            if resolved == zip_path:
                continue

            if not include_existing_zip_files and path.suffix.lower() == ".zip":
                counters["skipped_zip_files"] += 1
                continue

            files.append(path)

    files.sort(key=lambda item: item.relative_to(root).as_posix().lower())
    return files, counters


def build_zip(
    *,
    root: Path,
    output_directory: Path,
    prefix: str,
    include_existing_zip_files: bool,
    exclude_legacy_quarantine: bool,
) -> Path:
    root = root.resolve()
    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = (output_directory / f"{prefix}_{stamp}.zip").resolve()

    files, counters = _iter_project_files(
        root=root,
        zip_path=zip_path,
        include_existing_zip_files=include_existing_zip_files,
        exclude_legacy_quarantine=exclude_legacy_quarantine,
    )

    if not files:
        raise RuntimeError("No files matched archive rules.")

    if zip_path.exists():
        zip_path.unlink()

    with ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())

    size_mb = zip_path.stat().st_size / (1024 * 1024)

    print(f"Project root: {root}")
    print(f"Output zip:   {zip_path}")
    print(f"Files:        {len(files)}")
    print(f"Size MB:      {size_mb:.2f}")
    print(f"Skipped dirs by .* or _*:  {counters['skipped_dirs_by_name']}")
    print(f"Skipped files by .* or _*: {counters['skipped_files_by_name']}")
    print(f"Skipped existing zip files: {counters['skipped_zip_files']}")
    print(f"Skipped symlinks: {counters['skipped_symlinks']}")
    if exclude_legacy_quarantine:
        print(f"Skipped legacy_quarantine: {counters['skipped_legacy_quarantine']}")

    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a clean project zip.")
    parser.add_argument("--output-directory", default="", help="Output directory. Defaults to project root.")
    parser.add_argument("--prefix", default="project", help="Output zip prefix.")
    parser.add_argument(
        "--include-existing-zip-files",
        action="store_true",
        help="Include existing .zip files in the new archive.",
    )
    parser.add_argument(
        "--exclude-legacy-quarantine",
        action="store_true",
        help="Exclude legacy_quarantine from the archive.",
    )
    args = parser.parse_args()

    root = Path.cwd()
    output_directory = Path(args.output_directory) if args.output_directory else root

    build_zip(
        root=root,
        output_directory=output_directory,
        prefix=args.prefix,
        include_existing_zip_files=args.include_existing_zip_files,
        exclude_legacy_quarantine=args.exclude_legacy_quarantine,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
