"""Mu3Lab test suite.

Run with `make test` (or `python -m unittest discover -s tests -t .`); the
`-t .` makes this package initializer run before any test module imports ctl.
"""

import os
import tempfile
from pathlib import Path

# Tests must never read or write the host's real /srv/mu3lab state. Point the
# runtime root at a path that does not exist, matching a fresh CI machine.
os.environ["MU3LAB_RUNTIME_ROOT"] = str(Path(tempfile.mkdtemp(prefix="mu3lab-tests-")) / "runtime-root")
