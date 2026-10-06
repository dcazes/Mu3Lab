# 04. App integration playbook

The recipe for adding **any** app. Each app spec in `apps/*.md` gives the
app-specific answers; this file gives the procedure, the decision trees and
the edge-case checklist. Do every step in order. Do not skip a step because
the app spec "seems to cover it".

Reference implementations to read before your first app:
`apps/grocy/` (trusted header, owner adoption script, connector),
`apps/mealie/` (native OIDC, default-account retirement),
`apps/actual-budget/` (OIDC with initial-owner guard),
`apps/freellmapi/` (gate plus vault-saved login, `hooks.py`),
`apps/immich/` (OIDC, two connectors, `hooks.py`).

---

## Step 1. Research and VERIFY (no code yet)

Create `docs/expansion/notes/<task>.md` with these headings and fill each one
**from the exact release you will pin**:

1. **Release and image.** Latest stable release (not pre-release) from the
   upstream GitHub releases page. Image name, tag, **digest** (`docker
   buildx imagetools inspect <image>:<tag>` or `make approve`'s lookup).
   Architectures published (amd64 required; arm64 noted). Licence (must be
   OSI-approved, or BSL/source-available permitting private self-hosting).
2. **Services.** Every container the upstream **official** compose file uses
   (app, worker, database, cache). Database engine and major version.
3. **Ports and paths.** Internal listening port(s); every data path inside
   the container; which paths are user media versus app data.
4. **Process user.** UID/GID the app runs as, and whether it needs
   `PUID/PGID`, `user:` or a `chown` of its data folder.
5. **Default accounts.** Does a fresh install create any account (admin,
   demo, setup token)? How is it disabled? **This is security-critical.**
6. **First run.** Does the first visitor become admin? Is there a setup
   wizard? Which API, CLI or management command completes it without a
   browser?
7. **Sign-in options.** OIDC (env-based or UI/API-based?), trusted header,
   LDAP, local only. For OIDC: env names, redirect URI paths, whether it
   auto-creates users, whether it maps groups to roles (claim name, expected
   values), whether local login can be disabled, and how existing users are
   matched (email or username).
8. **Phone/desktop clients.** Official or community; how they authenticate
   (OIDC in-app with custom-scheme redirect? API token? username/password?).
   **A client that cannot complete an Authentik browser gate needs either a
   native-OIDC route or a token bypass.**
9. **API for chat.** Official MCP server? Community MCP servers (licence,
   activity, pinned release)? Or a documented REST API to wrap in a
   `mu3lab-adapter` connector?
10. **Health endpoint.** An unauthenticated URL that returns 200 when ready.
11. **Upgrade notes.** Migrations, breaking changes in the last three
    releases, and whether downgrade is possible (usually not; backups
    matter).
12. **Resource needs.** RAM at idle and under load, disk growth, GPU use.

Every item marked **VERIFY** in the app spec goes here too, each with its
evidence. If the app spec and your research disagree, the research wins.
Note the difference; if it changes a decision in [01-decisions.md](01-decisions.md),
**STOP**.

---

## Step 2. Choose the sign-in design (decision tree)

```text
Does the app support OIDC with auto-created users?
├─ yes → Do its phone/desktop clients do OIDC themselves (in-app browser + redirect)?
│        ├─ yes → route.access: open, sign_in.method: oidc         (Audiobookshelf, Dawarich, RomM, HomeBox, Outline, Open WebUI)
│        └─ no, they use API tokens → access: open (the app checks tokens itself) and document token setup
├─ no → Does it support a trusted identity header?
│        ├─ yes → access: trusted_header; strip/replace header at proxy; token_bypass for phone API keys if needed  (Grocy, Beaver, Frigate)
│        └─ no → Do its clients need to reach it without a browser (phone app)?
│                 ├─ no  → access: gate (+ audience), account.mode: api_bootstrap, save_login_to_vault: true   (n8n, Kopia UI)
│                 │        or gate only if it has no accounts at all (ComfyUI)
│                 └─ yes → access: open + app's own login, accounts created via official API, logins saved per person  (Home Assistant)
```

Rules that apply to every branch:

- **Owner first.** The installing owner must end up as the app's
  administrator with no login form. Use one of: native OIDC first-login plus
  `initial_owner_guard` (Authentik admits only the owner until ownership is
  verified), an adoption script (Grocy), or an `api_bootstrap` account saved
  to the vault.
- **Never leave a default account alive.** Retire it the moment health
  passes, through the app's own interface. Verify retirement in a test.
- **Disable local login** when SSO is the method, but only **after** verifying
  the owner's OIDC link (`password_login_off_after_setup` rule).
- **Household members** get what [01-decisions.md](01-decisions.md) §2.6
  says. If the app's default differs (for example: each user gets a separate
  space), the app spec says how to fix it. Test it with a second person.
