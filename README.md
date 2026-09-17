# Mu3Lab

Mu3Lab is a curated, private homelab control plane for Debian 12+ and Ubuntu
22.04+ x86-64 hosts. It separates first-run host preparation from normal app
management:

1. `./install.sh` (or `./check.sh`) opens a temporary local bootstrap dashboard at
   `127.0.0.1:8799`. It runs tests, measures the host, installs only missing
   dependencies, and pauses for required human actions such as the normal
   Tailscale web login and Docker-group re-login.
2. The React control plane is the permanent dashboard. It is intended to be
   reached through private Tailscale HTTPS, manages only Mu3Lab's curated
   services, and never treats arbitrary Docker projects as trusted apps.

## Product safety rules

- Tailscale and Authentik are mandatory infrastructure for the completed
  platform; reusable Tailscale auth keys are never accepted by the dashboard.
- Persistent data lives under `/srv/mu3lab`, not in the Git checkout.
- Git contains definitions and safe defaults, never application data, backups,
  or unencrypted secrets.
- Updates are reviewed/pinned and manually initiated. Failed operations stop
  the affected stack, preserve diagnostics, and offer a guided restore rather
  than silently rolling back.
- Vaultwarden is excluded from MCP exposure. MCP capabilities are curated and
  policy-bound, with read-only defaults.
- SurfSense is tracked as blocked until it has a supported native SSO or
  trusted-identity integration; Mu3Lab will not fork it or replay passwords.

## Current development state

The bootstrapper and registry-backed dashboard foundation are active work.
`services.yaml` is the deployment source of truth and `catalog.yaml` is the
user-facing curated catalog. Authentik and Vaultwarden Compose definitions are
included, while the first complete AI slice (Ollama, LiteLLM, and Open WebUI)
is still being brought in.

## Developer checks

```bash
./install.sh --no-open
make test
cd dashboard && npm ci && npm run build
```

The CI workflow runs the Python suite, dashboard build, and YAML validation.

## Storage and backups

```text
/srv/mu3lab/
├── data/
├── backups/
├── secrets/
├── runtime/
└── projects/
```

Local encrypted Restic backups use a default retention policy of 7 daily,
4 weekly, and 12 monthly snapshots. A restore must always be explicitly
confirmed.

## License

Mu3Lab is licensed under the AGPL-3.0-or-later. Individual curated apps retain
their own licenses and operational requirements.
