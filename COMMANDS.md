# Commands

Bootstrap check:

```bash
python main.py doctor
```

MVP1 data audit:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
```

Compile check:

```bash
python -m compileall main.py src tests
```

Tests:

```bash
python -m pytest tests
```
