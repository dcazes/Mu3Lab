# n8n (task 5-08)

Workflow automation that connects household apps (and outside services
reachable from the server). **Tailnet only; no public webhooks** (owner
decision). **Owner/operators only**: n8n can run code and reach internal
services, so it is an admin tool.

## 1. VERIFY list

- Latest stable release (2.x at planning time); image
  `docker.n8n.io/n8nio/n8n:<ver>` (or `n8nio/n8n`); digest; licence
  (**Sustainable Use License**: free for internal use; record it); arm64.
- **Task runners:** n8n 2.x runs Code-node JavaScript/Python in task runners.
  Is the external runner sidecar (`n8nio/runners:<same ver>`) recommended or
  required in the pinned version? Env (`N8N_RUNNERS_ENABLED`,
  `N8N_RUNNERS_MODE=external`, `N8N_RUNNERS_AUTH_TOKEN`, broker address). Use
  the upstream-recommended production setup.
- Database: Postgres (recommended for production), env
  `DB_TYPE=postgresdb`, `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_DATABASE`,
  `DB_POSTGRESDB_USER`, `DB_POSTGRESDB_PASSWORD`.
- Core env: `N8N_ENCRYPTION_KEY` (**critical**), `N8N_HOST`,
  `N8N_PROTOCOL=https`, `N8N_PORT=5678`, `WEBHOOK_URL`, `N8N_EDITOR_BASE_URL`,
  `N8N_PROXY_HOPS=1`, `GENERIC_TIMEZONE`, `TZ`, `N8N_SECURE_COOKIE`,
  telemetry off (`N8N_DIAGNOSTICS_ENABLED=false`,
  `N8N_VERSION_NOTIFICATIONS_ENABLED=false`,
  `N8N_PERSONALIZATION_ENABLED=false`, `N8N_HIRING_BANNER_ENABLED=false`),
  `N8N_TEMPLATES_ENABLED` (keep on; owner can browse templates).
- **Owner setup without a browser:** on a fresh instance, the editor asks to
  create the owner. The REST call behind it (`POST /rest/owner/setup` with
  email, firstName, lastName, password; VERIFY) is n8n's own setup endpoint.
  Allowed under "official interfaces" in the same way FreeLLMAPI's bootstrap is.
- **Public API key** creation for the owner (`POST /rest/api-keys` with a
  session cookie, or a CLI command; VERIFY scopes and expiry options).
- **Built-in MCP server** (reported in n8n ≥ 2.18.4 in all editions): enable
  path (Settings → MCP; is there an env or API to enable it?), endpoint path,
  auth (bearer token / API key), transport (Streamable HTTP?), and the
  per-workflow "available in MCP" switch.
- Webhook paths: `/webhook/*`, `/webhook-test/*`, `/webhook-waiting/*`, and
  the form path (`/form/*`).
- Health: `/healthz` (and `/healthz/readiness`; VERIFY).

## 2. Services

| Service | Image | Port | Data |
|---|---|---|---|
| `n8n` | n8nio/n8n | 5678 → `127.0.0.1:5678` | `data/n8n/home` → `/home/node/.n8n` |
| `runners` (if VERIFY says so) | n8nio/runners | internal | none |
| `postgres` | postgres:<major> pinned | internal | `data/n8n/postgres` |

Networks: `mu3lab_frontend` (egress for outside APIs) and
**`mu3lab_backend`** so workflows can call other Mu3Lab apps' internal APIs
with their API keys. This is powerful, which is why n8n is operators-only.
Record this in the manifest comment.

## 3. Sign-in and accounts

- `route.access: gate`, `audience: operators`; `sign_in.method: gate`.
- `account: {mode: api_bootstrap, save_login_to_vault: true}`. The owner
  account is created by the setup call with the **owner's email** and a
  random password, saved to the owner's vault with URL
  `https://<host>:8466` (port included) so Bitwarden fills it.
- Other operators: an operator can invite them inside n8n (n8n user
  management needs email delivery for invites; VERIFY whether an invite link
  can be copied without SMTP). Optional; not automated in this task. Record it.
- `N8N_ENCRYPTION_KEY`: generated secret. **Also queue it to every
  operator's vault** as "n8n encryption key". Without it, saved workflow
  credentials are unrecoverable after a restore onto a new machine. It is
  also inside the backup of `/home/node/.n8n/config` (VERIFY), but the vault
  copy is the safety net.

## 4. Webhooks on a tailnet-only, gated route

Tailnet devices (Home Assistant, phones, scripts) must reach webhooks without
an Authentik browser session:

- `route.app_auth_paths: ["/webhook/*", "/webhook-test/*", "/webhook-waiting/*", "/form/*"]`
  ([../05-platform.md](../05-platform.md) §5.3). n8n's webhook node then must
  use **its own authentication** (header auth or basic auth). Add to the
  assistant instructions and the app page: "Webhooks are reachable by any
  device on your tailnet. Always set Header Auth on webhook nodes."
- No Funnel. Nothing public.
- Home Assistant (host network) calling n8n: use the tailnet URL
  `https://<host>:8466/webhook/...`, or `http://127.0.0.1:5678/webhook/...`
  from the host network. Document the loopback form for HA.

## 5. Chat connector

- The **built-in n8n MCP server** if VERIFY confirms Streamable HTTP plus a
  token: provenance `official`. Credential: an n8n API key named
  "Mu3Lab MCP" created by `provision`. Review every tool: listing and
  running **MCP-enabled** workflows (run = write, off by default),
  creating/editing workflows (**blocked**: an assistant must not write
  automation that runs code).
- If the built-in server's tools cannot be limited enough, or it is SSE-only
  without a bridge: STOP and ask (options: community `n8n-mcp`, or no
  connector).

## 6. Phone clients

None needed (web editor on desktop). Web fallback only.

## 7. Backups

`data/n8n/home` and `data/n8n/postgres`. Stop-snapshot-start. Executions
history can grow large: set `EXECUTIONS_DATA_PRUNE=true`,
`EXECUTIONS_DATA_MAX_AGE=336` hours (14 days; VERIFY names).

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| N1 | Encryption key mismatch after restore | Restore brings back `.n8n/config` with the key; the env key must match it. The engine keeps the generated secret across reinstall-with-data. Test: back up, uninstall keeping data, reinstall, credentials still decrypt. |
| N2 | Workflows calling internal app URLs | Document internal hostnames (compose aliases) on the n8n app page. |
| N3 | Long-running executions during stop | Stop waits for graceful shutdown (`N8N_GRACEFUL_SHUTDOWN_TIMEOUT`, VERIFY); backups use it. |
| N4 | Licence nag or feature gates | Community edition; no license key. Hide upgrade banners via env where possible. |
| N5 | WebSockets/SSE for the editor push connection | Works through the route (`N8N_PUSH_BACKEND`, VERIFY default). |
| N6 | Workflow with a webhook without auth | Not preventable by Mu3Lab; the warning text covers it. |

## 9. Acceptance (VM)

Standard journey (as an operator; a household member gets 403 from the gate)
plus: a workflow with a Header-Auth webhook is triggered by `curl` from
another tailnet machine **without** an Authentik session (works with the
header, 401/403 without it); a workflow calls Grocy's internal API with an
API key; chat lists MCP-enabled workflows.
