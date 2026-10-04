# Rebuild handoff: how to continue the architecture rebuild

Written 2026-10-03 for the next implementer (person or AI model). Read this
whole file before touching code. It complements `docs/rebuild-plan.md` (the
why and the end state); this file is the **current state and the exact next
steps**. When the two disagree, this file wins: it records decisions made
after the plan was written.

---

## Current checkpoint — Task G implemented; Tasks H–J remain

The owner authorized the remaining implementation, then asked to review the
order and limit simpler work to normal releases. Dependency review confirms
G → H → I → J: I should use H's consolidated state, and final docs follow both.
Clarification is pending on whether to stop after G or continue H–J.
**The rebuild is unfinished; do not reinstall from this branch yet.**

Task G uses a pinned Node container for UI preview/check/build commands,
removes host Node installation and removal, and moves Docker before the UI
build. Normal release tags publish a verified dashboard archive and immutable
image manifest through CI; automatic updates ignore preview tags and branch
commits. Release runtime projects replace local builds with published digests.
See `docs/development.md` for commands and first-release acceptance.

Issues resolved in G: three connector images lacked npm lock files; Firecrawl's
existing Axios dependency had high-severity advisories, fixed with a pinned
1.20.0 override while retaining the connector version. All three locked image
builds and launch checks pass. Python suite: 636 tests, four expected skips;
93 dashboard tests and its build passed inside the pinned container. Lint,
formatting, mypy, shell parsing, the app-ID checker and 26 Compose checks pass.
The actual GitHub publishing/download path and arm64 builds have not run;
they require the first release. No release, tag or push was created.

The completed D–F checkpoint follows:

- D: All generated app routes, login launchers and sign-in checks use manifest
  settings. Removed duplicate core listeners from the base Caddyfile. Shared
  control-plane code discovers platform roles through manifest capabilities;
  an AST check enforces the absence of app-ID literals in `ctl/`.
  Golden route fixtures retain before/after output and explain the differences.
  Connector credentials now use their existing app-owned provision scripts.
- E: Each person approves chat once through the supported device flow. Their
  API key is encrypted and bound to their Authentik subject. Official LobeHub
  APIs synchronize assistants and gateway connectors after app changes and
  every five minutes. Conversations and personal instruction edits survive
  synchronization; stopped/disconnected installed apps retain their assistants.
  Removed all chat SQL, triggers and database activity polling. The supported
  provider/API-key UI flags replace the removed provider guard: upstream has
  no equivalent server setting forbidding all user-supplied keys. This limitation
  is recorded in `apps/lobehub/API.md`.
- F: Pinned standalone Bitwarden CLI 2026.5.0 handles routine vault operations.
  Every session uses a private disposable cache; secrets stay off command
  arguments. A temporary loopback HTTPS bridge is trusted only by that CLI
  process. CLI 2026.9.0 was incompatible with the pinned Vaultwarden 1.37.3
  user-key migration; 2026.5.0 passed the real integration test.
  Registration and organization creation retain minimal bootstrap crypto.
  The admin API sends account invitations. **The owner explicitly approved
  the organization invitation API** because admin invitations cannot add
  organization membership. Member confirmation stays in the official CLI.
  Generated admin credentials use an Argon2 PHC hash.
- Validation: Python suite: 634 tests, four expected integration skips;
  93 dashboard tests passed. Ruff, formatting, mypy, dashboard type/lint/format,
  shell parsing, manifest/rule validation, the app-ID checker, 26 Compose
  configurations and the complete generated Caddy configuration passed.
  LobeHub client tests use MockTransport against the saved pinned OpenAPI spec,
  rather than mutating the live chat. A real disposable Vaultwarden test
  registered two users, created the organization, invited and confirmed the
  member, and verified that member could decrypt a shared login through the CLI.
  Its containers, network and volume were removed afterwards.
- Tests for the removed SQL provider guard were removed with that implementation;
  API, encryption, secret handling, retention and device-flow tests replace
  obsolete tests. Cancellation checks deferred from C now exercise the shared
  Compose wrapper. No tests were disabled.
- No running app, live data, system service, live Docker stack or live checkout
  was changed. The isolated integration runner is
  `.venv/bin/python -m tools.test_vaultwarden`.

Previous checkpoints:
- Task C `7ed1f43`: core apps use the shared generated-project install engine,
  ordered by manifest dependencies, including Firecrawl. Provider routing and
  first-start HTTP bootstrap are app-owned rules/hooks. Tests were deferred at
  the owner's request; the D–F validation above includes these changes.
- Task A `ed2be8c`: Authentik REST/bootstrap and household management, with
  fresh-image integration verification. The default signing certificate is
  **authentik Self-signed Certificate**; the `akadmin` bootstrap API token must
  retain administrator access.
