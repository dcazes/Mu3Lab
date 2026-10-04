# Mu3Lab rebuild plan

Status: Tasks A–J from `docs/rebuild-handoff.md` implemented, 2026-10-04.
This original plan is retained as design history. The handoff records the
accepted scope, substitutions and current checkpoint. Automated checks pass;
real-device, clean-install and published-release acceptance remains pending in
`docs/acceptance.md`. The working stack has not been replaced.

Completed within the approved handoff: common manifest/rule install engine
(A–D), official identity/chat/vault interfaces (A, E, F), container UI tooling
and normal release preparation (G), shared private state (H), API/status/
checklist/UI refactor (I), and documentation (J). Phase 9's additional Linux
distributions, scheduled/off-device backups and other original future scope
are not part of these tasks and are not claimed complete. Detailed acceptance
criteria below remain references until the corresponding manual checks pass.

Audience: (1) the owner, who decides; (2) the engineer or AI model that
implements it, who should be able to follow it task by task without having
read the review conversation.

This document replaces `PLAN.md` and `BUILD_ORDER.md`, which describe an
older design (a browser-based bootstrap page, ARM rejected) and must not be
trusted. Phase 1 deletes them.

---

## Part A. Summary for the owner

Mu3Lab works, and much of it is well built: the durable job queue, the
check-first installer, the security model (proxy token, CSRF bound to the
Authentik session, secret redaction), digest-pinned images, the
approved-version update flow and a disciplined test suite (604 passing tests,
31 seconds).

The problems are structural, not cosmetic. They come from features being
added one at a time without a shared shape for them to fit into:

1. **One app's knowledge is spread over about a dozen files.** Mealie, for
   example, is described in `services.yaml`, its compose file,
   `materialize.py`, `identity.py`, `signin_check.py`, `integrations.py`,
   `service_ops.py`, `lobehub_ops.py`, `mcp_credentials.py`,
   `mcp-catalog.yaml` and the dashboard. The same fact (an OIDC redirect
   path, an image pin) is written in two places and they already disagree.
   Changing one app safely means knowing all of them.
2. **Three different engines start containers** (the installer, `core_setup`
   and `service_ops`), with different rules. Core services run straight from
   the git checkout, so a `git pull` can change a running core service
   without an update ever being approved.
3. **State lives in about ten places**: one SQLite file shared by six modules
   that each create their own tables, a second SQLite file, six JSON files,
   four separately keyed encrypted files written by copy-pasted code, and a
   secrets file inside the git checkout.
4. **Status is recomputed on every page refresh, for every viewer**, by
   running Docker and Tailscale commands and HTTP probes; some states even
   depend on who is looking. The dashboard then re-interprets about 19 state
   words. This is slow and is one reason two screens can disagree.
5. **Mu3Lab reaches inside other apps.** It writes SQL triggers into
   LobeChat's private database, runs Python inside Authentik's shell, and
   reimplements Bitwarden's encryption. Every upstream update can silently
   break these. The "assistants on my computer but not my phone" problem is a
   direct result (Part B explains).
6. **Installs are not reproducible across computers.** Python libraries below
   the top level are unpinned, the dashboard and chat connectors are built on
   each computer with whatever Node.js packages npm resolves that day, the
   timezone is hardcoded to Toronto, and Ubuntu 22.04 is advertised but
   cannot work (its Python 3.10 passes the installer's check, then the code
   crashes on a Python 3.11 feature).

The recommended course is a **staged rebuild in place**, not a from-scratch
rewrite: keep what works, give every app one folder that fully describes it,
run every container through one engine, keep all state in one database with
one secret store, type the API end to end, talk to other apps only through
their official interfaces, and ship tagged releases built once by GitHub.
The order follows your priority: first make every device show the same thing
(Phase 2), then rebuild the foundation (Phases 3–8), then widen to other
Linux distributions (Phase 9).

Your decisions so far (2026-10-03):

| Question | Decision |
| --- | --- |
| How do people get updates? | Tagged releases. GitHub builds the dashboard and connector images once; computers download them. No Node.js on the host. |
| Integrations that reach inside other apps | Keep every feature, including separate per-app assistants and automatic account setup. Find better ways to implement them, including other software if it does the job better. |
| "Same from different devices" | Both: phone vs laptop first, then other Linux distributions. |
| Old-version upgrade code | Leftovers (Part B1). Owner confirmed one clean reinstall; no data needs keeping. |
| App-specific behavior | Keep it, but as named **rules** written once; each app lists the rules it follows and may follow several (Appendix 1, `rules:`). |
| LobeChat | Keep it. Each person approves Mu3Lab once through LobeChat's own sign-in (the official `lobehub-cli` device approval); Mu3Lab then manages their assistants through LobeChat's official v1 API. No database writes. |
| Phones | Use LobeChat's website added to the home screen; the experimental native LobeHub app is dropped from the phone guide. |
| Vaultwarden | Hybrid: the official Bitwarden CLI saves logins and collections; Vaultwarden's admin API invites people; Mu3Lab's own code only creates the account and organization (nothing official can). |
| Node.js on hosts | Removed. The dashboard and connector images are built inside pinned containers (or downloaded prebuilt from a release). |
| Other distributions | Postponed. |

---

## Part B. Answers to your questions

### B1. Is the "old version" code unfinished features or leftovers?

Mostly leftovers from your own uninstall/reinstall testing. It falls into
three groups.

**Leftover upgrade code (safe to delete once you reinstall from scratch).**
Each piece exists only so an install made by an earlier Mu3Lab build keeps
working after a `git pull`:

| Where | What it upgrades |
| --- | --- |
| `ctl/registry.py` (`legacy_proxy`, schema versions 2 and 3) | `services.yaml` files written before ports were declared per app |
| `ctl/provider_ops.py` `migrate_legacy`, the `unsupported_legacy` state in `ctl/control_state.py`, and its branches in `ctl/api/routes/providers.py` | AI provider keys saved in an older format |
| `ctl/provisioning.py` (the `legacy = {...}` phase map) | Setup progress saved under old phase names |
| `ctl/vault_sync.py` (`Older installs recorded the person's email…`) | Vault records keyed by email instead of username |
| `ctl/lifecycle/app_releases.py` `_infer_version` | Release records written before they stored a version |
| `ctl/lifecycle/authentik_storage.py`, `core/authentik/docker-compose.legacy-volumes.yml`, installer step `authentik_storage` | Authentik data kept in Docker volumes before it moved to `/srv/mu3lab/data` |
| `ctl/identity.py` (`legacy ui.url … mixed-version rollouts`) | Dashboard builds older than the API |
| Credential handoffs: `workflow_secrets.create_handoff/metadata/reveal`, the `credential_handoffs` table, `/api/v1/credential-handoffs`, the reveal UI in `SecuritySettings.tsx`, handoff cleanup in `ctl/api/routes/vault.py` and `ctl/worker.py` | The pre-vault flow that showed generated passwords on screen. It also contradicts your 2026-10-01 rule that Mu3Lab never holds human login passwords; saving to the vault replaced it. |
| The `repair` action in `ctl/service_ops.py` and the dashboard types | Your 2026-10-01 decision removed Repair; the backend still offers it |