- **Roles:** operators (`mu3lab-operators`) are app admins; household
  (`mu3lab-household`) are normal users. The Authentik `profile` scope
  already emits `groups` containing `mu3lab-operators` and/or `mu3lab-users`
  (`ctl/authentik_blueprints.py`). If the app needs **different role names**,
  use the per-app claim mapping from [05-platform.md](05-platform.md) §10.
- **Custom-scheme redirects** (phone apps, e.g. `audiobookshelf://oauth`) need
  [05-platform.md](05-platform.md) §10 `extra_redirect_uris`.
- **Server-to-server OIDC discovery:** the app container must reach
  Authentik's discovery URL. Use the `discovery_url` field of `OidcEnv` the
  way existing OIDC apps do (read `apps/mealie/app.yaml` and
  `apps/immich/app.yaml`). Never assume the container can resolve the tailnet
  hostname.

---

## Step 3. Write the app folder

```text
apps/<id>/
  app.yaml                    # manifest; see ctl/manifest/models.py for every field
  docker-compose.yml          # pinned images, loopback ports, networks, health checks
  docker-compose.nvidia.yml   # only for GPU apps (see apps/ollama/)
  scripts/                    # container_script rule scripts (idempotent, MU3LAB_* markers)
  hooks.py                    # only if no shared rule fits (class Hooks(AppHooks))
  connectors/<connector-id>/  # connector.yaml, review.yaml, compose, lockfiles
  icon.svg                    # VERIFY where existing icons live (dashboard/src/components/AppIcon.tsx)
```

### 3.1 `docker-compose.yml` checklist

- [ ] `name: mu3lab-<id>`.
- [ ] Every image is `repo:tag@sha256:digest`. Databases pinned to a **major**
      version you will not change without a migration plan.
- [ ] App port published **only** on `127.0.0.1:<local_port>`. Databases and
      caches are **not** published.
- [ ] Networks: `mu3lab_frontend` (and `mu3lab_backend` only if another
      Mu3Lab service must call it, e.g. a connector or n8n). Databases sit on
      a per-app internal network (`internal: true`).
- [ ] Data volumes under `${MU3LAB_DATA_ROOT:-/srv/mu3lab/data}/<id>/...`.
      Media volumes under the media root ([05-platform.md](05-platform.md) §3).
- [ ] `TZ: ${TZ:-UTC}`. Never hard-code a zone.
- [ ] Secrets come from `${VAR:?VAR is required}` so a missing secret fails
      loudly.
- [ ] `restart: unless-stopped`; `security_opt: [no-new-privileges:true]`;
      `cap_drop: [ALL]` plus only proven `cap_add`. Test it: start the
      container and watch for permission errors before declaring it done.
- [ ] Health check for **each** long-running service, with
      `start_period` long enough for first-run migrations (measure it).
- [ ] Database services have their own health checks, and the app uses
      `depends_on: {db: {condition: service_healthy}}`.
- [ ] No `privileged: true`. Device access uses explicit `devices:`
      ([05-platform.md](05-platform.md) §8).
- [ ] `python tools/validate_compose.py` passes.

### 3.2 `app.yaml` checklist

- [ ] `id`, `name`, `tier: optional`, `group`, `category`, `tagline`,
      `summary` in plain words.
- [ ] `version` matches the image tag; `upstream` is `owner/repo`.
- [ ] `service.local_port`, `service.health.path`, `start_timeout_seconds`
      (measured first start × 2), `uses_gpu` if applicable.
- [ ] `route` with ports from the table in [05-platform.md](05-platform.md) §2.
- [ ] `sign_in` and `account` from Step 2. `sign_in.note` explains it in
      plain words.
- [ ] `secrets:` for every generated secret (right `kind` and `length`).
- [ ] `env:` for non-secret values (`{{public_url}}` and friends; VERIFY the
      template variables available in `ctl/engine/template.py`).
