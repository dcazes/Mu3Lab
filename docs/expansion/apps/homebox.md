# HomeBox (task 5-01)

Household inventory: what you own, where it is, receipts, warranties,
manuals. **One shared household inventory** (owner decision).

Use the maintained fork **sysadminsmedia/homebox**. The original
`hay-kot/homebox` is archived; never use it.

## 1. VERIFY list

- Latest stable release; image `ghcr.io/sysadminsmedia/homebox:<ver>`;
  digest; arm64 availability; licence (AGPL-3.0 reported).
- Internal port (7745), data path (`/data`), process UID.
- OIDC env (reported): `HBOX_OIDC_ENABLED`, `HBOX_OIDC_ISSUER_URL`,
  `HBOX_OIDC_CLIENT_ID`, `HBOX_OIDC_CLIENT_SECRET`, `HBOX_OIDC_SCOPE`,
  `HBOX_OIDC_AUTO_REDIRECT`, `HBOX_OIDC_VERIFY_EMAIL`,
  `HBOX_OIDC_AUTO_CREATE` (VERIFY name), group/role options,
  `HBOX_OPTIONS_ALLOW_LOCAL_LOGIN`, `HBOX_OPTIONS_ALLOW_REGISTRATION`,
  `HBOX_OPTIONS_TRUST_PROXY`. Redirect URI path (`/api/v1/users/login/oidc/callback`
  or similar; VERIFY).
- **Sharing model (critical):** HomeBox historically gives each new user a
  new "group" (newer versions may call it "collection"). How does a user join
  an existing group: invitation tokens (`POST /api/v1/groups/invitations`),
  registration with a token, or a multi-collection feature? Does any of
  that work for **OIDC-created** users? Can an admin add an existing user to
  a group by API?
- How the first user is created and whether it becomes an admin/owner.
- API authentication for automation (bearer token from login? API tokens?).
- Health endpoint (`/api/v1/status`, VERIFY).
- Any community MCP server for HomeBox (search GitHub, glama.ai, the MCP
  registry). Its licence and activity.

## 2. Services

| Service | Image | Port | Data |
|---|---|---|---|
| `homebox` | sysadminsmedia/homebox | 7745 → `127.0.0.1:7745` | `data/homebox/data` → `/data` |

SQLite inside `/data` (default). No separate database.

## 3. Manifest sketch

```yaml
id: homebox
capabilities: [home_inventory]
name: HomeBox
tier: optional
group: apps
category: home
tagline: Everything you own, where it is, and its receipts
version: v<VERIFY>
upstream: sysadminsmedia/homebox
service: {local_port: 7745, health: {path: /api/v1/status}}
route: {https_port: 8460, caddy_port: 19477, access: open}
sign_in:
  method: oidc
  oidc:
    client_id: mu3lab-homebox
    redirect_paths: [<VERIFY>]
    launch_path: <VERIFY: path that starts OIDC directly>
    env: {client_id: HBOX_OIDC_CLIENT_ID, client_secret: HBOX_OIDC_CLIENT_SECRET, discovery_url: HBOX_OIDC_ISSUER_URL}
account: {mode: oidc_first_login}
env:
  HBOX_OIDC_ENABLED: 'true'
  HBOX_OIDC_AUTO_REDIRECT: 'true'
  HBOX_OPTIONS_ALLOW_REGISTRATION: 'false'
  HBOX_OPTIONS_TRUST_PROXY: 'true'
  HBOX_OPTIONS_ALLOW_LOCAL_LOGIN: 'false'   # only after owner verified; see §4
rules:
  - rule: initial_owner_guard            # only the owner can sign in until ownership is verified
  - rule: password_login_off_after_setup # VERIFY fit; else staged env
```

Exact field names follow `ctl/manifest/models.py`; copy the OIDC shape from
`apps/mealie/app.yaml`.

## 4. Sign-in and the shared household

