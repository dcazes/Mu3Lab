#!/usr/bin/env bash
# Start the already-installed Mu3Lab control plane without mutating host setup.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" || ! -f "$ROOT_DIR/dashboard/dist/index.html" ]]; then
  echo "Mu3Lab is not installed yet. Run ./install.sh to open the bootstrap dashboard." >&2
  exit 1
fi
if ! systemctl --user restart mu3lab-ctl.service; then
  echo "Could not restart mu3lab-ctl.service. Complete bootstrap first." >&2
  exit 1
fi
printf 'Mu3Lab control plane restarted. Open its private Tailscale URL from the dashboard.\n'