**Not leftovers (keep).** These handle quirks of other projects, not old
Mu3Lab installs: `vaultwarden_api._camel` (Bitwarden servers answer in two
JSON styles) and SurfSense's second embedding variable name in
`materialize.py`.

**Abandoned design options (unfinished, never used).** The registry accepts
values no app uses: five account modes (`existing_bootstrap`,
`manual_owner`, `browser_registration`, `local_account_manual`, `internal`),
hardware `profiles`, the `blocked` stage and `availability`/`blocked_reason`,
`maturity: planned`, and `VALID_ACTIONS`. The `mcp.tools` lists in
`services.yaml` are an earlier connector design, now only used as
placeholder text before real tool discovery. The `docker` Python library is
installed but never imported.

**One genuinely unfinished feature:** scheduled and off-device backups (the
retention settings exist; nothing runs them on a schedule). It stays on the
roadmap and is out of scope here except where Phase 4 makes it easy to add.

### B2. Why LobeChat showed assistants on the computer but not the phone

Mu3Lab does not ask LobeChat to create assistants. It writes rows straight
into LobeChat's private database and installs database triggers that copy
each assistant into every person's private account. LobeChat 2.2.18's web
app lists assistants from its `agents` table, while its mobile view lists
`sessions`. Mu3Lab originally wrote only `agents`, so the phone showed
nothing. Commit `657daa2` added a second trigger for `sessions`, which fixes
today's version but will break again whenever LobeChat reorganizes its
tables, and it will do so silently.

A second, smaller cause of devices disagreeing: the Home "Get started"
checklist stores some ticks in the browser's local storage
(`dashboard/src/features/home/GetStarted.tsx`), so a step ticked on the
laptop is unticked on the phone.

Phase 2 fixes both properly: assistants become shared objects created
through an official API (so every device and every new household member sees
the same list without copying), and checklist progress is stored on the
server.

---

## Part C. Findings

Each finding lists the evidence, why it matters, and the phase that fixes it.

**F1. App knowledge is scattered and duplicated.** About 57
`if service.id == "<app>"` branches across 14 modules (27 in
`ctl/service_ops.py` alone). OIDC client IDs and redirect paths are defined
in both `ctl/identity.py` (`OIDC_CONTRACTS`) and
`ctl/lifecycle/materialize.py`. Image pins are written in both
`services.yaml` and each compose file and already differ (Mealie has a
digest in compose but not in `services.yaml`). Caddy routes are Python
f-strings with three per-app special cases (`ctl/routes.py`). Chat assistant
text lives in `ctl/lobehub_ops.py`. → Phase 3.

**F2. Three container engines.** `ctl/install.py` starts Caddy, Vaultwarden
and Authentik; `ctl/core_setup.py` starts Ollama, FreeLLMAPI, LiteLLM and
LobeChat; `ctl/service_ops.py` installs catalog apps (and Firecrawl, which
is "always_on" yet installed as optional). Core services run from the git
checkout (`service.compose_path(root)`) while apps run from copies under
`/srv/mu3lab/projects`, so `git pull` changes running core configuration —
against the approved-versions rule. → Phase 4.

**F3. Ten state stores.** `control-plane.sqlite3` is shared by
`jobs.py`, `control_state.py`, `provisioning.py`, `install_batches.py`,
`image_downloads.py` and `app_sizes.py`, each with its own `CREATE TABLE`
and unnumbered `ALTER TABLE` migrations. Also `mcp-activity.sqlite3`;
`bootstrap-state.json`, `backup-verification.json`, `vault-org.json`,
`releases.json`; four Fernet stores with four keys and copy-pasted code
(`provider_secrets.py`, `calendar_secrets.py`, `workflow_secrets.py`,
`onboarding_state.py`); the repo-root `.env` holding the control and ingress
tokens; and per-project `.env` files. "Who owns this app" is tracked in four
places. → Phase 5.

**F4. Status is recomputed per request, per viewer.** `GET /api/v1/services`
runs `tailscale status`, `tailscale serve status`, `docker compose ls/ps` and
an HTTP probe per service, then merges persisted state on top
(`ctl/api/service_view.py`). The dashboard polls nine endpoints every 10
seconds per open screen. Authentik shows "ready" only for operators. The
vocabulary has 19 `state` values plus `installation_state`,
`operational_state`, `lifecycle_state`, `setup_state`, `route_state` and
`health_state`, and the dashboard re-derives more
(`displayStage` hardcodes Firecrawl and LobeChat as "core"). → Phases 2 and 6.

**F5. Per-device state.** Get started ticks in `localStorage`. → Phase 2.

**F6. Reaching inside other apps.** LobeChat: string-built SQL and triggers in
`ctl/lobehub_ops.py` and `ctl/mcp_chat_activity.py`, hardcoded container
`mu3lab-lobehub-postgres-1`. Authentik: Python piped into `ak shell`
(`ctl/authentik_apply.py`, `ctl/actions.py`). FreeLLMAPI: `node -e` inside its
container (`actions.freellmapi_local_setup`). Vaultwarden: a hand-written
Bitwarden protocol client including organization RSA keys
(`ctl/vaultwarden_api.py`, `ctl/vault_org.py`). → Phases 2 and 7.

**F7. Untyped API.** No Pydantic models; every route returns
`dict[str, Any]` and parses JSON by hand; the dashboard keeps 713 lines of
hand-written types (`dashboard/src/api/types.ts`) that nothing checks
against the server. → Phase 6.

**F8. Tangled modules.** 70 flat modules in `ctl/`; about 170 imports placed
inside functions to avoid circular imports; `ctl/install.py` is 2,955 lines;
`service_ops._install` is a single 490-line function; the installer passes
untyped `ctx`/`check` dictionaries. → Phases 3, 4 and 8.

**F9. Not reproducible across computers.** `ctl/requirements.txt` pins only
top-level packages; `install.sh` accepts Python 3.10 but the code needs 3.11
(`from datetime import UTC` in ten modules), so Ubuntu 22.04 fails after a
"successful" check; the dashboard is built on each host after installing
Node.js from NodeSource; chat connector images are built locally with
`npm install`; `TZ` defaults to `America/Toronto` in three compose files;
only `apt` hosts are supported. → Phases 1, 8 and 9.

**F10. Dead code and stale docs.** See Part B1. Also: `install.py`'s
docstring refers to the deleted `ctl/bootstrap/server.py`; `.env.example`
talks about "Step 2"; `make clean`/`make nuke` only know about `core/` and
`.state`. → Phase 1.

**F11. Inconsistent names.** Authentik's compose project is `authentik`
(container `authentik-server-1`) while others are `mu3lab-*`; container
names are hardcoded in Python. → Phase 4.

**F12. Tests check mocks, not the system.** 421 `patch(...)` calls; no test
installs anything for real; CI compose validation needs a hand-kept list of
about 50 placeholder variables that every new app must extend. → Phases 3
and 8.

**What to keep:** the leased, resumable job queue (`ctl/jobs.py`,
`ctl/job_guard.py`); the check-then-fix installer idea; secret redaction;
the identity and CSRF model in `ctl/api/security.py`; atomic writes and file
locks (`ctl/secret_file.py`); digest-pinned images and the approved-version
flow; the test rules in `tests/README.md`; ruff, mypy, ESLint, Prettier and
the CI gates.

