#!/usr/bin/env bash
# List the IDs of Mu3Lab's containers, and only Mu3Lab's: those whose Compose
# project lives in this checkout's apps/ folder or under
# /srv/mu3lab/projects. Pass --all to include stopped containers.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
runtime="${MU3LAB_RUNTIME_ROOT:-/srv/mu3lab}/projects"
all=()
[[ "${1:-}" == "--all" ]] && all=(-a)
docker ps "${all[@]}" --format '{{.ID}} {{.Label "com.docker.compose.project.working_dir"}}' |
  while read -r id dir; do
    case "$dir" in
      "$repo"/apps/* | "$runtime"/*) echo "$id" ;;
    esac
  done
