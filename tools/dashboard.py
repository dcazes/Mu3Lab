"""Run dashboard development, verification or builds without host Node.js."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from ctl.bootstrap import stamps
from ctl.dashboard_build import COMMANDS, command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=COMMANDS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(command(root, args.task), check=False)
    if result.returncode == 0 and args.task == "build":
        stamps.write(root / stamps.BUILD_STAMP, stamps.dashboard_digest(root))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