---

## Part D. Target architecture

### D1. Principles

1. **One folder fully describes one app.** Adding an app means adding a
   folder. No Python module outside that folder names a specific app.
2. **Every fact is written once.** Generated outputs (Caddy routes, Authentik
   configuration, `.env` files, dashboard types) are derived, never edited.
3. **One engine runs every container**, for core services and catalog apps
   alike, always from a generated project under `/srv/mu3lab/projects`.
4. **One database, one secret store,** with numbered migrations.
5. **One status snapshot,** produced by the worker, identical for every
   viewer. Permissions decide which buttons show, never what state is shown.
6. **Official interfaces only** when talking to another app: its documented
   REST API, its configuration files, or its official command-line tool.
   Where none exists, the workaround lives in that app's folder behind a
   version check and a test that runs against the real app.
7. **Built once, installed anywhere.** Releases carry prebuilt artifacts and
   lock files; installing the same release on two computers gives the same
   software.

### D2. Repository layout (end state)

```
apps/<id>/                    one folder per app, core services included
  app.yaml                    the app manifest (schema in Appendix 1)
  compose.yaml                the only place image pins live
  hooks.py                    optional; app-specific steps (Appendix 2)
  files/                      optional; config files mounted into containers
  connector/                  optional; chat connector (MCP) definition + review
mu3lab/                       Python package (renamed from ctl/, Phase 8)
  manifest/                   Pydantic models and loader for app.yaml
  store/                      database, migrations/, secret store
  engine/                     jobs, worker, lifecycle pipeline, compose runner
  status/                     reconciler that writes the status snapshot
  integrations/               authentik, vaultwarden, caddy, tailscale,
                              litellm, freellmapi, chat (one module each)
  host/                       installer: host checks and fixes per distro
  api/                        FastAPI app, models.py, routes/
dashboard/                    React app; src/api/schema.ts is generated
tests/                        unit/, integration/ (real containers), system/ (VM)
tools/                        maintainer tools (release approval, VM test)
docs/                         architecture.md, app-updates.md, this plan
```

Core services move from `core/<id>/` into `apps/<id>/` with `tier: core` or
`tier: foundation`. `services.yaml`, `catalog.yaml`, `mcp-catalog.yaml` and
`mcp-reviews/` are replaced by the per-app manifests (Phase 3).

### D3. Runtime layout (unchanged root, tidier contents)

```
/srv/mu3lab/
  data/<id>/                  app data (unchanged)
  backups/                    restic repository (unchanged)
  projects/<id>/              generated compose project for EVERY service
  state/mu3lab.db             the one SQLite database
  state/secrets.key           the one encryption key (0600)
```

The repo-root `.env` and `.state/` disappear; the control-plane and ingress
tokens move into the secret store; installer logs move to
`/srv/mu3lab/state/logs/`.

### D4. One state model

Persisted by jobs (`installations.install_state`):
`not_installed`, `installing`, `installed`, `updating`, `uninstalling`, `failed`.

Observed by the reconciler (`status_snapshot.health`):
`running`, `starting`, `stopped`, `unhealthy`, `unknown`.

Shown to people, computed in exactly one backend function
(`mu3lab/status/display.py`) and sent as `display_state`:
`not_installed`, `working` (with a job label such as "Installing"),
`running`, `stopped`, `needs_attention` (with a plain-language reason).

The dashboard maps `display_state` to a color and label and nothing else.

### D5. One lifecycle pipeline

Every install, start, stop, update and uninstall is a list of named steps run
by one engine. Each step is a small function with a typed context, and app
hooks plug in at fixed points:

```
validate → render_project → pull_images → [hooks.before_start]
→ start → [hooks.after_start] → wait_healthy → [hooks.after_healthy]
→ register_sign_in → publish_route → verify_sign_in
→ connect_chat → save_logins_to_vault → record_installed
```

Steps that do not apply to an app (no route, no sign-in, no connector) are
skipped based on its manifest, not on its ID.

---

## Part E. Rules for the implementer

Read these before starting any task. They apply to every phase.

### E1. Workflow

1. Work on a branch named `rebuild/<phase>-<task>`, for example
   `rebuild/1-2-uv-lock`. One task per branch, one pull request per task.
2. Before changing anything, run `git worktree list` and `git status`. If
   files you did not change are modified, stop and ask the owner; never stash,
   discard or exclude someone else's work.
3. Before each commit run `make verify` (lint, type check, Python and
   dashboard tests, dashboard build). Do not commit if it fails.
4. Never skip, disable or loosen a test to make it pass. If a test encodes
   behavior this plan removes, delete the test in the same commit as the
   behavior and say so in the commit message.
5. A task is done only when its acceptance criteria are met and written in
   the pull request description as a checklist with evidence (command output
   or screenshot).
6. Never print, log or commit a secret. Use `ctl.jobs.redact` (later
   `mu3lab.engine.redact`) for any text that may contain one.
7. If a task turns out larger than described, or its instructions conflict
   with the code you find, stop and report instead of improvising.
8. Tell the owner plainly when work is still running; they act on the host
   (uninstall/reinstall) as soon as a task looks finished.

### E2. Coding standards

Python:
- Python 3.12 (provided by uv, Phase 1). Type hints on every function
  signature; `mypy --strict` for new packages under `mu3lab/`, existing
  settings for code still in `ctl/`.
- Data crossing a boundary (manifest files, HTTP bodies, database rows,
  job parameters) is a Pydantic model or a frozen dataclass, never a bare
  `dict[str, Any]`.
- No imports inside functions. If you need one to avoid a circular import,
  the module boundaries are wrong: move the shared code down a layer.
  The only exception is optional heavy imports in a CLI entry point.
- Layers may import only downward:
  `api → engine/status → integrations → store/manifest → (stdlib, libraries)`.
  `host/` may import `store` and `manifest` only. Add an import-linter
  contract (`import-linter` package) in Phase 3 that enforces this in CI.
- No module may contain an app ID string literal except files inside
  `apps/<id>/`. A CI check (Phase 3) greps for this.
- Functions under 60 lines; modules under 500 lines. Split by
  responsibility, not by line count alone.
- Errors: raise specific exception classes; catch only what you can handle;
  never `except Exception: pass`. Every user-facing failure carries a
  plain-language message and a stable `code`.
- Subprocess calls go through one runner (`mu3lab/engine/run.py`) that
  applies timeouts, redaction and job cancellation checks.
- Comments explain why, not what. Module docstrings state the module's single
  responsibility in one or two sentences.

TypeScript:
- Strict mode stays on. Types for API data come only from the generated
  `src/api/schema.ts` (Phase 6). No `any`.
- Components never decide an app's state from its ID; they read fields the
  API provides.
- No `localStorage` for anything that should be the same on every device.
  It is allowed only for per-device preferences (theme, download
  parallelism).

Tests:
- Follow `tests/README.md`. Unit tests may mock the subprocess runner and
  HTTP client only; everything above that runs for real.
