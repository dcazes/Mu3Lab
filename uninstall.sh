#!/usr/bin/env bash
# Mu3Lab :: uninstall.sh
# WHAT:  Removes Mu3Lab from this computer: its apps, containers, images,
#        volumes, data (/srv/mu3lab), background services and private
#        addresses. With --everything it also removes the shared tools the
#        installer added: Docker, Tailscale (after logging out), Node.js and
#        NVIDIA container support, plus their package sources.
# NEVER: Touches system Python, GPU drivers, base packages (curl, git, ...),
#        or runs `apt autoremove`. Only files and packages Mu3Lab adds.
# RUN:   ./uninstall.sh [--everything] [--dry-run] [--yes]
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
EVERYTHING=false
DRY_RUN=false
ASSUME_YES=false
FAILURES=()

usage() {
  cat <<'EOF'
Usage: ./uninstall.sh [--everything] [--dry-run] [--yes]

Removes Mu3Lab and ALL of its data (apps, accounts, files, passwords stored in
Vaultwarden). This cannot be undone.

  --everything  also remove Docker (and everything in it), Tailscale (logs this
                computer out of your tailnet), Node.js and NVIDIA container
                support. Your system Python, GPU drivers and other programs
                are not touched.
  --dry-run     show what would be removed, change nothing
  --yes         do not ask for confirmation
EOF
}

for arg in "$@"; do
  case "$arg" in
    --everything) EVERYTHING=true ;;
    --dry-run) DRY_RUN=true ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option '$arg'. Run ./uninstall.sh --help" >&2; exit 1 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
run() {
  # Print, then run unless this is a dry run. Failures are collected, never fatal,
  # so one missing piece does not leave the rest installed.
  printf '  $ %s\n' "$*"
  $DRY_RUN && return 0
  "$@" >/dev/null 2>&1 || { FAILURES+=("$*"); printf '    (failed, continuing)\n'; }
}
have() { command -v "$1" >/dev/null 2>&1; }
installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }

[[ "$(id -u)" -ne 0 ]] || { echo "Run this as your normal user, not with sudo." >&2; exit 1; }

cat <<EOF

This removes Mu3Lab from this computer, including ALL of its data:
  - every app and its files (photos, documents, recipes, ...)
  - your Vaultwarden vault and Authentik accounts
  - the dashboard and its settings
EOF
if $EVERYTHING; then
  cat <<'EOF'
It also removes:
  - Docker and EVERYTHING stored in it (including anything not from Mu3Lab)
  - Tailscale (this computer is logged out of your tailnet)
  - Node.js
  - NVIDIA container support (your GPU driver is kept)
EOF
fi
echo "Your system Python, GPU drivers and other programs are not touched."
if $DRY_RUN; then
  echo "(Dry run: nothing will be changed.)"
elif ! $ASSUME_YES; then
  printf '\nType DELETE to continue: '
  read -r answer
  [[ "$answer" == "DELETE" ]] || { echo "Nothing was changed."; exit 1; }
fi

