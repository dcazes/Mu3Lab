#!/usr/bin/env bash
# Mu3Lab :: install.sh
# WHAT:  The one command to install, resume, or update Mu3Lab on this computer.
#        It checks the basics, asks for your computer password once, prepares
#        the installer's Python environment, then installs only what is
#        missing or out of date, asking you only for what it can't decide.
# RUN:   ./install.sh
# DEBUG: Safe to run again at any time: finished steps are skipped. The
#        password is only used by sudo; Mu3Lab never sees or stores it.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
MIN_PYTHON="3.10"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() {
  printf '\n\033[1;31mSetup cannot continue:\033[0m %s\n' "$1" >&2
  [[ -n "${2:-}" ]] && printf 'What to do: %s\n' "$2" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./install.sh

Installs Mu3Lab, or finishes/updates an existing installation. Finished steps
are skipped, so it is always safe to run again (for example after `git pull`).
To remove Mu3Lab, run ./uninstall.sh.
EOF
}

case "${1:-}" in
  "") ;;
  -h|--help) usage; exit 0 ;;
  *) fail "Unknown option '$1'." "Run ./install.sh --help" ;;
esac

bold "Mu3Lab setup"

# --- 1. The basics this script itself needs -----------------------------------
[[ "$(id -u)" -ne 0 ]] || fail "Please run this as your normal user, not as root or with sudo." "./install.sh"
command -v apt-get >/dev/null 2>&1 \
  || fail "Mu3Lab supports Ubuntu, Debian, Linux Mint and similar systems that use apt." ""
command -v python3 >/dev/null 2>&1 || fail "python3 is missing." "sudo apt install python3, then run ./install.sh again."
python3 -c "import sys; raise SystemExit(sys.version_info < tuple(map(int, '$MIN_PYTHON'.split('.'))))" \
  || fail "Mu3Lab needs Python $MIN_PYTHON or newer; this computer has $(python3 --version 2>&1)." \
          "Upgrade to Ubuntu 22.04 / Debian 12 or newer."

# --- 2. One password prompt for the whole setup --------------------------------
step "Administrator access"
echo "Setup installs system software (Docker, Tailscale, Node.js), so it needs your"
echo "password once. It is used only by sudo and never stored by Mu3Lab."
sudo -n true 2>/dev/null || sudo -v \
  || fail "Administrator access was not granted." "Run ./install.sh again and enter your password."
# Keep the sudo session alive while setup runs; it stops when this script ends.
( while kill -0 "$$" 2>/dev/null; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &
KEEPALIVE_PID=$!
trap 'kill "$KEEPALIVE_PID" 2>/dev/null || true' EXIT

# --- 3. The installer's own Python environment ----------------------------------
step "Preparing the installer"
if ! python3 -c "import ensurepip, venv" >/dev/null 2>&1; then
  echo "Installing Python's virtual environment support..."
  APT_LOG="$(mktemp)"
  if ! { sudo -n apt-get update -qq && sudo -n env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
         apt-get install -y -qq python3-venv python3-pip; } >"$APT_LOG" 2>&1; then
    cat "$APT_LOG" >&2
    fail "Could not install python3-venv." "Fix the package error above, then run ./install.sh again."
  fi
  rm -f "$APT_LOG"
fi
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Creating the Python environment (.venv)..."
  python3 -m venv "$ROOT/.venv" || fail "Could not create the Python environment." "sudo apt install python3-venv"
fi
STAMP="$ROOT/.venv/.mu3lab-requirements.sha256"
WANT="$(sha256sum "$ROOT/ctl/requirements.txt" | cut -d' ' -f1)"
if [[ "$(cut -d' ' -f1 "$STAMP" 2>/dev/null)" != "$WANT" ]] \
   || ! "$ROOT/.venv/bin/python" -c "import fastapi, yaml, httpx" >/dev/null 2>&1; then
  echo "Installing Python packages (first run takes a minute)..."
  "$ROOT/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$ROOT/ctl/requirements.txt" \
    || fail "Python packages could not be installed." "Check the internet connection and run ./install.sh again."
  printf '%s\n' "$WANT" >"$STAMP"
else
  echo "Python environment is up to date."
fi

# --- 4. Install ---------------------------------------------------------------------
cd "$ROOT"
set +e
"$ROOT/.venv/bin/python" -m ctl.bootstrap.terminal
status=$?
set -e
exit "$status"