- Every integration module (`mu3lab/integrations/*`) gets an integration test
  that runs against the real pinned container in CI (Phase 8).
- Every bug fix starts with a test that reproduces the bug.

---

## Part F. Phases and tasks

Each task lists: goal, files, steps, acceptance criteria. Do them in order
unless a task says otherwise. Estimates assume one focused engineer.

### Phase 0. Safety net (1–2 days)

**0.1 Record the baseline.**
Steps: run `make dev-setup`, then `make verify`; save the output summary in
the pull request. Note the test count (604 Python at time of writing).
Acceptance: `make verify` passes on `main`; counts recorded.

**0.2 Write the manual acceptance script.**
Goal: one checklist the owner runs after each phase on the real host.
Files: create `docs/acceptance.md`.
Content, as numbered steps with expected results:
1. `./uninstall.sh --everything --yes`, then `./install.sh`; installer ends
   with the dashboard open and no errors.
2. Sign in on the laptop and on the phone (Tailscale connected); Home shows
   identical status and identical Get started ticks on both.
3. Install Mealie, Immich and Nextcloud from Apps; each reaches Running;
   clicking Open signs in without a login form.
4. Chat on the laptop and the phone (web and installed app) lists the same
   assistants, one per installed app; asking the Mealie assistant "what
   recipes do I have?" returns real data.
5. Add a household member in Settings → People; that person sees the same
   assistants after first sign-in.
6. Stop and start Mealie; back up and restore it; uninstall it (keep data),
   reinstall it, data is back.
7. Vaultwarden contains the SurfSense and FreeLLMAPI logins and Bitwarden
   fills them.
Acceptance: the owner has run it once on today's `main` and noted failures
in the file as "known issues before rebuild".

**0.3 VM system test, documented.**
Goal: anyone can test a clean install without wiping the owner's host.
Files: `tools/vm/fresh-vm.sh` (exists), `docs/acceptance.md`.
Steps: confirm `make vm-test` boots Ubuntu 24.04 with the checkout and runs
`./install.sh` non-interactively up to the Tailscale step; document how to
complete the Tailscale step. Fix the script if it no longer works.
Acceptance: a clean VM reaches the Tailscale prompt with one command.

### Phase 1. Quick fixes and dead code (3–5 days)

**1.1 Delete stale documents.**
Delete `PLAN.md`, `BUILD_ORDER.md`. Review `COPY_REVIEW.md` and
`docs/reviews/app-review-2026-09-30.md`: move any item still open into
GitHub issues, then delete the files. Fix the `ctl/install.py` docstring
(remove the `ctl/bootstrap/server.py` reference), `.env.example` comments,
and the `make clean`/`make nuke` targets (they must use
`tools/mu3lab-containers.sh` rather than looping over `core/`).
Acceptance: `grep -rn "bootstrap/server.py\|Step 2\|BUILD_ORDER\|PLAN.md" .`
returns nothing outside git history and this plan.

**1.2 Pin the Python toolchain and every dependency with uv.**
Goal: the same Python and the same library versions on every computer, and
Ubuntu 22.04 works.
Files: `pyproject.toml`, new `uv.lock`, `install.sh`, `Makefile`,
`.github/workflows/ci.yml`, delete `ctl/requirements.txt` and
`requirements-dev.txt`, `ctl/install.py` (`_venv_check`, `_pip_check`,
`fix_venv`, `fix_pip_deps`), `ctl/preflight.py` (Python check).
Steps:
1. Move dependencies into `pyproject.toml` `[project.dependencies]` and a
   `[dependency-groups] dev` group. Drop `docker` (unused).
2. Set `requires-python = ">=3.12,<3.13"` and add `.python-version` with
   `3.12`.
3. Run `uv lock` and commit `uv.lock`.
4. In `install.sh`, replace the venv/pip block: install uv to
   `~/.local/bin` from its official standalone installer with a pinned
   version and checksum, then run `uv sync --frozen --no-dev`. uv downloads
   Python 3.12 itself, so the host's Python version no longer matters.
   Remove the `MIN_PYTHON` check (the host needs only `curl`).
5. Update the installer steps `venv`/`pip_deps` to call `uv sync --frozen`
   and compare against `uv.lock`'s hash instead of `requirements.txt`.
6. Update the systemd unit templates in `deploy/` to run
   `@MU3LAB_ROOT@/.venv/bin/...` as before (uv creates `.venv`).
7. CI: use `astral-sh/setup-uv` and `uv sync --frozen`; run tools with
   `uv run`.
Acceptance: `uv sync --frozen` succeeds on a clean Ubuntu 22.04 VM and a
clean 24.04 VM; `make verify` passes; `grep -rn requirements.txt` finds
nothing.

**1.3 Use the host's timezone.**
Files: `apps/*/docker-compose.yml` and `core/*/docker-compose.yml` with
`America/Toronto`; `ctl/lifecycle/materialize.py`.
Steps: set `TZ` in every generated `.env` from the host
(`/etc/timezone`, else the `timedatectl show -p Timezone --value` output,
else `UTC`); change compose defaults to `${TZ:-UTC}`.
Acceptance: `grep -rn America/Toronto apps core` is empty; a test covers the
timezone lookup with injected file contents.

**1.4 Delete leftover upgrade code.** *Owner must confirm a clean reinstall
first (Part G, question 1).*
Remove each item in the first table of Part B1, with its tests. Keep the
"not leftovers" group. For credential handoffs, first prove they are
unreachable: add a temporary test asserting no manifest entry can reach
`workflow_secrets.create_handoff`, run it, then delete the whole flow
(Python, API route, table, dashboard UI, types).
Acceptance: `grep -rni "legacy\|handoff\|migrate_legacy" ctl dashboard/src`
returns only the kept SurfSense/Bitwarden lines; `make verify` passes;
acceptance script steps 1, 3 and 7 pass on a fresh install.

**1.5 Delete unused registry options.**
Remove the five unused account modes, `profiles`, `blocked`
stage/availability/`blocked_reason`, `maturity`, `VALID_ACTIONS`, and the
`mcp.tools` lists in `services.yaml` (show "Discovering tools…" until real
discovery finishes). Remove the `repair` action from backend and dashboard.
Acceptance: registry tests updated; the dashboard never shows a Repair
button; `make verify` passes.

**1.6 Fix the hardcoded "core" display rule.**
`displayStage` in `dashboard/src/lib/services.ts` hardcodes Firecrawl and
LobeChat. Add a `group` field (`apps`, `ai`, `infrastructure`) to the API
response computed from the manifest, and use it.
Acceptance: no app ID literals remain in `dashboard/src/lib/`.

### Phase 2. The same on every device — implemented by E/I; manual acceptance pending

**2.1 Store Get started progress on the server.**
Files: `dashboard/src/features/home/GetStarted.tsx`, new route in
`ctl/api/routes/identity.py` (`GET/PUT /api/v1/me/checklist`), new table
`person_checklist(person_uid, item, done_at)` added through
`ControlState` (moves into the new store in Phase 5).
Steps: replace `useManualDone` with an API-backed hook; on first load, if
`localStorage` has ticks, send them once, then delete the local key.
Acceptance: tick a step on the laptop, refresh the phone: it is ticked.
Dashboard test covers the migration path.