1. Owner first: `initial_owner_guard` admits only the owner until their
   HomeBox user exists. The owner's first OIDC login creates the user **and
   the household group**. Verify by API: the user exists, is linked to OIDC,
   and owns a group. Then the guard lifts.
2. **Household members join the owner's group.** Implement with the
   mechanism VERIFY found, in this order of preference:
   a. A built-in, documented setting that makes OIDC users join a default
      group. Use it.
   b. An admin/owner API that adds an existing user to a group. Implement
      a `periodic` step in `apps/homebox/hooks.py`: for each new HomeBox user
      not in the owner's group, add them. Authenticate as the owner through a
      **service credential**. VERIFY how: an API token the owner's account
      can mint; if only password login gives tokens and local login is
      disabled, see (d).
   c. Invitation token plus join API usable by an existing user: same
      periodic approach.
   d. None of the above works without keeping local password login enabled
      or editing the database → **STOP** and ask the owner. Options to
      present: keep inventories per person (shares via HomeBox's own
      invites), or allow local login for one hidden service account (gated:
      route redirects `/login` to OIDC, so people never see it).
3. After a member joins the owner's group, verify that their empty personal
   group (if any) is not the default they land in (VERIFY the "active group"
   behaviour). A fresh member's first page must show the household's items.
4. Local login off and registration off after the owner is verified.

## 5. Install sequence

1. Render env (OIDC client from Authentik blueprint), start, wait for health.
2. Apply the OIDC route; `signin_check` passes.
3. Owner guard active; owner opens HomeBox → OIDC → user created.
4. Worker finalisation (existing onboarding flow) verifies the owner, lifts
   the guard and disables local login (restart).
5. Chat connector provisioning (if one exists, §6).

## 6. Chat connector

- If a maintained MCP server exists: review it. Categories: `items`
  (search/read/create/update), `locations`, `labels`, `maintenance`
  (warranties, maintenance entries). Block: user management, group
  management, exports, raw API.
- If none: build a `mu3lab-adapter` connector over HomeBox's REST API with
  ~8 tools: `search_items`, `get_item`, `list_locations`, `list_labels`,
  `items_in_location`, `upcoming_warranties`; writes `create_item`,
  `move_item` (off by default). Core tools ≤ 6.
- Credential: a dedicated API token for the owner's account named
  "Mu3Lab MCP" (VERIFY that tokens exist). The assistant then sees the
  **household** group.

## 7. Phone clients

HomeBox has no official native app (VERIFY; community apps may exist). Use
the responsive web UI as a PWA: "Open HomeBox in your phone's browser,
sign in, then Add to Home Screen". QR-code labels (HomeBox prints asset
labels) open item pages on the phone; this works when the phone is on the
tailnet.

## 8. Backups

`data/homebox/data` (SQLite plus attachments: receipts, photos, manuals).
No excludes unless VERIFY finds a cache folder. Stop-snapshot-start is fine.

## 9. Edge cases

| # | Case | Handling |
|---|---|---|
| H1 | Member signs in before the periodic join runs | They see an empty inventory for up to one worker cycle. Run the join in the OIDC finalisation path too, if HomeBox exposes a hook; otherwise accept and say "refresh in a minute" in Get started. |
| H2 | Owner removed or demoted | The group belongs to the owner's user; never delete that user. Removed people lose access via Authentik. |
| H3 | Two owners (two operators) | Both are in the same group; only the installing owner is the group owner. |
| H4 | Attachment uploads | Large PDFs: test a 50 MB upload through the route. |
| H5 | Label printing / QR URLs | QR codes encode `HBOX_WEB_URL` or the request host (VERIFY); make sure they encode the tailnet HTTPS URL. |

## 10. Acceptance (VM)

Standard journey ([10-acceptance.md](../10-acceptance.md) §3) plus: a second
person sees the owner's items without any manual step; chat lists items in a
location.
