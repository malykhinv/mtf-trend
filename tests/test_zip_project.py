from __future__ import annotations

from zipfile import ZipFile

from zip_project import build_zip


def test_project_zip_includes_package_init_files(tmp_path) -> None:
    root = tmp_path / "repo"
    package_dir = root / "src" / "anomaly_science" / "data"
    package_dir.mkdir(parents=True)

    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "src" / "anomaly_science" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "loader.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package_dir / "_private.py").write_text("SHOULD_NOT_BE_PACKED = True\n", encoding="utf-8")

    zip_path = build_zip(
        root=root,
        output_directory=tmp_path,
        prefix="project",
        include_existing_zip_files=False,
        exclude_legacy_quarantine=False,
    )

    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())

    assert "src/anomaly_science/__init__.py" in names
    assert "src/anomaly_science/data/__init__.py" in names
    assert "src/anomaly_science/data/loader.py" in names
    assert "src/anomaly_science/data/_private.py" not in names


def test_project_zip_excludes_top_level_tmp_artifacts(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tmp" / "real_smoke" / "future").mkdir(parents=True)

    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tmp" / "real_smoke" / "future" / "anomaly_future_paths.csv").write_text(
        "heavy,artifact\n",
        encoding="utf-8",
    )

    zip_path = build_zip(
        root=root,
        output_directory=tmp_path,
        prefix="project",
        include_existing_zip_files=False,
        exclude_legacy_quarantine=False,
    )

    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())

    assert "main.py" in names
    assert "src/module.py" in names
    assert not any(name.startswith("tmp/") for name in names)