**2.2 One status snapshot for everyone.**
Goal: every viewer sees the same state, and page loads stop running Docker
commands.
Files: new `ctl/status_reconciler.py` (moves to `mu3lab/status/` later),
`ctl/worker.py`, `ctl/api/service_view.py`, `ctl/service_state.py`,
`dashboard/src/state/dashboard.tsx`.
Steps:
1. Move the probing in `service_snapshot` into a reconciler function that
   the worker runs every 5 seconds, and immediately after any job step that
   changes containers. It writes one row per service to a `status_snapshot`
   table (`service_id, health, detail, containers_json, route_ok, observed_at`).
2. Remove the operator-only Authentik override in `service_view.py`. If
   Authentik's route needs proof, the reconciler probes it the same way for
   everyone.
3. `GET /api/v1/services` reads the table and the job store only. It returns
   `observed_at`; the dashboard shows "Status may be out of date" if it is
   older than 30 seconds (the worker may be down).
4. Add `GET /api/v1/snapshot` returning services, system, jobs, identity
   and chat status in one response; switch the dashboard loader to it and
   keep the 10-second poll. (Server-sent events can replace polling later;
   not needed now.)
Acceptance: `GET /api/v1/services` runs no subprocess (unit test patches the
runner to fail if called); two browsers signed in as an operator and a
household member show the same state for every app; response time under
50 ms on the host.

**2.3 Spike result (done 2026-10-03).** LobeHub 2.2.18 serves its official API at
`/api/v1/openapi.json`: agents, MCP servers (with tool sync), API keys with
scopes, users and roles. Calls act as the key's owner. Shared workspaces exist
in the schema but are a cloud beta (`ENABLE_BUSINESS_FEATURES=false` in the
self-hosted build), so they are not used. LobeHub's own OIDC provider (enabled
by `JWKS_KEY`) registers the `lobehub-cli` client with the device-code grant;
Mu3Lab uses it once per person to obtain a scoped API key. The web sidebar lists
`agents` directly (`packages/database/src/repositories/home`), so API-created
assistants appear on desktop and phone browsers. The original spike text is kept
below for reference.

**2.3 Spike: shared chat assistants through an official interface.**
Time box: 3 days. Output: `docs/decisions/chat-platform.md` with findings and
a recommendation, reviewed by the owner before 2.4 starts.
Goal: find the most reliable way to keep every current chat feature:
- one separate assistant per installed app, each with only its app's tools;
- assistants appear automatically for every household member on first
  sign-in, with no per-person setup;
- sign-in only through Authentik, accounts created automatically;
- the same assistants on desktop web, phone web and the phone app;
- models routed through LiteLLM only, with no provider settings users can
  change;
- write tools ask for approval (the Mu3Lab tool gateway stays).
Candidates, in this order:
1. **LobeChat through its official API.** LobeHub 2.2.14 (August 2026)
   added a public OpenAPI (`@lobehub/sdk`) covering agents, models, users
   and, since PR #18141, MCP server registration, plus workspace-scoped API
   keys. Test whether a Mu3Lab-owned **workspace** shared by all household
   members can hold the per-app agents and their MCP connectors, so there
   is one copy everyone sees instead of per-user copies. Test that the
   mobile web view and the native app list workspace agents.
2. **Open WebUI.** Assistants are "workspace models" created by an admin
   through its REST API (`/api/v1/models/create`), each binding a system
   prompt and specific tools, shared to groups. It supports Authentik OIDC
   with automatic account creation and group mapping, native MCP over
   Streamable HTTP (v0.6.31+), and is a PWA. Check: whether MCP tool servers
   can be set through its admin API (today the UI and an env variable that
   covers only OpenAPI servers); whether a per-model tool binding can point
   at one MCP server; which native phone apps exist and how they sign in;
   its license terms for a multi-user homelab.
3. **LibreChat.** MCP servers are declared in `librechat.yaml` (official,
   file-based). Check whether per-app assistants with their own MCP servers
   can be declared in configuration or created through a supported API and
   shared with all users.
For each candidate, record a pass/fail against every bullet above, the
official interface used for each, what breaks on upgrade, and migration cost
(LobeChat conversation history does not carry over to another product;
say so).
Acceptance: the decision document exists and the owner has chosen.

**2.4 Implement the chosen chat integration.**
Write a detailed task list for this inside the decision document once the
platform is chosen; it must follow these rules:
- All code in `apps/<chat-app>/hooks.py` plus one module
  `ctl/chat_integration.py` (later `mu3lab/integrations/chat.py`) with a
  small interface: `ensure_assistant(app_manifest)`,
  `remove_assistant(app_id)`, `connect_tools(app_id, gateway_url, token)`,
  `list_assistants()`.
- Assistant titles, descriptions and instructions move from
  `ctl/lobehub_ops.py` `AGENTS` into each app's manifest (`chat.assistant`).
- No SQL against the chat app's database, no triggers, no `docker exec`.
  The only allowed direct database read is none; activity metadata
  (`ctl/mcp_chat_activity.py`) is replaced by the tool gateway's own call
  log, which Mu3Lab already controls.
- Delete `ctl/lobehub_ops.py`'s SQL, the provider guard trigger (replace
  with the chat app's own configuration for disabling user-supplied
  providers) and `ctl/mcp_chat_activity.py`.
Acceptance: acceptance script steps 4 and 5 pass on laptop, phone web and
phone app; `grep -rn "psql\|CREATE TRIGGER" ctl` is empty.

### Phase 3. One folder per app (2–3 weeks)

**3.1 Define the manifest schema.**
Files: new `mu3lab/manifest/models.py`, `mu3lab/manifest/load.py`,
`tests/unit/test_manifest.py`.
Steps: implement the Pydantic models in Appendix 1 with
`model_config = ConfigDict(extra="forbid", frozen=True)`. The loader reads
every `apps/*/app.yaml`, validates cross-app rules (unique IDs, unique
ports, dependencies exist), and caches the result per process keyed by the
files' modification times. Expose `load_all() -> Catalog` and
`Catalog.get(app_id)`.
Acceptance: unit tests cover every validation rule; loading the real
manifests (after 3.2) takes under 100 ms.

**3.2 Convert every service to a manifest.**
For each entry in `services.yaml` (16 services) create `apps/<id>/app.yaml`
by moving facts from: `services.yaml`; `ctl/identity.py`
(`OIDC_CONTRACTS`, `OIDC_LAUNCH_PATHS`, `GATED_APPS`, `TRUSTED_HEADER`,
`PROXY_GATE`); `ctl/lifecycle/materialize.py` (`GENERATED_SECRETS`,
`SSO_ONLY`, `TAILNET_SETTINGS`); `ctl/routes.py` (special route blocks);
`ctl/lobehub_ops.py` (`AGENTS`); `mcp-catalog.yaml` and `mcp-reviews/`
(into `apps/<id>/connector/`); `catalog.yaml` (bundle membership becomes
`tier: core`). Move `core/<id>/` contents into `apps/<id>/` and rename
`docker-compose.yml` to `compose.yaml`.
Remove the `images:` list (compose is the only pin; tools that need image
lists read compose). Do one app per commit, starting with Mealie, and run
the old-vs-new comparison test from 3.3 after each.
Acceptance: every service has a manifest; the old files are not yet deleted
(3.5 does that).