- Task B `545b1d7`: optional-app pipeline, rule callbacks and periodic checks,
  generic sign-in launch pages, account recovery and credential-handoff removal.
  Its unit/static/dashboard/Compose checks passed (622 Python tests, four
  expected skips; 93 dashboard tests).

The WIP inventory below describes the original starting checkpoint. Tasks
A–G are implemented; Tasks H–J remain.

## 0. Where the work is

- Branch: `rebuild/architecture`, in the git worktree
  `/home/dak/Desktop/Mu3Lab/.claude/worktrees/rebuild-architecture`.
  **Always work there.** The main checkout `/home/dak/Desktop/Mu3Lab` runs the
  owner's live install; switching branches there changes what runs on restart.
- Run every command from the worktree with absolute paths. Shell `cd` can be
  reset between commands.
- Python: `.venv/bin/python` (uv-managed Python 3.12.14). Dev tools are in the
  venv: `.venv/bin/ruff`, `.venv/bin/mypy`. If `.venv` is missing run
  `make dev-setup`.
- Dashboard: `cd dashboard && npx tsc --noEmit && npx eslint . && npx vitest run && npx prettier --check src`.
- Full gate before every commit:
  ```
  .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy \
    && .venv/bin/python -m unittest discover -s tests -t . \
    && (cd dashboard && npx tsc --noEmit && npx eslint . && npx vitest run)
  ```
- Commits end with the line `Co-Authored-By: <your model name> <noreply@anthropic.com>`
  (or nothing if you are a person). Do not put model names anywhere else.
- Do not push and do not open a pull request unless the owner asks.
- A throwaway Authentik may still be running from integration tests. Remove it
  when done: `docker compose -f tests/integration/authentik-compose.yml down -v`
  (it is project `mu3lab-test-authentik`, port 19901; it never touches the live install).

### Commits already on the branch

| Commit | What |
| --- | --- |
| `ed2be8c` | Authentik REST bootstrap, sign-in registration and household management (Task A) |
| `8331653` | `docs/rebuild-plan.md` with the owner's decisions |
| `ebe48ec` | uv toolchain (`tools/toolchain.sh`, `uv.lock`, `.python-version` 3.12.14), stale docs deleted, host timezone (`ctl/hostinfo.py`), lint/type fixes |
| `c2bb415` | one folder per app: `apps/<id>/app.yaml` + `ctl/manifest/` (Pydantic models + catalog loader), connectors moved to `apps/<app>/connectors/<id>/`, `platform/` for Mu3Lab's own images, `ctl/registry.py` is now a thin projection of manifests, dashboard uses `group` and backend launch URLs |

### Work in progress (uncommitted when this file was written; committed as a WIP commit right after)

The WIP commit message starts with `WIP:`. Tests are **not** fully green in it
(see section 3). Files:

