#!/usr/bin/env bash
# Mu3Lab :: start.sh
# WHAT:  Turns Mu3Lab back on after ./stop.sh or the dashboard's Turn off, printing its
#        progress. When Mu3Lab is already on, restarts the dashboard and its worker instead.
# NEVER: Changes host setup (that is ./install.sh).
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" || ! -f "$ROOT_DIR/dashboard/dist/index.html" ]]; then
  echo "Mu3Lab is not installed yet. Run ./install.sh to open the bootstrap dashboard." >&2
  exit 1
fi
cd "$ROOT_DIR"
state="$("$ROOT_DIR/.venv/bin/python" -m ctl.power status)"
if [[ "$state" != '{"state": "on"}' ]]; then
  exec "$ROOT_DIR/.venv/bin/python" -m ctl.power on
fi
if ! systemctl --user restart mu3lab-ctl.service mu3lab-worker.service; then
  echo "Could not restart the Mu3Lab dashboard. Complete bootstrap first." >&2
  exit 1
fi
printf 'Mu3Lab is on; its dashboard restarted. Open its private Tailscale URL from the dashboard.\n'
