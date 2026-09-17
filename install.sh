#!/usr/bin/env bash
# Mu3Lab bootstrap entrypoint. The local HTML dashboard owns host mutations.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
exec "$ROOT_DIR/check.sh" "$@"