**3.3 Generate everything from manifests, compared against today's output.**
Files: new `mu3lab/render/` with `env.py` (project `.env`), `caddy.py`,
`authentik.py` (blueprint or API payload), `vault_items.py`.
Steps:
1. For each renderer, first write a "golden" test: render with the old code
   for every app using fixed inputs (host name, fixed secrets), store the
   output under `tests/golden/`.
2. Implement the new renderer from manifests and assert byte-for-byte equal
   output (or a documented, reviewed difference).
3. Caddy: render the whole Caddyfile from a template plus manifest
   `route` sections (`access: oidc | forward_auth | trusted_header | none`,
   `api_token_bypass` for Baby Buddy, `blocked_paths` for SurfSense) instead
   of f-strings with per-app branches.
Acceptance: golden tests pass; no renderer contains an app ID literal.

**3.4 Move app-specific steps into hooks.**
Files: `apps/<id>/hooks.py` for Nextcloud (`lifecycle/nextcloud.py`),
Immich (`enforce_identity_settings`, API bootstrap from
`lifecycle/onboarding.py`), Mealie (`adopt_mealie_admin`), AdventureLog
(`configure_adventurelog_oidc`), Actual Budget (initial-owner guard),
SurfSense (`surfsense_embedding_preflight`, account bootstrap), Paperless,
FreeLLMAPI. Interface in Appendix 2.
Steps: move the code unchanged first (one app per commit), then replace each
`if service.id == ...` branch in `service_ops.py`, `accounts.py`,
`signin_check.py`, `core_setup.py` with a hook call or a manifest field
(for example `start_timeout_seconds`, `initial_services` for Nextcloud).
Acceptance: the CI check "no app ID literals outside apps/" (add it now as
`tools/check_no_app_ids.py`, run in `make lint`) passes.

**3.5 Delete the old sources of truth.**
Delete `services.yaml`, `catalog.yaml`, `mcp-catalog.yaml`, `mcp-reviews/`,
`ctl/registry.py`, `ctl/mcp_catalog.py`, `ctl/mcp_review.py`, the contract
tables in `ctl/identity.py` and `ctl/lifecycle/materialize.py`. Update
`tools/approve_release.py` to edit `apps/<id>/app.yaml` (`version`) and
`compose.yaml`.
Replace the CI "Validate Compose interpolation" placeholder list: a small
script renders each app's `.env` with dummy secrets from its manifest and
runs `docker compose config --quiet`, so new apps need no CI edits.
Acceptance: `make verify` passes; acceptance script steps 1–7 pass on a
fresh install.

**3.6 Enforce import layers.**
Add `import-linter` with the contract in E2; fix violations by moving code
down a layer; remove function-level imports as you go.
Acceptance: `lint-imports` passes in CI; function-level imports in `ctl/`
and `mu3lab/` are under 10, each with a comment explaining why.

### Phase 4. One lifecycle engine (2–3 weeks)

**4.1 Build the pipeline runner.**
Files: new `mu3lab/engine/pipeline.py`, `mu3lab/engine/context.py`,
`mu3lab/engine/compose.py`, `mu3lab/engine/run.py`.
Steps: implement a `Step` protocol (`name`, `applies(app) -> bool`,
`run(ctx) -> None`, raising `StepFailed(code, message)`); a runner that
records stage events, checks `job_guard.checkpoint()` between steps, and
supports resuming from the last completed step. `compose.py` is the only
module that builds `docker compose` command lines; it always uses project
name `mu3lab-<id>` and the generated project directory. `run.py` is the only
subprocess runner.
Acceptance: unit tests for ordering, skipping, failure, cancellation and
resume.

**4.2 Port catalog-app install to the pipeline.**
Rewrite `service_ops._install` as the step list in D5 using hooks. Keep the
same stage names and error codes the dashboard already shows. Delete
`_install`, `_repair`, `_configure_identity` once ported.
Acceptance: the old install tests, adapted to the new engine, pass;
acceptance steps 3 and 6 pass on the host.

**4.3 Port core services to the same pipeline.**
Install Ollama, FreeLLMAPI, LiteLLM, LobeChat (or the chosen chat app) and
Firecrawl with the same engine, from generated projects, in dependency order
taken from manifests. Delete `ctl/core_setup.py`'s container handling (keep
only "queue the core install job" for the installer to call). Rename
Authentik's project to `mu3lab-authentik` and stop hardcoding container
names (`compose.py` resolves `service → container`).
Acceptance: `git pull` of a changed `apps/litellm/compose.yaml` does not
change the running LiteLLM until an approved update runs (test with a
fixture project).

**4.4 Shrink the host installer to host work.**
`ctl/install.py` keeps only host steps (packages, Docker, networks, NVIDIA,
Tailscale, systemd units, runtime folders) plus starting Caddy, Vaultwarden
and Authentik through the engine. Split it into `mu3lab/host/steps/*.py`, one
file per step group, with typed `Check` and `Fix` results (dataclasses with
an enum `state`) replacing the `DISPATCH` dictionary and `ctx` dict.
Acceptance: no file in `mu3lab/host/` over 400 lines; the installer output
looks the same to the user; fresh install passes.

**4.5 Simplify the worker loop.**
Replace the five hand-timed blocks in `ctl/worker.py` with a small scheduler
(`every(seconds, task)`) and a list of registered periodic tasks
(status reconcile, vault sync, connector reconcile, batch reconcile).
Acceptance: worker module under 150 lines; unit test for the scheduler.

### Phase 5. One database, one secret store — implemented by H

**5.1 Single database with numbered migrations.**
Files: new `mu3lab/store/db.py`, `mu3lab/store/migrations/0001_initial.sql`.
Steps: one connection helper (keep `ClosingConnection` behavior, WAL mode,
`foreign_keys=ON`); a `schema_version` table; migrations are numbered SQL
files applied in order at startup by both processes under a file lock. Move
every table from `jobs.py`, `control_state.py`, `provisioning.py`,
`install_batches.py`, `image_downloads.py`, `app_sizes.py` and
`mcp_activity.py` into `0001_initial.sql` (clean schema, no history, since
we reinstall). Replace the JSON files (`bootstrap-state.json`,
`backup-verification.json`, `vault-org.json`, `releases.json`) with tables.
Merge the four "who owns this app" records into one `app_owner` table.
Acceptance: `find /srv/mu3lab -name '*.json' -path '*state*'` is empty
after a fresh install; one `.db` file; all store tests pass.

**5.2 Single encrypted secret store.**
Files: new `mu3lab/store/secrets.py`; delete `provider_secrets.py`,
`calendar_secrets.py`, `workflow_secrets.py`, `onboarding_state.py`,
the repo-root `.env` handling in `ctl/secrets.py`.
Steps: one Fernet key at `/srv/mu3lab/state/secrets.key` (0600, created
once with `read_or_create_key`); a `secrets` table
`(scope, name, ciphertext, updated_at, expires_at)`; API:
`put(scope, name, value, ttl=None)`, `get`, `delete`, `list_names(scope)`.
Scopes: `platform` (control and ingress tokens), `provider`, `calendar`,
`job`, `app:<id>`. Machine secrets that containers read stay in generated
`projects/<id>/.env` files (0600), rendered from the store.
Acceptance: `grep -rn "Fernet(" mu3lab ctl` finds one place; no secrets file
in the git checkout; security tests (redaction, 0600 permissions) pass.

