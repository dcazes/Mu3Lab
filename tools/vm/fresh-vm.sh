#!/usr/bin/env bash
# Mu3Lab :: tools/vm/fresh-vm.sh
# WHAT:  Boots a throwaway Ubuntu 24.04 VM (QEMU/KVM) with this checkout copied
#        in, so the installer can be exercised from a genuinely fresh host.
# WHY:   The installer must be repeatable. Testing it on a developer machine
#        that is already installed only ever exercises the "skip" paths.
# RUN:   tools/vm/fresh-vm.sh up        boot a new VM and copy the repo in
#        tools/vm/fresh-vm.sh ssh [cmd] open a shell (or run cmd) in the VM
#        tools/vm/fresh-vm.sh sync      re-copy the working tree into the VM
#        tools/vm/fresh-vm.sh down      power off and delete the VM disk
# DEBUG: Console log: $VM_DIR/console.log. The VM user is "mu3lab" with
#        passwordless sudo (a real desktop user types their password once).
#        Port: host 2222 -> VM ssh.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
VM_DIR="${MU3LAB_VM_DIR:-$HOME/.cache/mu3lab-vm}"
BASE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
BASE="$VM_DIR/noble.img"
DISK="$VM_DIR/disk.qcow2"
SEED="$VM_DIR/seed.iso"
KEY="$VM_DIR/id_ed25519"
PIDFILE="$VM_DIR/qemu.pid"
SSH_PORT="${MU3LAB_VM_SSH_PORT:-2222}"
MEMORY="${MU3LAB_VM_MEMORY:-8G}"
CPUS="${MU3LAB_VM_CPUS:-4}"
SSH_OPTS=(-i "$KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)

vm_ssh() { ssh "${SSH_OPTS[@]}" mu3lab@127.0.0.1 "$@"; }

sync_repo() {
  rsync -a --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude .git --exclude .venv --exclude node_modules --exclude dashboard/dist \
    --exclude __pycache__ --exclude .state --exclude '.env' \
    "$ROOT/" mu3lab@127.0.0.1:Mu3Lab/
}

up() {
  command -v qemu-system-x86_64 >/dev/null || { echo "Install qemu-system-x86 first." >&2; exit 1; }
  [[ -r /dev/kvm ]] || { echo "KVM is not available (/dev/kvm)." >&2; exit 1; }
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "A VM is already running. Use: $0 down" >&2
    exit 1
  fi
  mkdir -p "$VM_DIR"
  if [[ ! -f "$BASE" ]]; then
    curl -fSL -o "$BASE.part" "$BASE_URL"
    mv "$BASE.part" "$BASE"
  fi
  [[ -f "$KEY" ]] || ssh-keygen -q -t ed25519 -N "" -f "$KEY"
  rm -f "$DISK"
  qemu-img create -q -f qcow2 -b "$BASE" -F qcow2 "$DISK" 60G
  cat >"$VM_DIR/user-data" <<EOF
#cloud-config
hostname: mu3lab-vm
users:
  - name: mu3lab
    groups: [sudo]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys: ["$(cat "$KEY.pub")"]
package_update: false
EOF
  printf 'instance-id: mu3lab-%s\nlocal-hostname: mu3lab-vm\n' "$(date +%s)" >"$VM_DIR/meta-data"
  genisoimage -quiet -output "$SEED" -volid cidata -joliet -rock "$VM_DIR/user-data" "$VM_DIR/meta-data"
  qemu-system-x86_64 -enable-kvm -cpu host -m "$MEMORY" -smp "$CPUS" \
    -drive "file=$DISK,if=virtio" -drive "file=$SEED,if=virtio,media=cdrom" \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22" \
    -device virtio-net-pci,netdev=net0 \
    -display none -serial "file:$VM_DIR/console.log" -daemonize -pidfile "$PIDFILE"
  printf 'Waiting for the VM to boot'
  for _ in $(seq 1 90); do
    if vm_ssh true 2>/dev/null; then
      vm_ssh 'cloud-init status --wait >/dev/null 2>&1 || true'
      printf '\n'
      sync_repo
      echo "VM ready. Open a shell with: $0 ssh   then run: cd Mu3Lab && ./install.sh"
      return 0
    fi
    printf '.'
    sleep 2
  done
  printf '\nThe VM did not answer on ssh; see %s/console.log\n' "$VM_DIR" >&2
  exit 1
}

down() {
  if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    rm -f "$PIDFILE"
  fi
  rm -f "$DISK" "$SEED"
  echo "VM stopped and its disk deleted."
}

case "${1:-}" in
  up) up ;;
  ssh) shift; vm_ssh "$@" ;;
  sync) sync_repo ;;
  down) down ;;
  *) echo "Usage: $0 up|ssh [command]|sync|down" >&2; exit 1 ;;
esac
