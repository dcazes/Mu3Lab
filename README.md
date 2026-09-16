# Mu3Lab

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](ctl/requirements.txt)

**Minimal homelab bootstrap with a guided dashboard.** Mu3Lab takes a bare
Debian/Ubuntu machine from zero to working infrastructure — Docker, Tailscale,
and a local entry point — through three gated, self-verifying steps.
No Ansible, no committed build artifacts, no blind shell scripts.

## How it works

```
① Unit tests  →  ② Preflight  →  ③ Install
   (prove the      (measure the     (fix exactly
    ruler)          machine)         what's missing)
```

1. **Clone and check** — `./check.sh` needs only `git` and `python3`. It opens
   a zero-dependency local dashboard (`http://127.0.0.1:8799`) that runs the
   unit suite and host preflight behind three cards that unlock in order.
2. **Install** — card ③ remediates precisely: every component reports a
   granular state (`absent`, `daemon_down`, `no_group`, …) and the installer
   runs exactly the fix for that state — starting a stopped daemon instead of
   reinstalling it, creating only missing networks, skipping what's ready.
3. **Guided setup** — steps needing a human (Tailscale connection, Docker
   group re-login) pause as `waiting` rows with inline instructions instead
   of failing. Privilege escalation uses the native system dialog (polkit);
   passwords are never typed into any page.

## Quickstart

```bash
git clone https://github.com/dcazes/Mu3Lab.git
cd Mu3Lab
./check.sh            # opens the check dashboard, no installs
```

Requires: Debian 12+ / Ubuntu 22.04+ (or derivative), x86-64 or ARM64,
Python 3.10+. Everything else is installed by card ③.

## Project layout

| Path | Purpose |
|---|---|
| `check.sh` | Fresh-user entry: python precheck, venv prove-or-warn, serves dashboard |
| `check_server.py` | Stdlib-only check dashboard (retired when the real dashboard lands) |
| `ctl/preflight.py` | Read-only host checks; thresholds, never version pins |
| `ctl/install.py` | Check-first remediation runner (states → exact fixes) |
| `ctl/privilege.py`, `ctl/actions.py` | pkexec-first elevation + auditable host verbs |
| `tools/run_tests.py` | JSON-line test runner for live dashboard progress |
| `core/ingress/` | Minimal Caddy entry point (`:19460` → dashboard) |
| `tests/` | Unit suite — every check pinned by fixtures, nothing touches the host |
| `PLAN.md`, `BUILD_ORDER.md` | Architecture and file-by-file build record |

See [`docs/SETUP.md`](docs/SETUP.md) for the full guide (written from verified
runs), [`PLAN.md`](PLAN.md) for architecture decisions, and
[`BUILD_ORDER.md`](BUILD_ORDER.md) for the build sequence.

## Status

Active development. The check dashboard (cards ①–③ through infrastructure
install) works; the React control dashboard, Authentik/Vaultwarden onboarding,
and app catalog arrive in later phases. `PLAN.md` tracks what's locked vs open.

## License

Mu3Lab is free software: you can redistribute and/or modify it under the terms
of the **GNU Affero General Public License** as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE) for the full text.