### Phase 6. Typed API and generated dashboard types — implemented by I

**6.1 Pydantic models for every route.**
Files: `mu3lab/api/models.py` (or one per router), every
`ctl/api/routes/*.py`.
Steps: give every route a request model and a `response_model`; delete
`json_body` and manual field checks; errors keep the existing
`{code, message, recommended_action}` shape through one exception handler.
Acceptance: `grep -n "dict\[str, Any\]" ctl/api` is empty;
`/openapi.json` describes every route.

**6.2 Generate TypeScript types.**
Steps: add `openapi-typescript` as a dev dependency; `npm run gen:api`
fetches the schema from a running app (or a dumped `openapi.json` committed
by `make api-schema`) and writes `src/api/schema.ts`; replace
`src/api/types.ts` with re-exports from it; CI fails if the generated file
is out of date.
Acceptance: `types.ts` deleted; `npm run typecheck` passes; changing a
response field in Python without regenerating fails CI.

**6.3 Collapse the state vocabulary.**
Implement D4: `display_state` and `reason` computed in
`mu3lab/status/display.py`; dashboard `stateLabel`, `stateTone`,
`isInstalled`, `canInstall`, `needsAttention` become thin mappings of the
new fields; remove `installation_state`, `operational_state`,
`lifecycle_state`, `setup_state`, `route_state` from the public API.
Acceptance: dashboard tests updated; acceptance steps 2, 3 and 6 pass.

**6.4 Split the stylesheet.** `dashboard/src/index.css` (3,401 lines)
becomes `styles/tokens.css`, `styles/base.css` and one CSS file per feature
folder, loaded through a central ordered import list to preserve the cascade
when pages load lazily. No visual change.
Acceptance: screenshots of Home, Apps, an app page, Chat and Settings match
before and after (Playwright screenshot test).

### Phase 7. Official interfaces for Authentik, Vaultwarden, FreeLLMAPI (2 weeks)

**7.1 Authentik through its REST API.**
Steps: at install, create a Mu3Lab service account and API token through
Authentik's documented bootstrap (`AUTHENTIK_BOOTSTRAP_TOKEN` environment
variable on first start), store it in the secret store. Keep generating
blueprint files from manifests, but apply them with
`POST /api/v3/managed/blueprints/{instance_uuid}/apply/` in the order
Mu3Lab needs, instead of `ak shell`. Use the API for owner/group setup now
done by `actions.authentik_set_owner` and password reset. Delete
`ctl/authentik_apply.py`'s shell script and the `ak shell` calls in
`ctl/actions.py`.
Acceptance: `grep -rn "ak shell\|ak\", \"shell" ctl mu3lab` is empty;
integration test against the pinned Authentik image creates, updates and
removes an OIDC application.

**7.2 Vaultwarden through the official Bitwarden CLI where it can.**
Steps: package the official `@bitwarden/cli` (pinned) in a small Mu3Lab
image built by CI (Phase 8), run `bw serve` on the backend network only when
a vault job runs, and use its local REST API for login items, folders,
collections and organization member confirmation. Keep Mu3Lab's own crypto
only for steps the CLI cannot do (check: account registration and
organization creation), isolated in `mu3lab/integrations/vaultwarden/`
with a version check against the pinned Vaultwarden.
Acceptance: integration test against the pinned Vaultwarden image registers
an owner, creates the organization, saves a login, and a second member
receives it; hand-written crypto is under 200 lines.

**7.3 FreeLLMAPI through its HTTP API.** Replace `node -e` setup in
`actions.freellmapi_local_setup` with its documented admin API (the same one
`ctl/freellmapi_admin.py` already uses). If an operation has no API, record
it in the app's `hooks.py` with an upstream issue link.
Acceptance: no `docker exec` remains except inside `apps/<id>/hooks.py`
files, each with a comment linking the upstream limitation.

### Phase 8. Releases built once (1–2 weeks)

**8.1 Rename `ctl/` to `mu3lab/`.** Mechanical move with `git mv`, update
imports, systemd units, Makefile, docs. Do this after Phases 3–7 so it does
not collide with them.

**8.2 Prebuilt artifacts.**
Files: `.github/workflows/release.yml`.
On a tag `vX.Y.Z`: build the dashboard (`npm ci && npm run build`), build
and push every image Mu3Lab builds itself (tool gateway, MCP adapters and
connectors, Bitwarden CLI) to `ghcr.io/dcazes/mu3lab-*` with digests,
write those digests into the connectors' compose files in the release
commit, and attach `mu3lab-dashboard-vX.Y.Z.tar.gz` plus `SHA256SUMS` to
the GitHub release. Connector Dockerfiles must use lock files
(`npm ci` against a committed `package-lock.json`), never bare
`npm install`.
Acceptance: a release produces the tarball and digest-pinned images.

**8.3 Install and update from tags.**
`install.sh` checks out the newest release tag (or the tag given), downloads
and verifies the dashboard tarball, and uses `uv sync --frozen`. Remove
Node.js installation from the host steps (`node` step, NodeSource repo) and
from `uninstall.sh`. `ctl/self_update.py` moves only between release tags:
fetch tags, show the release notes, check out the new tag, run the same
unattended steps as today. Developers can still run from a branch with
`MU3LAB_DEV=1 ./install.sh`, which builds the dashboard locally if Node.js
is present.
Acceptance: a fresh VM installs with no Node.js; updating from one tag to
the next works from the dashboard; the dashboard shows the release version.

**8.4 Real integration tests in CI.**
Add a CI job that starts the pinned Authentik, Vaultwarden, LiteLLM and the
chat app containers and runs `tests/integration/`. Run it on every pull
request that touches `mu3lab/integrations/` or `apps/`, and nightly.
Add a manual workflow that runs the full install in a VM
(`tests/system/`), up to the Tailscale step, for release candidates.
Acceptance: integration job green; bumping an app version in
`tools/approve_release.py` requires the integration job to pass before
merge.

### Phase 9. Other Linux distributions (2–3 weeks, after Phase 8)

**9.1 Host package layer.**
Files: `mu3lab/host/packages/{base.py,apt.py,dnf.py,pacman.py}`.
Steps: define a `PackageManager` protocol (`installed(name)`,
`add_repository(repo)`, `install(names)`, `remove(names)`); move all apt
logic from `ctl/install.py` and `uninstall.sh` into `apt.py`. Docker and
Tailscale come from their official repositories for each family (both
publish Fedora and Arch instructions). `preflight.py` selects the
implementation from `/etc/os-release` `ID`/`ID_LIKE`.
Acceptance: Debian/Ubuntu behavior unchanged (fresh-install test passes).

**9.2 Add Fedora, then Arch.** Implement `dnf.py`, test on a Fedora VM
(SELinux: bind mounts need `:Z` or the right context; check every compose
file), then `pacman.py`. Update the README support matrix only after the
acceptance script passes on that distribution.

