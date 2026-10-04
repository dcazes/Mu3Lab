"""The pinned uv toolchain that owns Mu3Lab's Python environment.

``install.sh`` downloads uv into ``.tools/`` and syncs ``.venv`` from
``uv.lock`` before any Mu3Lab Python runs (see ``tools/toolchain.sh``). After
an update the control plane runs the same sync so the new code finds exactly
the packages it was released with.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def uv_environment(root: Path) -> dict[str, str]:
    """The same uv settings ``tools/toolchain.sh`` exports."""
    return {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(root / ".tools" / "python"),
        "UV_CACHE_DIR": str(root / ".tools" / "cache"),
        "UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
    }


def sync_python(root: Path, timeout: int = 1800) -> tuple[int, str]:
    """Make ``.venv`` match ``uv.lock`` exactly; a no-op when it already does."""
    uv = root / ".tools" / "bin" / "uv"
    if not uv.is_file():
        return 1, "The uv toolchain is missing; run ./install.sh once."
    try:
        proc = subprocess.run(
            [str(uv), "sync", "--frozen", "--no-dev", "--quiet"],
            cwd=root,
            env=uv_environment(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()
