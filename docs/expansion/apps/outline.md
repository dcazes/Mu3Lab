# Outline (task 5-04)

Household wiki and notes: shared collections (recipes, house manual, plans)
plus private documents per person. Chosen over Joplin and Trilium (see
[../01-decisions.md](../01-decisions.md) §2.1).

## 1. VERIFY list

- Latest stable release; image (`docker.getoutline.com/outlinewiki/outline:<ver>`
  or `outlinewiki/outline` on Docker Hub; VERIFY the official one); digest;
  arm64.
- **Licence:** BSL 1.1 (reported). Read the "Additional Use Grant" for the
  pinned version. Self-hosting for your own household must be allowed. If it
  is not, STOP.
- Required env: `NODE_ENV=production`, `URL`, `PORT`, `SECRET_KEY` (32-byte
  hex), `UTILS_SECRET`, `DATABASE_URL`, `REDIS_URL`, `PGSSLMODE=disable`,
  `FILE_STORAGE=local`, `FILE_STORAGE_LOCAL_ROOT_DIR`,
  `FILE_STORAGE_UPLOAD_MAX_SIZE`, `FORCE_HTTPS` (false behind our proxy),
  `ENABLE_UPDATES=false` (no phone-home), `DEFAULT_LANGUAGE`.
- OIDC env: `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_AUTH_URI`,
  `OIDC_TOKEN_URI`, `OIDC_USERINFO_URI`, `OIDC_LOGOUT_URI`,
  `OIDC_USERNAME_CLAIM`, `OIDC_DISPLAY_NAME`, `OIDC_SCOPES`, and whether
  `OIDC_ISSUER_URL` (discovery) is supported in the pinned version. Redirect
  path `/auth/oidc.callback`.
- First-user behaviour: the first person to sign in creates the workspace
  and becomes **admin** (reported). Later users join as members or viewers
  (a workspace setting; VERIFY the default role and whether new users need
  approval or an allowed email domain).
- Allowed-domain handling: Outline may restrict sign-ups to email domains.
  Household emails vary (gmail, etc.). VERIFY how to allow any email from
  our OIDC provider (Authentik already restricts who can sign in).
- Data path inside the container and its UID (reported `nodejs`, UID 1001).
- Health endpoint (`/_health`, VERIFY).
- **MCP:** does the pinned Outline ship an official MCP endpoint (reported
  in recent versions; VERIFY path, transport, and auth: API key or OAuth)?
  Otherwise, community Outline MCP servers (licence, activity).
- API keys: per-user API keys created in settings or by API
  (`apiKeys.create`, VERIFY), with scopes.

## 2. Services

| Service | Image | Port | Data |
|---|---|---|---|
| `outline` | outline | 3000 → `127.0.0.1:3005` | `data/outline/files` → local file storage dir |
| `postgres` | postgres:<major> pinned | internal | `data/outline/postgres` |
| `redis` | redis:<major>-alpine pinned | internal | none (cache; or `data/outline/redis` if Outline needs persistence; VERIFY) |

Postgres and Redis sit on an internal network only.

## 3. Sign-in and accounts

- `sign_in.method: oidc`, `route.access: open`.
- **Owner first:** `initial_owner_guard` (Authentik admits only the owner
  until verified), as Actual Budget does. The owner's first sign-in creates
  the workspace and makes them admin. Finalisation verifies via the API (with
  a service API key, see below) that the owner is an admin. Then the guard
  lifts.
- **Launch:** Home opens `/auth/oidc` directly (`launch_path`), so the Outline
  login page with its "Continue with Mu3Lab" button is skipped. Redirect
  `/login*` to `/auth/oidc` at the proxy (VERIFY that this does not loop when
  logged out).
- **Household members:** default role **member** (can create private docs
  and edit shared collections). Operators: Outline has no group-claim role
  mapping (VERIFY); if not, a `periodic` hook promotes operators to admin
  via the API. Optional: keep everyone as members except the owner.
  Recommend: **owner admin, operators admin via periodic sync, household
  members**.
- **No email delivery** in Mu3Lab: Outline's email features (invites, digests)
  are off. VERIFY Outline runs without SMTP (it does for OIDC-only setups).

## 4. Service API key (for finalisation, role sync and the connector)

The owner creates nothing by hand. After the owner's first sign-in, Mu3Lab
needs an API key acting as the owner. Options, in order (VERIFY):
1. An Outline management command that creates an API key for a user (none
   known; check).
2. The API key endpoint called **with the owner's session**: not available
   to the server.
3. **Fallback:** the Get started list shows one step: "Chat with your
   notes: in Outline, open Settings → API, create a key named Mu3Lab,
   paste it here." This is a one-time paste, like phone API keys elsewhere.
   **STOP and ask** before choosing (3), because it adds a manual step,
   which the owner dislikes. Present (3) and "no chat connector for
   Outline" as the options.

## 5. Chat connector

Official MCP if VERIFY confirms it: provenance `official`. Otherwise a
reviewed community server or a `mu3lab-adapter` over the API. Categories:
`search` (search docs; core), `documents` (read, create, update; writes off),
`collections` (list). Block: user/admin, API key, export-all,
workspace-settings and delete tools. The assistant sees what its key's user
sees (the owner). Note that people's **private** docs are not visible to the
owner's key. Say so in the assistant description.

## 6. Phone clients

No official native app (VERIFY). The PWA works well: steps to open it on the
phone with Tailscale and Add to Home Screen.

## 7. Backups

`data/outline/postgres` and `data/outline/files`. Stop-snapshot-start.
Redis excluded.

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| O1 | A person without an email in Authentik | Outline needs email. Mu3Lab people always have one (`add_person` requires it). If not, Outline shows an error; documented. |
| O2 | OIDC username claim | Use `preferred_username` so @mentions match Mu3Lab usernames. |
| O3 | Large attachments | `FILE_STORAGE_UPLOAD_MAX_SIZE` (e.g. 100 MB); route timeout. |
| O4 | Collaborative editing (WebSockets) | Two browsers edit the same doc through the route; both see changes (E17). |
| O5 | Workspace creation race (two people sign in at once on first install) | Prevented by the owner guard. |
| O6 | Reinstall with kept data | The workspace exists; owner sign-in works; no second workspace. |

## 9. Acceptance (VM)

Standard journey plus: the owner creates a shared collection and a private
doc; a household member sees the collection, not the private doc; real-time
co-editing works; chat search returns a doc.
