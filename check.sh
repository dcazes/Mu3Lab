#!/usr/bin/env bash
# Mu3Lab :: check.sh
# WHAT:  Fresh-user entry point. Prechecks python3, proves the venv works,
#        then hands off to the stdlib-only check dashboard (check_server.py).
# WHY:   New users trip on `python3 check_server.py` (wrong python, missing
#        venv package, tracebacks). This script fails with plain English
#        BEFORE any Python runs, and proves venv creation early so install.sh
#        inherits a known-good .venv instead of discovering breakage mid-run.
# RUN:   ./check.sh [--no-open] [--no-venv] [--port N] [--help]
#        --no-open  don't auto-open the browser (SSH sessions)
#        --no-venv  skip venv creation, run on system python3 (stdlib is enough)
#        --port N   serve the check dashboard on N (default 8799)
# DEBUG: Every failure prints what is missing and the exact fix. Exit codes:
#        0 ok (execs server, never returns) · 1 usage/env error ·
#        2 python missing or too old. This script NEVER uses sudo.

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Constants: defaults live here with reasons.
# ---------------------------------------------------------------------------
PORT=8799            # check dashboard port; 8787 is reserved for the real dashboard
OPEN_BROWSER=true    # auto-open unless --no-open (locked decision)
WANT_VENV=true       # prove the venv unless --no-open... (see --no-venv)
MIN_PY_MAJOR=3
MIN_PY_MINOR=10      # must match ctl/preflight.py MIN_PYTHON

# mu3lab_root: the repo dir (this script lives in it). No cd tricks elsewhere.
MU3LAB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

usage() {
  cat <<'EOF'
Usage: ./check.sh [--no-open] [--no-venv] [--port N]

Zero-install health check for a fresh Mu3Lab checkout.
Runs the unit tests and host preflight behind a small local dashboard.

  --no-open   print the dashboard URL instead of opening a browser
  --no-venv   skip venv creation (system python3 runs the stdlib-only checks)
  --port N    serve on port N (default 8799)
  -h, --help  show this help

Needs only: git checkout, python3 >= 3.10. No sudo, no pip, no node.
EOF
}

# --- parse args (manual loop: no getopt dependency, keeps it portable) ---
# Consume --port's separate value as well as --port=N. The documentation has
# always advertised both forms; treating the value as a separate unknown
# argument made the bootstrap fail before the page could open.
while [[ $# -gt 0 ]]; do
  arg="$1"
  shift
  case "$arg" in
    --no-open) OPEN_BROWSER=false ;;
    --no-venv) WANT_VENV=false ;;
    --port)
      [[ $# -gt 0 ]] || { echo "Error: --port needs a value: ./check.sh --port 8799" >&2; exit 1; }
      PORT="$1"
      shift ;;
    --port=*) PORT="${arg#--port=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown option '$arg'. See ./check.sh --help" >&2; exit 1 ;;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "Error: --port must be numeric, got '$PORT'" >&2; exit 1; }

# --- 0. port already taken? (a previous ./check.sh is usually the culprit) ---
# A second server cannot bind the port: instead of dying with a traceback,
# report WHO holds it (with its own version/start time) and stop. Stale
# servers serve new pages with old APIs — the exact confusion this avoids.
if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT))==0 else 1)" 2>/dev/null; then
  echo "Port $PORT is already serving something." >&2
  python3 - "$PORT" >&2 <<'EOF'
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as r:
        st = json.load(r)
    print(f"  It is a Mu3Lab check server (code v{st.get('code_version', '?no-version(old)')}, started {st.get('server_started_at', 'unknown')}).", file=sys.stderr)
    print(f"  Open http://127.0.0.1:{port} to use it,", file=sys.stderr)
    print(f"  or stop that process first (Ctrl-C in its terminal), then re-run ./check.sh.", file=sys.stderr)
except Exception:
    print(f"  It is NOT a Mu3Lab server. Free the port, then re-run ./check.sh.", file=sys.stderr)
EOF
  exit 1
fi

# --- 1. python3 present? (plain English, no traceback) ---
command -v python3 >/dev/null 2>&1 || {
  echo "Mu3Lab check needs python3, but none was found." >&2
  echo "Fix: sudo apt install -y python3   (needs 3.10 or newer)" >&2
  exit 2
}

# --- 2. python3 >= 3.10? (version tuple compared inside python, quoted safely) ---
if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)"; then
  echo "Mu3Lab check needs python3 >= $MIN_PY_MAJOR.$MIN_PY_MINOR, found: $(python3 --version 2>&1)" >&2
  echo "Fix: install a newer python3, then re-run ./check.sh" >&2
  echo "Note: never remove the system python3 — add a newer one alongside it." >&2
  exit 2
fi

# --- 3. prove the venv (early warning, never escalates) ---
# If python3-venv is missing, venv creation fails. That failure is EXPECTED on
# stock Ubuntu — install.sh fixes it later. So: try, and on failure continue
# in system-python mode with a visible warning. The checks are stdlib-only,
# so they run identically either way.
PYBIN="python3"
if "$WANT_VENV"; then
  if [[ -x "$MU3LAB_ROOT/.venv/bin/python" ]]; then
    PYBIN="$MU3LAB_ROOT/.venv/bin/python"   # reuse: install.sh inherits it
  elif python3 -c "import ensurepip" >/dev/null 2>&1; then
    echo "check: creating .venv (first run proves venv creation works)..."
    if python3 -m venv "$MU3LAB_ROOT/.venv" 2>/dev/null; then
      PYBIN="$MU3LAB_ROOT/.venv/bin/python"
      echo "check: .venv ready."
    else
      echo "check: WARNING: venv creation failed; continuing on system python3." >&2
      echo "check: install.sh will install python3-venv and retry this step." >&2
    fi
  else
    echo "check: WARNING: python3-venv not installed; continuing on system python3." >&2
    echo "check: install.sh will install it (sudo apt install python3-venv)." >&2
  fi
fi

# --- 4. hand off (exec: this shell is replaced, signals go to the server) ---
SERVER_ARGS=( "$MU3LAB_ROOT/check_server.py" --port "$PORT" )
if ! "$OPEN_BROWSER"; then
  SERVER_ARGS+=( --no-open )
fi
exec "$PYBIN" "${SERVER_ARGS[@]}"
