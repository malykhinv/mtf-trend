# Python environment entrypoints

This project keeps dependency declarations in `pyproject.toml` and exposes two
reviewable pip entrypoints:

```bash
python -m pip install -r requirements/test.txt
python -m pip install -r requirements/dev.txt
```

`constraints/research-minimums.txt` mirrors the direct dependency lower bounds
used by the project. It is not a solved transitive lockfile. For an exact local
lock, generate it with the resolver used on the target machine and commit the
result separately from research-logic changes.

After installation, verify the environment with:

```bash
python scripts/check_environment.py --scope test
python -m compileall -q main.py src tests zip_project.py
python -m pytest -q
```
