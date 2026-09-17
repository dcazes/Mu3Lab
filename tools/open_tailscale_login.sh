#!/usr/bin/env bash
# Mu3Lab :: tools/open_tailscale_login.sh
# WHAT: Run the normal interactive Tailscale join and open its login URL as
#       soon as the CLI prints it. The sudo password remains in the terminal.
# WHY:  Tailscale may take a few seconds to return its one-time URL; users
#       should not have to copy it manually. This script never stores it.
# RUN:  ./tools/open_tailscale_login.sh

set -Eeuo pipefail

opened=false
sudo tailscale up --hostname=mu3lab 2>&1 | while IFS= read -r line; do
  printf '%s\n' "$line"
  if [[ "$opened" == false && "$line" =~ (https://login\.tailscale\.com/[A-Za-z0-9/_-]+) ]]; then
    url="${BASH_REMATCH[1]}"
    opened=true
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$url" >/dev/null 2>&1 &
      printf 'Opened the Tailscale login page in your browser.\n'
    else
      printf 'Open this URL in a browser: %s\n' "$url"
    fi
  fi
done
