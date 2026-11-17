#!/usr/bin/env bash
# Mu3Lab :: stop.sh
# WHAT:  Turns all of Mu3Lab off: every app, sign-in, Caddy and the dashboard stop, and only
#        a tiny wake page stays on at the dashboard's address. Prints its progress.
#        Refused while a task (an update, a backup, an install) is running.
# RUN:   ./stop.sh   Turn it on again with ./start.sh or the wake page's Turn on button.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "Mu3Lab is not installed." >&2
  exit 1
fi
cd "$ROOT_DIR"
exec "$ROOT_DIR/.venv/bin/python" -m ctl.power off
