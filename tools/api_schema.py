"""Dump the public API contract without starting services or touching runtime data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ctl.api import create_app

TARGET = Path(__file__).resolve().parents[1] / "dashboard/openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(create_app(Path("/nonexistent-mu3lab-dashboard")).openapi(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not TARGET.is_file() or TARGET.read_text() != content:
            print("API schema is stale. Run make api-schema.")
            return 1
    else:
        TARGET.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
