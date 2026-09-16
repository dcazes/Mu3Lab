"""Mu3Lab :: tools/__init__.py

WHAT: Marks `tools/` as a package so tests can `from tools import run_tests`
      and pin the JSON wire format without spawning subprocesses.
WHY:  Importable tooling is testable tooling (see tests/test_run_tests.py).
DEBUG: `python3 -c "from tools import run_tests; print('ok')"` from root.
"""