- [ ] `configuration:` for owner choices (with defaults and labels).
- [ ] `rules:` (reuse existing rules first; list each with its `with:`).
- [ ] `chat.assistant` and `chat.connectors`.
- [ ] `mobile:` clients with numbered steps and a web fallback.
- [ ] `resource_guidance`, `setup_action`.
- [ ] `depends_on` / soft dependencies ([05-platform.md](05-platform.md) §6).

### 3.3 Scripts and hooks

- Scripts are **idempotent**: running twice changes nothing the second time.
- Scripts print exactly one success marker (`MU3LAB_<APP>_<STEP>_OK`) or
  `MU3LAB_ERROR <reason>`, and never print secrets except through the
  `MU3LAB_OUTPUT key=value` channel the engine already understands (see
  Grocy's `provision.php`).
- Scripts receive data as arguments or stdin, never by string-building shell
  commands.
- `hooks.py` only for logic **no other app shares**. If two apps need it, it
  is a rule in `ctl/rules/` ([../rebuild-handoff.md](../rebuild-handoff.md) §5).

---

## Step 4. Default-account and first-run safety

1. Start the pinned image on an empty volume (container named `<id>-test-…`).
2. List every account that exists before anyone signs in. Use the app's API
   or CLI.
3. Write the retirement (delete, disable, or adopt as owner with a random
   password) as a script or rule step that runs **after healthy, before the
   route goes live**. VERIFY the order in `ctl/engine/install.py`.
4. Test: after install, the default credentials no longer work (HTTP 401 or
   equivalent), both directly on loopback and through the route.

---

## Step 5. Chat connector

Follow [../chat-connectors.md](../chat-connectors.md) exactly, using
`apps/grocy/connectors/grocy-community/` as the model.

1. **Choose:** official MCP endpoint > well-maintained community MCP server >
   `mu3lab-adapter` (a small Mu3Lab-written MCP server over the app's REST
   API, kept in the connector folder). Record why.
2. **Pin:** package version plus lockfile, or image digest. Build locally the
   way Grocy's connector does (`Dockerfile` in the connector folder).
3. **Credential:** a dedicated, named, least-privilege key ("Mu3Lab MCP")
   created through the app's own interface in a `provision` script. Never a
   person's own key. Never an admin password.
4. **Transport:** the gateway speaks Streamable HTTP only (`transport:
   streamable-http`). If the server speaks only stdio or SSE, VERIFY whether
   `platform/mcp-adapter/server.py` bridges it. If not, STOP and ask.
5. **Review:** list **every** tool in `review.yaml` (category, read/write,
   core). Block raw-API, user/admin management, credential, shell, file-system
   and server-settings tools. **No more than 6 core tools.** Writes start off.
6. **Assistant:** `chat.assistant.instructions` says what to ask before
   changing data, and to use only returned IDs.
7. **Test:** the connector's `tools/list` returns exactly the reviewed,
   non-blocked tools; one read tool returns real data from seeded test
   content; one write tool is refused while off.

---

## Step 6. Phone and desktop clients

For each client: name, kind, support level, platforms, install links (HTTPS),
`setup` type, numbered `steps` written for a beginner, and `caveat`. Always
include a web or PWA fallback. Mention **Tailscale must be connected** (and
**always-on** for background sync apps such as Dawarich and the Home Assistant
Companion app).

---

## Step 7. Backups

Fill this in the app spec's notes and in the manifest (fields from
[06-kopia.md](06-kopia.md) §4.3):

- Which folders hold **data** (included), **media** (excluded unless the owner
  opts in), **cache/regenerable** (excluded: thumbnails, transcodes, model
  files, search indexes).
- Consistency: the default is stop, snapshot, start (the current engine
  behaviour). Measure stop and start time. If the app is disruptive to stop
  (Home Assistant), the spec says so and the schedule respects a quiet window.
- Restore test: create data, back up, change data, restore, confirm the
  original data is back and sign-in still works.

---

## Step 8. Updates, uninstall and reinstall

- `make approve` must be able to move the app's image(s). If tags do not
  follow versions, document the `IMAGES=` form in the spec.
- **Uninstall keeping data**, then reinstall: the same owner, no duplicate
  admin, connectors re-provisioned (reusing keys), routes back.
