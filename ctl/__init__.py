"""Mu3Lab :: ctl/__init__.py

WHAT: Marks `ctl/` as the control-plane Python package.
WHY:  Lets `install.sh`, `start.sh`, systemd and tests import
      `ctl.preflight`, `ctl.app`, etc. from the repo root.
DEBUG: `python3 -c "import ctl; print(ctl.__version__)"` from the repo root.
"""

__version__ = "0.1.0"