if ! $DRY_RUN; then
  echo "Administrator access is needed; enter your password if asked."
  sudo -v || { echo "Administrator access was not granted. Nothing was changed." >&2; exit 1; }
  ( while kill -0 "$$" 2>/dev/null; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &
  KEEPALIVE_PID=$!
  trap 'kill "$KEEPALIVE_PID" 2>/dev/null || true' EXIT
fi

# --- Background services ------------------------------------------------------
step "Stopping the Mu3Lab dashboard"
for unit in mu3lab-ctl.service mu3lab-worker.service; do
  if [[ -f "$HOME/.config/systemd/user/$unit" ]]; then
    run systemctl --user disable --now "$unit"
    run rm -f "$HOME/.config/systemd/user/$unit"
  fi
done
run systemctl --user daemon-reload
if [[ "$(loginctl show-user "$USER" --property=Linger 2>/dev/null)" == "Linger=yes" ]]; then
  run sudo -n loginctl disable-linger "$USER"
fi

# --- Private addresses (Tailscale Serve routes to Mu3Lab's loopback ports) ------
if have tailscale && ! $EVERYTHING; then
  step "Removing Mu3Lab's private addresses"
  ports="$(tailscale serve status --json 2>/dev/null | python3 -c '
import json, re, sys
try:
    web = json.load(sys.stdin).get("Web", {})
except ValueError:
    web = {}
for host, cfg in web.items():
    targets = [h.get("Proxy", "") for h in cfg.get("Handlers", {}).values()]
    if any(re.match(r"http://127\.0\.0\.1:194\d\d", t) for t in targets):
        print(host.rsplit(":", 1)[-1])
')"
  for port in $ports; do run sudo -n tailscale serve --https="$port" off; done
fi

# --- Docker contents --------------------------------------------------------------
if have docker; then
  step "Removing Mu3Lab's containers, volumes, networks and images"
  DOCKER=(docker)
  docker info >/dev/null 2>&1 || DOCKER=(sudo -n docker)
  is_ours='^(mu3lab-.*|ingress|authentik|vaultwarden)$'
  mapfile -t projects < <("${DOCKER[@]}" ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u | grep -E "$is_ours")
  for project in "${projects[@]}"; do
    mapfile -t ids < <("${DOCKER[@]}" ps -aq --filter "label=com.docker.compose.project=$project")
    [[ ${#ids[@]} -gt 0 ]] && run "${DOCKER[@]}" rm -f "${ids[@]}"
  done
  mapfile -t volumes < <("${DOCKER[@]}" volume ls --format '{{.Name}} {{.Label "com.docker.compose.project"}}' \
    | awk -v re="$is_ours" '$2 ~ re || $1 ~ /^(mu3lab|ingress_|authentik_|vaultwarden_)/ {print $1}')
  [[ ${#volumes[@]} -gt 0 ]] && run "${DOCKER[@]}" volume rm -f "${volumes[@]}"
  for net in mu3lab_frontend mu3lab_backend mu3lab_mcp; do
    "${DOCKER[@]}" network inspect "$net" >/dev/null 2>&1 && run "${DOCKER[@]}" network rm "$net"
  done
  mapfile -t listed < <(grep -hoE '"[a-z0-9./_-]+(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?"' \
    "$ROOT/services.yaml" "$ROOT/mcp-catalog.yaml" 2>/dev/null | tr -d '"' | grep -E '[:/]' | sed 's/@sha256:.*//' | sort -u)
  mapfile -t images < <("${DOCKER[@]}" images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | while read -r ref id; do
    repo="${ref%:*}"
    if [[ "$repo" == mu3lab* ]] || printf '%s\n' "${listed[@]}" | grep -qxE "(docker\.io/)?(library/)?${ref//./\\.}|(docker\.io/)?(library/)?${repo//./\\.}(:.*)?"; then
      echo "$id"
    fi
  done | sort -u)
  [[ ${#images[@]} -gt 0 ]] && run "${DOCKER[@]}" rmi -f "${images[@]}"
  run "${DOCKER[@]}" image prune -f
fi

# --- Data ---------------------------------------------------------------------------
step "Deleting Mu3Lab's data (/srv/mu3lab)"
[[ -e /srv/mu3lab ]] && run sudo -n rm -rf /srv/mu3lab

step "Cleaning this folder (generated files only; your checkout is kept)"
for path in .venv .env .state dashboard/node_modules dashboard/dist .mypy_cache .ruff_cache; do
  [[ -e "$ROOT/$path" ]] && run rm -rf "${ROOT:?}/$path"
done
run find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} +
run rm -rf "/tmp/mu3lab-docker-cfg-$(id -u)"

# --- Shared tools (only with --everything) ------------------------------------------
purge() {
  local present=()
  for pkg in "$@"; do installed "$pkg" && present+=("$pkg"); done
  [[ ${#present[@]} -gt 0 ]] && run sudo -n env DEBIAN_FRONTEND=noninteractive apt-get purge -y "${present[@]}"
}
remove_files() { for f in "$@"; do [[ -e "$f" ]] && run sudo -n rm -rf "$f"; done; }

if $EVERYTHING; then
  step "Removing Tailscale (logging this computer out first)"
  if have tailscale; then
    run sudo -n tailscale serve reset
    run sudo -n tailscale logout
  fi
  purge tailscale tailscale-archive-keyring
  remove_files /var/lib/tailscale /etc/apt/sources.list.d/tailscale.list /etc/apt/sources.list.d/tailscale.sources \
    /etc/apt/keyrings/tailscale.gpg /usr/share/keyrings/tailscale-archive-keyring.gpg

  step "Removing Docker and everything stored in it"
  if systemctl list-unit-files docker.service >/dev/null 2>&1; then
    run sudo -n systemctl disable --now docker.service docker.socket containerd.service
  fi
  purge docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras
  remove_files /var/lib/docker /var/lib/containerd /etc/docker /etc/apt/sources.list.d/docker.list \
    /etc/apt/sources.list.d/docker.sources /etc/apt/keyrings/docker.asc
  getent group docker >/dev/null && run sudo -n groupdel docker

  step "Removing NVIDIA container support (the GPU driver is kept)"
  purge nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1
  remove_files /etc/apt/sources.list.d/nvidia-container-toolkit.list /etc/apt/keyrings/nvidia-container-toolkit.asc \
    /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg /etc/nvidia-container-runtime

  step "Removing Node.js"
  purge nodejs
  remove_files /etc/apt/sources.list.d/nodesource.list /etc/apt/sources.list.d/nodesource.sources \
    /etc/apt/keyrings/nodesource.gpg /usr/share/keyrings/nodesource.gpg

  run sudo -n apt-get update -qq
fi

echo
if $DRY_RUN; then
  echo "Dry run finished. Nothing was changed."
elif [[ ${#FAILURES[@]} -eq 0 ]]; then
  echo "Mu3Lab has been removed."
  $EVERYTHING && echo "Log out and back in (or restart) so your account forgets the removed 'docker' group."
  echo "To install again: ./install.sh"
else
  echo "Mu3Lab was removed, but these steps failed (you can run ./uninstall.sh again):"
  printf '  - %s\n' "${FAILURES[@]}"
  exit 1
fi