- **Uninstall deleting data**: the data folder, the onboarding record, the
  Authentik provider/application (removal blueprint) and the vault entries
  Mu3Lab created are all gone. **Media folders are never deleted** by
  uninstall. Say so in the uninstall dialog text.
- Reinstall after delete behaves as a fresh install.

---

## Step 9. Tests (automated)

Add `tests/test_<id>.py` covering at least:

1. The manifest loads and validates; ports are unique (the catalog test
   already checks this; make sure your app is in it).
2. The rendered Compose project validates.
3. The generated Caddy block: identity header replaced or stripped, gate
   present or absent as designed, bypass paths only with the header.
4. Each script: success marker, idempotent re-run, bad input gives
   `MU3LAB_ERROR`. Run in the pinned image if the existing tests do so for
   Grocy; otherwise plumbing tests plus a recorded manual run.
5. The review covers every tool the pinned connector offers (a snapshot of
   `tools/list` committed as a fixture).
6. Any new rule or route feature gets its own unit tests in its own task.

---

## Step 10. Edge-case checklist (answer each one in the notes)

| # | Edge case | Expected handling |
|---|---|---|
| E1 | Install interrupted at each stage, then retried | Resumes; no duplicate owner; generated password reused (onboarding state). |
| E2 | Owner signs in for the first time on a phone | Works, or is documented as a browser-only first step. |
| E3 | Second household member, before and after chat is connected | Gets the designed access; connector still provisions (Grocy D1 lesson). |
| E4 | Person removed in Settings → People | Loses access through Authentik; app account disabled where we created it (HA). |
| E5 | Person's role changes (household ↔ operator) | App role follows on next sign-in (claim mapping) or periodic sync. |
| E6 | Forged identity header from another tailnet device | Replaced or stripped at the proxy; test proves it. |
| E7 | App's own login page URL visited directly | Redirected to SSO (`route.redirects`) or harmless. |
| E8 | Default account credentials tried after install | Rejected. |
| E9 | Container restarted mid-request; health flaps during first-run migrations | `start_period` covers it; the install waits. |
| E10 | Disk full during install or backup | Clear error with code; nothing half-written left marked "done". |
| E11 | Upstream image deleted or retagged | The digest pin keeps working while cached; `make approve` shows the change. |
| E12 | App update with a database migration, then restore of the pre-update backup | Restore puts back the previous release too (existing behaviour); test once per app with a DB. |
| E13 | Uninstall with kept data, reinstall a **newer** approved version | Migrations run; data intact. |
| E14 | Tailscale hostname changes (machine renamed) | Public URL env values re-render on repair/update; VERIFY that the engine re-renders `{{public_url}}`. |
| E15 | Time zone or locale not set on the host | `${TZ:-UTC}` default; nothing hard-coded. |
| E16 | Large uploads (audiobooks, ROMs, attachments) | No proxy body limit blocks them (VERIFY Caddy and Tailscale Serve limits); the timeout is long enough. |
| E17 | WebSockets (HA, n8n, ComfyUI, Open WebUI, Outline) | Work through Caddy and Tailscale Serve; tested. |
| E18 | Connector tools change in a new connector release | Unreviewed tools stay hidden; the review is updated in the approve step. |
| E19 | App stopped while chat calls its tool | The gateway returns a plain "app is stopped" message. |
| E20 | VM without GPU (acceptance) for a GPU app | CPU fallback path works (slowly), or the install is blocked with a clear reason. |

---

## Step 11. VM acceptance

Run the per-app journey in [10-acceptance.md](10-acceptance.md) §3 in the
disposable VM. Never on the owner's stack.

## Step 12. Validation record

Create `docs/<id>-validation.md` modelled on [../grocy-validation.md](../grocy-validation.md):
status line with date; "Implemented" bullets; "Checks performed" table
(check → result); "Live acceptance" list with pass/fail or "pending (reason)".

## Step 13. Definition of done (per app)

- [ ] Every VERIFY recorded with evidence; no unresolved STOP.
- [ ] Manifest, compose, scripts, connector and review committed; tests added.
- [ ] `make verify` and `tools/validate_compose.py` pass.
- [ ] Every edge case in Step 10 answered (handled or explicitly "not
      applicable because …").
- [ ] VM journey passed, or failures recorded with the task marked blocked.
- [ ] Validation record written; README status updated; owner told what to
      test.