**9.3 Port `uninstall.sh` logic into Python** using the same package layer,
keeping the shell script as a thin wrapper, so install and uninstall cannot
drift.

---

## Part G. Open questions for the owner

1. **Clean reinstall.** Phase 1 task 1.4 and Phase 5 assume you will run
   `./uninstall.sh` and a fresh `./install.sh` once, losing test app data and
   LobeChat conversations. Confirm before 1.4 starts.
2. **Chat platform.** Task 2.3 ends with a recommendation. If the best
   option is Open WebUI or LibreChat rather than LobeChat, existing
   LobeChat conversations will not move over. You choose after reading the
   decision document.
3. **Distribution order.** Phase 9 proposes Fedora before Arch. Tell us if
   another distribution matters more to you.

---

## Appendix 1. Manifest schema (`apps/<id>/app.yaml`)

Example (Mealie). Fields marked optional may be omitted.

```yaml
id: mealie
name: Mealie
tier: optional            # foundation | core | optional
group: apps               # apps | ai | infrastructure (dashboard grouping)
category: home
tagline: "Recipes, meal plans, and shopping lists"
summary: "Organizes household recipes, meal plans, and shopping lists. …"
version: v3.28.0          # approved release; image digests live in compose.yaml
upstream: mealie-recipes/mealie
depends_on: []            # other app IDs

service:                  # how Mu3Lab reaches the app on this computer
  local_port: 9925        # 127.0.0.1 port published by compose
  health_path: /api/app/about
  start_timeout_seconds: 120

route:                    # optional; absent = internal only
  https_port: 8450        # Tailscale Serve port
  caddy_port: 19467       # loopback Caddy listener
  access: oidc            # oidc | forward_auth | trusted_header | none
  launch_path: /api/auth/oauth
  blocked_paths: []       # e.g. ["/auth/register*"] for SurfSense
  api_token_bypass: null  # e.g. {path: "/api/*", header_prefix: "Token "}

sign_in:                  # required when route.access is oidc
  client_id: mu3lab-mealie
  redirect_paths: ["/login", "/login?direct=1"]
  env:                    # where the app expects the values
    client_id: MEALIE_OIDC_CLIENT_ID
    client_secret: MEALIE_OIDC_CLIENT_SECRET
    discovery_url: MEALIE_OIDC_CONFIGURATION_URL
  fixed_env:              # always assigned, never defaulted
    MEALIE_OIDC_ENABLED: "true"
    MEALIE_OIDC_AUTO_REDIRECT: "true"
    MEALIE_ALLOW_SIGNUP: "false"
    MEALIE_ALLOW_PASSWORD_LOGIN: "false"

account:                  # how the first owner account comes to exist
  mode: oidc_first_login  # none | oidc_first_login | trusted_header
                          # | environment_bootstrap | api_bootstrap
  save_login_to_vault: false

secrets:                  # generated once, kept forever
  - {env: MEALIE_DB_PASSWORD, kind: token, length: 36}

env:                      # templated; available variables: {public_url},
  MEALIE_BASE_URL: "{public_url}"   # {dns_name}, {data_root}, {tz}
  MEALIE_OIDC_REMEMBER_ME: "true"

backup:
  binds: [data]
  database: null

mobile: { … }             # unchanged from services.yaml

chat:                     # optional
  assistant:
    title: Mealie
    description: "Plan meals, recipes, and shopping lists."
    instructions: "Help with Mealie recipes, meal plans, and shopping lists. …"
  connector: connector/   # folder with connector.yaml and review.yaml

configuration: []         # user-editable settings (unchanged contract)
hooks: hooks.py           # optional
```

Validation rules (implement all in 3.1 and test each):
- `id` matches `^[a-z0-9][a-z0-9-]{1,30}$` and equals the folder name.
- Ports are unique across all apps; `caddy_port` in 19460–19499;
  `https_port` not used by Tailscale Serve for anything else.
- `route.access == oidc` requires `sign_in`; other access modes forbid it.
- `depends_on` entries exist; no dependency cycles.
- `compose.yaml` exists, every `image:` has a digest, none uses `latest`,
  and its project `name:` is `mu3lab-<id>`.
- `env` templates reference only the listed variables.
- `chat.connector` folder, if set, contains a valid connector and review.

## Appendix 2. Hook interface (`apps/<id>/hooks.py`)

```python
"""Mealie-specific install steps that no generic step covers."""

from mu3lab.engine.hooks import AppHooks, HookContext


class Hooks(AppHooks):
    def after_healthy(self, ctx: HookContext) -> None:
        """Make the owner's Authentik account Mealie's administrator."""
        ...
```

`AppHooks` (in `mu3lab/engine/hooks.py`) defines these methods, all no-ops
by default: `prepare_env(ctx, values)`, `before_start(ctx)`,
`after_start(ctx)`, `after_healthy(ctx)`, `verify_account(ctx) -> bool`,
`before_uninstall(ctx)`. `HookContext` gives typed access to: the manifest,
the project directory, the job logger, the compose runner
(`ctx.compose.exec(service, argv)`), the HTTP client, the secret store
scoped to the app, the owner identity, and `ctx.fail(code, message)`.
Hooks may not import from `mu3lab.api` or other apps' folders. The loader
imports `hooks.py` by path; a missing file means the default `AppHooks`.

## Appendix 3. Order of work and rough effort

| Phase | What | Effort |
| --- | --- | --- |
| 0 | Safety net | 1–2 days |
| 1 | Quick fixes, dead code, uv | 3–5 days |
| 2 | Same on every device (incl. chat spike) | 1–2 weeks |
| 3 | One folder per app | 2–3 weeks |
| 4 | One lifecycle engine | 2–3 weeks |
| 5 | One database, one secret store | 1–2 weeks |
| 6 | Typed API, generated types | 1–2 weeks |
| 7 | Official interfaces | 2 weeks |
| 8 | Releases built once | 1–2 weeks |
| 9 | Other distributions | 2–3 weeks |

Phases 5 and 6 can run in parallel with Phase 7 if two people work on them.
Every phase ends with the acceptance script (`docs/acceptance.md`) on a
fresh install.

## Appendix 4. Sources for the chat and Authentik research

- LobeHub v2.2.14 release notes (public API, SDK, scoped keys):
  https://newreleases.io/project/github/lobehub/lobehub/release/v2.2.14
- LobeHub PR #18141 (v1 api-keys, evals, mcp-servers, usage):
  https://github.com/lobehub/lobehub/pull/18141
- Open WebUI workspace models: https://docs.openwebui.com/features/workspace/models/
- Open WebUI API endpoints: https://docs.openwebui.com/reference/api-endpoints/
- Open WebUI MCP: https://docs.openwebui.com/features/extensibility/mcp/
- Open WebUI MCP via environment (open request):
  https://github.com/open-webui/open-webui/discussions/18105
- LibreChat model specs: https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs
- LibreChat MCP: https://www.librechat.ai/docs/features/mcp
- Authentik blueprint apply endpoint: https://api.goauthentik.io/reference/managed-blueprints-apply-create/
