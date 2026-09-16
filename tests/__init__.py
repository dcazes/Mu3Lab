"""Mu3Lab :: tests/__init__.py

WHAT: Marks `tests/` as a package so `python -m unittest discover -s tests`
      (a.k.a. `make test`) finds every test_*.py module.
WHY:  Without this file, discovery silently collects zero tests and the
      suite looks green while testing nothing.
DEBUG: `python -m unittest discover -s tests -v` should list test names.
"""
