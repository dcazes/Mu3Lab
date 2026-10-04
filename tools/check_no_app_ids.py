"""Reject app-ID literals in shared control-plane code.

App-specific values belong in manifests, app scripts and hooks. Discover
platform services by capability so renaming an app does not break callers.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def violations(root: Path = ROOT) -> list[str]:
    ids = {folder.name for folder in (root / "apps").iterdir() if (folder / "app.yaml").exists()}
    result = []
    for path in sorted((root / "ctl").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ids:
                result.append(f"{path.relative_to(root)}:{node.lineno}: app ID {node.value!r} belongs in a manifest")
    return result


if __name__ == "__main__":
    errors = violations()
    print("\n".join(errors) if errors else "Shared control-plane code contains no app-ID literals.")
    raise SystemExit(bool(errors))
