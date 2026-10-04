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
# shellcheck source=tools/toolchain.sh
source "$ROOT/tools/toolchain.sh"

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
for tool in curl tar sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is missing." "sudo apt install curl tar coreutils, then run ./install.sh again."
done

# --- 2. One password prompt for the whole setup --------------------------------
step "Administrator access"
echo "Setup installs system software (Docker and Tailscale), so it needs your"
echo "password once. It is used only by sudo and never stored by Mu3Lab."
sudo -n true 2>/dev/null || sudo -v \
  || fail "Administrator access was not granted." "Run ./install.sh again and enter your password."
# Keep the sudo session alive while setup runs; it stops when this script ends.
( while kill -0 "$$" 2>/dev/null; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &
KEEPALIVE_PID=$!
trap 'kill "$KEEPALIVE_PID" 2>/dev/null || true' EXIT

# --- 3. The installer's own Python environment ----------------------------------
# uv (a pinned, checksum-verified download) installs one exact Python build and
# the exact packages in uv.lock, so every computer runs identical software and
# the host's own Python version does not matter.
step "Preparing the installer"
mu3lab_ensure_uv || fail "Could not download the installer's toolchain." "Check the internet connection and run ./install.sh again."
mu3lab_sync_python || fail "Python packages could not be installed." "Check the internet connection and run ./install.sh again."
mu3lab_ensure_bw || fail "Bitwarden could not be installed." "Check the internet connection and run ./install.sh again."

# --- 4. Install ---------------------------------------------------------------------
cd "$ROOT"
set +e
"$ROOT/.venv/bin/python" -m ctl.bootstrap.terminal
status=$?
set -e
exit "$status"
