# MTF Trend

This repository contains trading utilities and services built around SQLite.

## SQLite mode

`data/db.py` now uses `WAL` journal mode and a 30‑second timeout on
connections to reduce `database is locked` errors. A module level `DB_LOCK`
is provided for serializing write operations when running in multi-threaded
contexts.