- `ctl/engine/` – new install engine pieces (not wired in yet):
  `template.py` (`{{name}}` placeholders), `compose.py` (`Compose` class, `run_script`,
  `MU3LAB_OUTPUT`/`MU3LAB_ERROR` protocol), `project.py` (renders a runtime project from a
  manifest; replaces `ctl/lifecycle/materialize.py`), `hooks.py` (`HookContext`, `StartPlan`,
  `StepFailed`, `AppHooks`, `load_app_hooks`), `loopback.py` (JSON requests to an app's port).
- `ctl/rules/` – the shared rule groups (owner's request: app-specific behavior generalized
  into named rules that apps opt into, an app may follow several):
  `container_script`, `first_admin_from_env`, `staged_first_start`,
  `password_login_off_after_setup`, `needs_embedding_model`, `hide_model_providers`,
  `initial_owner_guard`, plus `scripts/django_superuser.py`. `ctl.rules.check(catalog)`
  validates every manifest's rules and their files.
- `ctl/integrations/authentik.py` – Authentik REST client (API token, blueprints via the
  synchronous **import** endpoint, users, groups, set_password, recovery links,
  `password_hash()` for `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH`).
- `ctl/authentik_blueprints.py` – **rewritten** as pure renderers with no app ids:
  `render_gate_blueprint(host, dashboard_port, gated)`, `render_oidc_blueprint(host, OidcApp)`,
  `render_removal_blueprint(id, name, oidc=)`, `GatedApp`, `OidcApp`, blueprint name helpers,
  and `WELCOME_FLOW` (household invite links). The old functions
  (`write_dashboard_blueprint`, `render_dashboard_blueprint`, `write_oidc_application_blueprint`,
  `write_removal_blueprint`, `clear_removal_blueprint`, `render_oidc_application_blueprint`)
  **no longer exist**; their callers are listed in section 3 and must be ported.
- `ctl/manifest/models.py` – added `Route.audience` (household|operators), `Ui.label`,
  `HttpsUrl`, connector `secrets`/`include`, `JsonLaunch.launcher/session_check_path`.
- Manifests: rules added (nextcloud, adventurelog, paperless-ngx, mealie, immich, lobehub,
  actual-budget, surfsense), `audience: operators` on litellm and freellmapi.
- App folders: `apps/nextcloud/scripts/{install.sh,verify-admin.sh,configure.sh}`,
  `apps/adventurelog/scripts/oidc-social-app.py`, `apps/mealie/scripts/adopt-owner.py`,
  `apps/immich/immich-config.json.tmpl`, `apps/immich/hooks.py`, `apps/surfsense/hooks.py`.
  `apps/nextcloud/docker-compose.yml` now passes `MU3LAB_OIDC_*` to the app container.
- `tests/integration/` – `authentik-compose.yml` and `test_authentik_live.py` (runs only with
  `MU3LAB_INTEGRATION=1`).

---

## 1. Decisions already made by the owner (do not re-ask)

1. One clean reinstall is fine; **no data or upgrade path needs keeping**. Delete
   old-install migration code freely.
2. Keep every feature. App-specific behavior stays, but as **named shared rules**
   (`ctl/rules/`) that apps list in `app.yaml`; an app may follow several. App-unique
   logic that no other app shares goes in `apps/<id>/hooks.py` (`class Hooks(AppHooks)`).
   **No module under `ctl/` may name a specific app id.**
3. One Docker/lifecycle engine for every service (core and catalog apps alike), always
   running from generated projects under `/srv/mu3lab/projects/<id>`.
4. Remove Node.js from the host (the "Node fix"): build the dashboard in a pinned Docker
   container for development checkouts, and ship it prebuilt in tagged releases.
   Updates move between **tagged releases**.
5. Postpone other Linux distributions.
6. **Keep LobeChat.** Each person approves Mu3Lab **once** through LobeChat's own official
   sign-in (device-code flow of the built-in `lobehub-cli` OIDC client). Mu3Lab then uses
   LobeChat's official v1 API with a per-person API key. **No SQL or triggers in LobeChat's
   database.**
7. Phones use LobeChat's website added to the home screen (PWA). The experimental native
   LobeHub app is dropped from the phone guide (already done in `apps/lobehub/app.yaml`).
8. Authentik through official interfaces only (REST API + bootstrap env). Vaultwarden
   **hybrid**: official Bitwarden CLI (`bw`) for logins, folders and collections; Vaultwarden's
   admin API for invitations; Mu3Lab's own crypto code only for registering the account and
   creating the organization (nothing official can do those). Keep the automated paths.
9. Explain things to the owner in plain English. The owner reinstalls as soon as a
   turn looks finished, so say plainly when work is still running.

---

## 2. Verified facts you can rely on (researched 2026-10-03)

### Authentik 2026.8.3 (tested against the real image)

- First-start settings (read once, documented "automated install"):
  `AUTHENTIK_BOOTSTRAP_EMAIL`, `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH`, `AUTHENTIK_BOOTSTRAP_TOKEN`.
  With them Authentik creates `akadmin` with that email and password, creates an API
  token with that key, **and marks first-run setup finished** (`Setup.set(True)` in
  `authentik/core/setup/signals.py`). `ctl.integrations.authentik.password_hash()` produces
  an accepted hash; sign-in with the original password works. So the installer no longer
  needs `ak shell` for the owner account, and the plain password is never written to disk.
- Values containing `$` must be single-quoted in env files (Mu3Lab's `runtime_env_text` does this).
- `POST /api/v3/managed/blueprints/{uuid}/apply/` is **asynchronous** (queues a task).
  Use `POST /api/v3/managed/blueprints/import/` (multipart `file`): it validates and applies
  **synchronously** and stores no instance. `Authentik.apply_blueprint` already does this.
- Removing an app = import its removal document. Verified: app, provider, claims mapping
  and owner policy disappear; other apps stay.
- Owner guard (Actual Budget): guarded state has one policy binding at order 10; lifted
  state has the three group bindings at orders 0–2 and the owner policy is removed.
- Household invite links: `POST /core/users/{pk}/recovery/` needs the brand's recovery flow.
  `WELCOME_FLOW` (in the gate blueprint) creates flow `mu3lab-welcome` (choose password → save →
  sign in) and sets it on the default brand. Verified end to end: link → choose password →
  that password signs in. The link the API returns uses the host the API was called on;
  rebuild it as `https://<tailnet host>` + the link's path and query.
- `GET /core/users/?username=…&include_groups=true`, `POST /core/groups/{pk}/add_user/`,
  `PATCH /core/users/{pk}/`, `POST /core/users/{pk}/set_password/` all work with the token.

### LobeHub 2.2.18

- Official API at `/api/v1/openapi.json`. Bearer auth with API keys `sk-lh-…` or an OIDC JWT
  from **LobeHub's own** OIDC provider. Calls act as the key's owner.
- `POST /api/v1/agents` (title, description, systemRole, model, provider, plugins
  `[{identifier, mode: pinned|auto|disabled}]`), `PATCH/DELETE /api/v1/agents/{id}`.
- `POST /api/v1/mcp-servers` (identifier, name, serverUrl, credentials
  `{type: bearer, token}`), `POST /api/v1/mcp-servers/{id}/sync` (discovers tools).
- `POST /api/v1/api-keys` (name, scopes e.g. `agent:read agent:write mcp:read mcp:write`, expiresAt).
- LobeHub's OIDC provider is enabled by env `JWKS_KEY` (an RSA private JWK). Built-in
  client `lobehub-cli` uses the device-code grant (`urn:ietf:params:oauth:grant-type:device_code`),
  public client, scopes `openid profile email offline_access`. The provider's endpoints are
  under `/oidc` (check `/.well-known/openid-configuration` on the LobeHub origin).
- Workspaces exist in the schema but are a paid-cloud beta (`ENABLE_BUSINESS_FEATURES=false`)
  — **do not use them**.
- The web sidebar (desktop and phone browser) lists `agents` directly
  (`packages/database/src/repositories/home`), so API-created agents appear there.
  The API's `createAgent` creates no `sessions` row; only the dropped native app needs those.

### Bitwarden / Vaultwarden

- Official `bw` CLI: can log in, sync, create items/folders, `create org-collection`,
  `confirm org-member`, and `bw serve` gives a local REST API. It **cannot** register an
  account, create an organization, or invite members.
- Vaultwarden admin API (with `ADMIN_TOKEN`) can invite users (`POST /admin/invite`).
- Standalone `bw` binaries are published on GitHub releases (no Node.js needed); pin the
  version and checksum like `tools/toolchain.sh` does for uv.

---

## 3. Known breakage in the WIP commit (resolved by Task A)

Run the full gate. Expected failures and their causes:

1. **Imports of removed blueprint functions.** Callers to port to the new renderers +
   `Authentik.apply_blueprint`:
   `ctl/identity.py` (`reconcile_blueprints`), `ctl/install.py` (lines near
   `_dashboard_blueprint`, `fix_authentik`, `fix_dashboard_protection`),
   `ctl/lifecycle/materialize.py` (`_register_oidc_client`), `ctl/lifecycle/uninstall.py`
   (removal), `ctl/identity_reconcile.py`, `ctl/api/__init__.py` (startup reconcile),
   `ctl/service_ops.py`; tests `tests/test_identity.py`, `tests/test_bootstrap_identity.py`,
   `tests/test_uninstall.py`, `tests/test_vault_setup.py`, `tests/test_install.py`,
   `tests/test_app_onboarding.py`.
2. **Integration test `tests/integration/test_authentik_live.py` failed on a fresh
   container** with "Authentik rejected Mu3Lab dashboard access: Created Event; Task
   enqueued". Cause (most likely): on a brand-new Authentik the worker has not yet applied
   the default flows (`default-authentication-flow`,
   `default-provider-authorization-implicit-consent`, `default-provider-invalidation-flow`),
   so `!Find` returns nothing and validation fails. The same blueprint applied fine on a
   container that had been up for a few minutes.
   **Fix:** add `Authentik.wait_for_defaults(timeout=300)` that polls
   `GET /flows/instances/?slug=<slug>` for those three slugs (and
   `GET /crypto/certificatekeypairs/?name=authentik Self-signed Certificate`) until all
   exist; call it before the first `apply_blueprint` in the installer and in `setUpClass`.
   Also print `result["logs"]` fully in the error (filter `log_level` warning/error) so the
   next failure is diagnosable. Then run
   `MU3LAB_INTEGRATION=1 .venv/bin/python -m unittest tests.integration.test_authentik_live -v`
   until all three tests pass. Do not loosen the assertions.

---

## 4. Remaining tasks, in order

Each task: do it, run the full gate, commit. Keep commits focused. Never skip or
disable a test; delete a test only together with the behavior it covered, and say so in
the commit message.

### Task A — Finish Authentik on official interfaces (complete)

1. Add `wait_for_defaults` (section 3.2) and make the integration test green.
2. Rewrite `ctl/identity.py` to be manifest-driven, no app ids:
   - `mode_for(service)`: map `manifest.sign_in.method` → `oidc→native_oidc`,
     `trusted_header→trusted_header`, `gate→proxy_gate`, `local→local`, `none→none`.
   - `authentik_only(app_id)`: method in {oidc, trusted_header}.
   - `launch_path(app)`: render `manifest.sign_in.oidc.launch_path` with
     `ctl.engine.template.render` and the app's project `.env` (Nextcloud's
     `{{NEXTCLOUD_OIDC_PROVIDER_ID}}`; default `1` comes from its `secrets` entry).
   - `sync_sign_in(catalog, host, authentik, paths)`: build `GatedApp` for every app whose
     `route.access` is gate or trusted_header and that is installed (tier != optional, or
     `projects/<id>/docker-compose.yml` exists); apply the gate blueprint; for every installed
     app with `sign_in.method == oidc`, read client id/secret from its project `.env`
     (keys from `manifest.sign_in.oidc.env`), owner from `oidc.initial_owner_env`, and apply
     `render_oidc_blueprint`. Return the ids applied.
   - `remove_sign_in(app, authentik)`: apply the removal document; for gated apps re-run
     `sync_sign_in` first so the outpost drops the provider.
   - Delete `OIDC_CONTRACTS`, `GATED_APPS`, `TRUSTED_HEADER`, `PROXY_GATE`, `LOCAL`, `NO_UI`,
     `OIDC_LAUNCH_PATHS`, `reconcile_blueprints`, and `ctl/authentik_apply.py`.
3. Installer (`ctl/install.py`): Authentik project `.env` gets
   `AUTHENTIK_BOOTSTRAP_EMAIL`, `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH` (from the password the
   terminal installer already collects) and `AUTHENTIK_BOOTSTRAP_TOKEN` (random 48 chars,
   kept: `ctl.integrations.authentik.api_token()` reads it) **before Authentik's first
   start**. After Authentik is healthy: `wait_for_defaults`, then
   `update_user(akadmin, name=<owner name>)`, then remove `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH`
   and `AUTHENTIK_BOOTSTRAP_EMAIL` from the `.env` (keep the token). Delete
   `actions.authentik_set_owner`, `actions.reset_authentik_admin_password`, the
   `_authentik_initial_setup_pending` logic and the `/blueprints/custom` volume and
   `AUTHENTIK_BLUEPRINTS_DIR` in `apps/authentik/docker-compose.yml`. Replace
   `ctl/secrets.ensure_authentik_env` accordingly (keep `AUTHENTIK_SECRET_KEY` and
   `AUTHENTIK_POSTGRESQL__PASSWORD` generation; drop `AUTHENTIK_TAG`).
   Dashboard protection step: `sync_sign_in` instead of writing files.
4. Rewrite `ctl/people.py` on the REST client: list = users in groups
   `mu3lab-operators`, `mu3lab-household`, `authentik Admins` (exclude usernames starting
   `ak-`); add = create user (username derived from email as today, suffix on clash), add to
   group, recovery link; role change, deactivate (`PATCH is_active=false`), reactivate,
   invite. Keep the "at least one administrator" checks in Python. Drop the
   `has_password` field (the API has no equivalent; check the dashboard's
   `PeopleSettings.tsx` and remove its use). Delete the `_SCRIPT` shell code.
5. Grep must be empty afterwards: `grep -rn "ak shell\|docker\", \"exec\", \"-i\", \"mu3lab-authentik" ctl`.
6. Update tests listed in section 3.1; add unit tests for `sync_sign_in` using an
   `httpx.MockTransport` passed as `Authentik(transport=…)`.

### Task B — Complete: the install pipeline (replace `service_ops._install` and `materialize`)

Create `ctl/engine/install.py` with `run_install(store, state, job, service, registry, actor, root)`
that performs these steps in order, using `StepFailed` for every failure
(`stage`, `code`, `message` — keep today's stage names and error codes, the dashboard shows them):

1. `validate_service`: optional apps only (until Task C); required configuration present
   (`ctl.service_config.missing_required`); owner identity present when
   `account.mode in {environment_bootstrap, api_bootstrap}` or any rule's `needs_owner`.
2. Remember owner (`onboarding_state.remember_owner`), set initialization/installation states
   exactly as `_install` does today.
3. `materialize_runtime`: `ctl.engine.project.render(app, Facts(dns_name, RuntimePaths(), catalog),
   hooks=[rule.prepare_env bound with owner for each rule])`; then
   `app_releases.align`. Delete `ctl/lifecycle/materialize.py` and `ctl/login_launch.py` at the end of the task.
4. `before_start` hooks of every rule (e.g. embedding model).
5. `validate_configuration`: `Compose.validate`.
6. `pull_images` (`service_ops._download_images` → move into the engine) and
   `resolve_digests` (`app_releases.pin`).
7. If `sign_in.method == oidc`: `identity.sync_sign_in` then
   `signin_check.wait_for_provider(host, app.id)`.
8. `start_service`: build a `StartPlan`, call every rule's `plan_start`, then
   `compose.up(wait_seconds=None if not plan.wait else service.start_timeout_seconds,
   services=plan.services, env=plan.env, extra=plan.extra_files, recreate=prior_install)`.
   Retry once without recreate when the failure code is `dependency_unhealthy` (see
   `service_ops._failure_code`).
9. Every rule's `after_start`.
10. If `plan.needs_final_start`: `compose.up(wait_seconds=timeout, recreate=bool(plan.extra_files or plan.env))`
    with no service filter (this starts everything and drops first-start settings).
11. If `account.mode == api_bootstrap`: `load_app_hooks(app).bootstrap_account(ctx)`.
12. `verify_application`: `lifecycle.health.wait_healthy`.
13. Every rule's `after_healthy`.
14. `configure_route`: `routes.apply` (rewritten in Task D).
15. `verify_sign_in`: `signin_check.verify_sign_in` (rewritten in Task D).
16. Record installed/identity state; `onboarding_state.discard_password` when
    `mode_for` is native_oidc or trusted_header; chat sync (assistants + connector
    `preenable` + `sync_application`); `onboarding_state.mark_configured`; succeed.

`HookContext.rerender` must call `project.render` again; `reregister_sign_in` must call
`identity.sync_sign_in`. Also run each rule's `periodic` for installed apps from the worker
every 60 seconds (replaces `ctl/identity_reconcile.py`, delete it).

Then delete from `ctl/service_ops.py`: `_install`, `_repair`, `_configure_identity`,
`reset_failed_application`'s app-specific env cleanup (keep a generic: remove
`MU3LAB_BOOTSTRAP_*` keys), the `repair` and `configure_identity` actions (also remove
`repair` from `dashboard/src/api/types.ts` and `useServiceActions.ts`), every
`if service.id == …` branch (start/restart re-run `container_script` rules marked to
re-run? — no: only AdventureLog re-registered its SocialApp on start; that is now covered
because the rule runs at install and the setting is persisted in its database; drop it),
and `ctl/lifecycle/accounts.py`, `ctl/lifecycle/integrations.py`, `ctl/lifecycle/onboarding.py`,
`ctl/lifecycle/nextcloud.py` (their logic now lives in rules, scripts and app hooks).
Credential handoffs (`workflow_secrets.create_handoff`, `credential_handoffs` table,
`/api/v1/credential-handoffs`, reveal UI in `SecuritySettings.tsx`) are unreachable now —
delete them.

Tests: write `tests/test_engine_install.py` with a fake `Compose` (record calls) and a fake
app catalog in a temp `apps/` folder covering: step order, `staged_first_start` +
`first_admin_from_env` (final start recreates without overrides), retry on
`dependency_unhealthy`, owner required, `container_script` output saved to `.env`,
`password_login_off_after_setup` rerender + recreate. Port useful assertions from
`tests/test_service_ops.py`, `tests/test_app_onboarding.py`; delete tests of removed code.

### Task C — Complete: core services through the same engine

- Core apps (ollama, freellmapi, litellm, lobehub, firecrawl) install with `run_install`
  from generated projects, in dependency order (`manifest.depends_on`). Remove
  `registry.APP_INSTALLER_CORE`, `core_setup.INSTALLER_CORE_APPS`, `CORE_ORDER`,
  `project_path`'s lobehub special case, and the `MU3LAB_ENV_FILE`/`MU3LAB_LITELLM_CONFIG`
  env plumbing: compose files use `env_file: .env` and project-relative config files.
- LiteLLM `config.yaml` and FreeLLMAPI `freellmapi.config.json` are produced by
  `ctl/core_wiring.py`; call it from a rule `provider_routing` (new) in `prepare_env` or as an
  app hook, writing into the app's project folder.
- FreeLLMAPI's account bootstrap (`core_setup._provision_freellmapi`) becomes
  `apps/freellmapi/hooks.py` `bootstrap_account`, using its HTTP API only; delete
  `actions.freellmapi_local_setup` (`node -e`). If its API cannot do the first setup when
  it answers 403, record that in the hook docstring with an upstream issue link and keep the
  smallest possible fallback.
- Allowed actions: `tier != optional` apps can start/stop/restart (if `service.stoppable`)
  but never uninstall.
- Delete `ctl/provisioning.py` phases that only mirror install steps if nothing in the
  dashboard needs them (check `SystemSettings.tsx` and `HomePage.tsx` first).

### Task D — Complete: Routes, sign-in checks and launchers from manifests

- `ctl/routes.py`: generate every app route from `manifest.route` (skip `generated: false`):
  `access` open → plain proxy; gate → Authentik forward_auth (+ `copy_identity_headers`);
  trusted_header → gate, then set `trusted_header` from `X-Authentik-Username`
  (keep the comment about Caddy applying deletes after sets); `token_bypass` → handle block
  that strips the trusted header; `blocked_paths` → `respond`; `forwarded_host` host|hostport;
  if `oidc.json_launch.launcher` → generic launcher page at the launch path built from
  `JsonLaunch` (port of `ctl/login_launch.py`, with CSP hash). Remove LiteLLM, FreeLLMAPI and
  LobeChat blocks from `apps/ingress/Caddyfile.authenticated` (now generated) and the
  `# MU3LAB_LOGIN` marker from `apps/lobehub/Caddyfile`. `reconcile_core` publishes every
  installed app route, no app list.
  Write golden tests: render with fixed inputs and compare against today's output for
  mealie, surfsense, baby-buddy, actual-budget, litellm; differences must be explained in
  the test docstring.
- `ctl/lifecycle/signin_check.py`: replace `_JSON_LAUNCH`, the paperless branch and
  `_check_first_run` with `oidc.launch` (redirect|json_post|csrf_form), `oidc.json_launch`
  and `oidc.first_run_checks` (dotted keys in `expect`). Gated apps: method gate/trusted_header.
- After Task D: `python - <<'EOF'` check that no file under `ctl/` contains any app id string
  (`grep -rnE '"(nextcloud|immich|mealie|actual-budget|adventurelog|paperless-ngx|surfsense|lobehub|baby-buddy|firecrawl|freellmapi|litellm|ollama|vaultwarden|authentik|ingress)"' ctl`).
  Allowed exceptions: none. Add this as `tools/check_no_app_ids.py` and run it in `make lint` and CI.

### Task E — Complete: LobeChat through its official API (owner decision 6)

1. `apps/lobehub/app.yaml`: add secret `JWKS_KEY` (new `Secret.kind: rsa_jwk` — generate an
   RSA-2048 private key with `cryptography` and serialize as a JWK JSON string) so LobeHub's
   OIDC provider is enabled. Confirm the env name by reading LobeHub's `src/envs/auth.ts`
   for v2.2.18 (`ENABLE_OIDC` is `!!JWKS_KEY`).
2. `ctl/integrations/lobehub.py`:
   - `start_device_login()` → POST LobeHub's device authorization endpoint with
     `client_id=lobehub-cli`, scopes `openid profile email offline_access`; return
     `verification_uri_complete`, `user_code`, `device_code`, `interval`.
   - `poll_device_login(device_code)` → token endpoint, grant
     `urn:ietf:params:oauth:grant-type:device_code`.
   - With the access token: `POST /api/v1/api-keys` (name "Mu3Lab", scopes
     `agent:read agent:write mcp:read mcp:write`, no expiry). Store the key encrypted per
     person (`provider_secrets`-style store until the single secret store exists) keyed by
     the Authentik uid; discard the OIDC tokens.
   - `ensure_assistants(person_key, apps)`: for each installed app with `chat.assistant`
     and a live connector, upsert an MCP server (identifier `mu3lab-<app>`, serverUrl =
     tool gateway URL for that app, bearer = gateway token), `POST …/sync`, then upsert the
     agent (title, description, systemRole = instructions, model `mu3lab-chat`, provider
     `openai`, plugins `[{identifier: mu3lab-<app>, mode: pinned}]`). Find existing ones by
     listing and matching identifier/title; never create duplicates. Remove agents for
     uninstalled apps only when they have no topics (`GET /api/v1/topics?agentId=…`).
3. Dashboard Chat page: if the person has no key yet, show "Connect chat (one time)" which
   calls a new API route that starts the device login and opens `verification_uri_complete`
   in a new tab (or inside the existing LobeChat iframe); poll until approved; then show
   "Your assistants are ready".
4. Worker: every 5 minutes and after each app install/uninstall, run `ensure_assistants`
   for every connected person.
5. Delete from `ctl/lobehub_ops.py` all SQL (`_agent_sql`, `_mobile_sessions_sql`,
   `_sync_connector`, triggers, `PSQL`, `DATABASE_CONTAINER`) and `ctl/mcp_chat_activity.py`
   (use the tool gateway's own log). The provider guard trigger becomes LobeHub
   configuration: `hide_model_providers` rule already hides providers; also set
   LobeHub's env that disables user-supplied API keys if one exists in v2.2.18 — check
   `src/envs/` (look for `ENABLED_ACCESS_CODE`/`DISABLE_*` style flags) and document what you
   find; if none, accept that and note it.
6. Integration test: throwaway LobeHub cannot easily sign in without Authentik; instead
   unit-test the client with `httpx.MockTransport` using request/response shapes from
   `/api/v1/openapi.json` (save the spec into `tests/fixtures/lobehub-openapi-2.2.18.json`
   from the running instance: `curl -s http://127.0.0.1:3211/api/v1/openapi.json`).

### Task F — Complete: Vaultwarden hybrid (owner decision 8)

- Download a pinned, checksum-verified standalone `bw` binary into `.tools/bin/bw`
  (extend `tools/toolchain.sh`; never `npm install`).
- `ctl/integrations/vaultwarden/`: keep only registration and organization creation from
  `ctl/vaultwarden_api.py`/`ctl/vault_org.py` (move into this package, ≤ 200 lines of crypto);
  invitations via Vaultwarden admin API (generate `ADMIN_TOKEN` as an argon2 PHC string,
  stored in the Vaultwarden project `.env`); everything else (`bw login --apikey` or
  password login with `BW_PASSWORD` env, `bw sync`, `bw create item`, `bw create org-collection`,
  `bw confirm org-member`) via the CLI with `BITWARDENCLI_APPDATA_DIR` set to a private
  temp folder deleted after each job.
- Integration test like the Authentik one: throwaway Vaultwarden (pinned image from
  `apps/vaultwarden/docker-compose.yml`), register owner, create org, save a login, invite and
  confirm a second member, member sees the item.

### Task G — Implemented: Node.js off the host and tagged releases (owner decision 4)

1. Installer: delete the `node` step, NodeSource repository, `MIN_NODE_MAJOR` and the node
   preflight check; `uninstall.sh` no longer removes Node.js.
2. Dashboard build step: if `dashboard/dist/` matches the checkout (release tarball present
   and `stamps.dashboard_digest` matches), skip. Otherwise build inside a pinned container:
   `docker run --rm -v <root>/dashboard:/src -w /src node:24-alpine@sha256:<digest> sh -c "npm ci && npm run build"`
   (resolve the digest with `docker buildx imagetools inspect`), run as the invoking uid
   (`--user $(id -u):$(id -g)`, `-e HOME=/tmp`). Move this step after Docker is installed.
3. Connector images and the tool gateway: every `Dockerfile` that runs `npm install` must
   use a committed `package-lock.json` and `npm ci`. Add `.github/workflows/release.yml`:
   on tag `v*` build and push `ghcr.io/dcazes/mu3lab-<name>` images with digests, build the
   dashboard tarball and `SHA256SUMS`, attach to the GitHub release. Compose files reference
   `image:` (digest) and keep `build:` for development.
4. `install.sh`: if the checkout is at a release tag and the release tarball exists, download
   and verify it into `dashboard/dist`. `ctl/self_update.py`: move only between release tags
   (`git fetch --tags`, newest `v*` tag greater than current, `git checkout <tag>`), not
   branch heads.

### Task H — One database and one secret store

As in `docs/rebuild-plan.md` Phase 5 (unchanged). Do it after Tasks A–G so ported code moves once.

### Task I — Typed API, status snapshot, server-side checklist

As in `docs/rebuild-plan.md` Phases 2.1, 2.2 and 6. The Get started checklist must be stored
on the server (phone and laptop must agree).

### Task J — Docs

- `docs/architecture.md`: one page explaining apps/, manifests, rules, hooks, engine steps,
  integrations, and how to add an app (copy a similar app folder, list rules, run
  `make lint test`).
- `README.md` development table and "How it works" updated.
- `docs/acceptance.md` per the plan, Phase 0.2.
- Delete `docs/rebuild-handoff.md` and `docs/rebuild-plan.md` sections that are done, or
  mark them done.

---

## 5. How to add a new rule (pattern to copy)

1. `ctl/rules/<name>.py` with a `Params` subclass (all settings, `extra="forbid"`), a class
   decorated with `@register`, `name = "<name>"`, a one-line `summary`, and only the hook
   methods it needs. App-specific scripts/queries come in through `Params` and live in the
   app's folder; implement `check_files(app)` when it references files.
2. Import the module in `ctl/rules/__init__.py` `_load_all`.
3. Add it to an app's `rules:` in `app.yaml`.
4. Unit test with a temp app folder and a fake `HookContext` (see the rules' constructor
   signatures in `ctl/engine/hooks.py`).

## 6. Ground rules (from the owner and the plan)

- Professional standards: typed, small functions, no `except Exception: pass`,
  comments say why, every user-facing failure has a plain-language message and a code.
- No function-level imports except the plugin loader in `ctl/rules/__init__.py`; fix
  cycles by moving code down a layer.
- Never print or log secrets; use `ctl.jobs.redact` for any text that may contain one.
- Never run `./uninstall.sh`, `./install.sh` or anything with `sudo` on the owner's
  computer; the owner runs those. Never stop or modify the owner's running containers
  (names without `-test-`).
- When finished with a task, tell the owner in plain English what changed and what to test.
