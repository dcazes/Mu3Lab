# Mu3Lab :: tools/toolchain.sh
# WHAT:  Installs the pinned uv release into .tools/ and syncs the Python
#        environment (.venv) from uv.lock. Sourced by install.sh; the
#        control plane calls the same commands through ctl/toolchain.py.
# WHY:   One exact Python build and one exact set of packages on every
#        computer, independent of the host's own Python.
# DEBUG: .tools/bin/uv --version; .tools/bin/uv sync --frozen --dry-run

MU3LAB_UV_VERSION="0.12.5"
declare -A MU3LAB_UV_SHA256=(
  [x86_64]="68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2"
  [aarch64]="9bf43b4d1a07665bf64d4c4e710930b382321a785e0eb10aac07f46471f86a31"
)

export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"
export UV_CACHE_DIR="$ROOT/.tools/cache"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
MU3LAB_UV="$ROOT/.tools/bin/uv"

mu3lab_ensure_uv() {
  if [[ -x "$MU3LAB_UV" ]] && [[ "$("$MU3LAB_UV" --version 2>/dev/null)" == "uv $MU3LAB_UV_VERSION"* ]]; then
    return 0
  fi
  local arch expected archive work
  arch="$(uname -m)"
  expected="${MU3LAB_UV_SHA256[$arch]:-}"
  if [[ -z "$expected" ]]; then
    echo "This processor ($arch) is not supported." >&2
    return 1
  fi
  archive="uv-$arch-unknown-linux-gnu.tar.gz"
  work="$(mktemp -d)"
  echo "Downloading uv $MU3LAB_UV_VERSION..."
  if ! curl -fsSL "https://github.com/astral-sh/uv/releases/download/$MU3LAB_UV_VERSION/$archive" -o "$work/$archive"; then
    rm -rf "$work"
    return 1
  fi
  if [[ "$(sha256sum "$work/$archive" | cut -d' ' -f1)" != "$expected" ]]; then
    echo "The uv download did not match its published checksum; nothing was installed." >&2
    rm -rf "$work"
    return 1
  fi
  tar -xzf "$work/$archive" -C "$work"
  mkdir -p "$ROOT/.tools/bin"
  install -m 0755 "$work/uv-$arch-unknown-linux-gnu/uv" "$MU3LAB_UV"
  rm -rf "$work"
}

mu3lab_sync_python() {
  (cd "$ROOT" && "$MU3LAB_UV" sync --frozen --no-dev --quiet)
}
