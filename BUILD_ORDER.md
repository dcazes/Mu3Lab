# Mu3Lab — File-by-File Build Order

> How to read this doc: each phase lists files in build order. Per file: what it
> contains (function by function), how it is run/tested, and the gate that must
> pass before the next file begins. No phase starts until the previous gate is green.

---

## 0. House conventions (apply to every file)

### 0a. At-a-glance header — every source file starts with this block

Python:
```python
"""Mu3Lab :: ctl/preflight.py
...
```

TypeScript:
```ts
/**
 * Mu3Lab :: dashboard/src/api.ts
 * ...
 */
```

Bash:
```bash
#!/usr/bin/env bash
# Mu3Lab :: install.sh
# ...
```

YAML:
```yaml
# Mu3Lab :: core/ingress/docker-compose.yml
# ...
```

### 0b. Commenting rules

1. Every function gets a 1–3 line docstring: what it does, what it returns, what
   it never does (e.g. "never writes secrets", "read-only, no side effects").
2. Every `subprocess` call has an inline comment: why sudo is/isn't needed.
3. Magic values (ports, timeouts, image tags) are named constants at file top
   with a comment giving the reason.
4. Test files state the invariant under test in the test name
   (e.g. `test_job_db_never_stores_secrets`).

---

## Phase 1 — Skeleton (no logic, just shape)

| # | File | Contains | Test / gate |
|---|------|----------|-------------|
| 1.1 | `.gitignore` | `.env`, `core/*/.env`, `.state/`, `.venv/`, `node_modules/`, `dashboard/dist/`, `core/*/data/` | `git check-ignore .env .state .venv` → all ignored |
| 1.2 | `.env.example` | `MU3LAB_CTL_TOKEN=`, `MU3LAB_INGRESS_TOKEN=` with comments (no real values) | visual review |
| 1.3 | `README.md` | What Mu3Lab is, 2-step install summary, link to `docs/SETUP.md` | visual review |
| 1.4 | `Makefile` | `install / start / test / dry-run / clean / nuke` (nuke requires typing `NUKE`) | `make -n <each>` prints expected commands |
| 1.5 | `ctl/__init__.py` | Package marker + `__version__` | `python -c "import ctl"` ok |
| 1.6 | `ctl/requirements.txt` | `fastapi, uvicorn[standard], docker, pyyaml, psutil, httpx` (no fastmcp) | `pip install --dry-run` resolves |

Gate: `git status` shows only intended files; nothing secret-adjacent exists yet.

---

## Phase 2 — `ctl/preflight.py` (pure reads, zero side effects)

Functions, in file order:

- `check_os()` — parse `/etc/os-release`; accept debian≥12, ubuntu≥22.04, mint via `ID_LIKE`+`UBUNTU_CODENAME`. Returns `{name:"os", status, detail, action}`.
- `check_arch()` — `uname -m` ∈ {x86_64, aarch64}.
- `check_python()` — `sys.version_info >= (3,10)`.
- `check_node()` — `node --version` is v20.x (required: dashboard builds from source).
- `check_privilege()` — polkit agent detectable? else terminal-path. Never prompts; reports only.
- `check_docker()` — binary present? daemon reachable (`docker info`)? backend process in `docker` group (live)? `mu3lab_frontend/backend/mcp` networks exist?
- `check_tailscale()` — binary? `tailscaled` active? `tailscale status` joined?
- `check_ports()` — `socket.connect_ex` on **8787, 19460, 9001, 8081** only.
- `check_bundle()` — root `.venv/bin/python` exists? `dashboard/dist/index.html` + every referenced `/assets/*` exists?
- `check_compose_projects()` — per `core/*`: `.env` present? containers running?
- `run_all()` — runs every check, returns `{ok, checks:[...]}`. Never includes secret values.

Tests — `tests/test_preflight.py` (no root, all mocked):

- `test_ubuntu_2204_accepted` / `test_debian_11_rejected` / `test_mint_uses_ubuntu_compat`
- `test_arm_rejected` (e.g. i686)
- `test_python_39_rejected`
- `test_docker_missing_reports_install_action`
- `test_port_conflict_names_port_and_action`
- `test_bundle_missing_lists_asset`

Run: `.venv/bin/python -m unittest tests.test_preflight -v`
Gate: all green + `python -m ctl.preflight` on your box shows honest output —
blockers PASS, install-provided items TODO with "step 3 ..." actions.
(Locked: short test names; OS checks are family-generic with a WSL note;
thresholds not pins; docker floor ≥24 + compose plugin; `blocking` flags +
`install_ready` drive card ③'s gate.)

---

## Phase 2.5 — Simple check dashboard (stdlib only, retired at Phase 11)

> Locked scope: THREE gated cards — ① Unit tests → ② Preflight → ③ Install.
> Card ③ delivers host deps + Node + venv + pip + dashboard source check +
> dashboard build + root `.env` + systemd service + Docker/networks +
> Tailscale (install, guided join, serve) + Caddy, remediating precisely per
> granular check states (never reinstalling). Authentik + Vaultwarden are NOT
> here (real dashboard, PLAN.md row 16). The real dashboard's richer UI
> (routers, app installers) still arrives in Phases 8/11; the status screen
> + health API ship now because card ③ must end on a working :8787. React
> builds as the unprivileged user with per-step log tails — background tech,
> never user-facing. Privilege: ONE pkexec worker per job (ctl/elevate.py),
> combined copy-paste script headless. UI: keyed in-place row updates (open
> logs never snap shut), always-visible tails, full transcript at job end.

| # | File | Contains | Test / gate |
|---|------|----------|-------------|
| 2.5.1 | `check.sh` | Executable wrapper: python3 ≥3.10 precheck (plain-English error, no traceback) → ensure root `.venv` if `python3-venv` present else warn + continue in system mode → pick `.venv/bin/python` else `sys` python → exec `check_server.py`. Flags `--no-open`, `--no-venv`. Never escalates to sudo. | `./check.sh --help` prints usage; run on box with/without venv present |
| 2.5.2 | `tools/run_tests.py` | Stdlib JSON-line runner: `TestLoader` discovers `tests/`, custom result emits `{test, outcome}` per test + `{summary:{ran,ok,fail,error}}`. Replaces regex-parsing of `-v` text. | `python3 tools/run_tests.py` → valid JSON lines; shape test in `tests/test_run_tests.py` (no subprocess needed — import + feed fake result) |
| 2.5.3 | `check_server.py` + `ctl/install.py` + `ctl/privilege.py` + `ctl/actions.py` | Stdlib `http.server` on `127.0.0.1:8799`: static page + tests/preflight endpoints (as listed) + real `POST /api/install/start|state|events|input|continue|kill|retry` backed by the remediation runner (check-first-skip, `waiting` prompts, single-run lock, memory-only auth key). pkexec-first privilege with terminal-command fallback; no secret parameters anywhere. Header marked `RETIRE AT PHASE 11`. | dispatch + fallback + gate tests (mocked); live click-through installs for real |
| 2.5.4 | `tools/check_page.html` | Single static page (~200 lines, no framework): card ① tests (Run, progress bar, per-test rows, output expander) → card ② preflight (Run, PASS/TODO/FAIL table, copy-buttons) → card ③ install (auth-key password field — memory-only, TOS note + admin-console link — Install button, per-step rows, terminal-command fallback blocks). Locked cards stay visibly locked until prior goes green. | open in browser, click through on your box |
| 2.5.5 | `Makefile` | `check` target: `./check.sh` (+ passthrough of `--no-open`) | `make -n check` prints expected command |

Fresh-user flow this enables: `git clone` → `./check.sh` (browser auto-opens `:8799`) → Run tests → Run preflight → Install card shows its **manifest** (exact install list + "guided later" join note) → Install runs credential-free Phase A, then guided Phase B prompts inline → *"Open the real dashboard"* link (`https://mu3lab.<tail>:port`, phone-ready) → check server may exit.
Progress persists in gitignored `.state/` across checker restarts (invalidated on code change, with the reason stated). Our autostarted dashboard holding `:8787` is reported as info, never a blocker; DB-vs-live group split distinguishes "log out" from "restart the checker".
Gate: manual click-through on your clean box, cards unlock in order, no password/auth-key value in any log or response (eyeball + leak test extended to cover the key field).

---

## Phase 3 — `ctl/setup_jobs.py` (job persistence, secret-free)

Copies M2Lab's proven shape. Functions:

- `_connect()` — opens `.state/setup-jobs.sqlite3` (mode 0700 dir), creates `setup_jobs` + `setup_events` tables (WAL mode).
- `create_job(target, kind, summary)` — returns existing active job for same target (single-active guard) or inserts new `queued` row + event.
- `update_job(job_id, status, stage, summary, progress, message, action?, error?)` — clamps progress 0–100, appends event. `action` is JSON metadata (step ids, commands) — never secrets.
- `get_job(job_id)` / `list_jobs(limit)` — read paths; events ordered.
- `recover_interrupted_jobs()` — flips non-terminal rows to `failed` with "control plane restarted, safe to retry".

Tests — `tests/test_setup_jobs.py` (temp dir DB via patch, M2Lab pattern):

- `test_progress_persists_across_reopen`
- `test_second_create_returns_active_job` (single-active)
- `test_interrupted_job_becomes_retryable_failed`
- `test_job_rows_contain_no_secret_keys` (insert fake `ADMIN_TOKEN` attempt → assert absent; documents the invariant)

Run: `.venv/bin/python -m unittest tests.test_setup_jobs -v`
Gate: green; plus manual `sqlite3 .state/setup-jobs.sqlite3 ".schema"` eyeball.

---

## Phase 4 — `ctl/secrets.py` (generation only, never returns existing)

Functions:

- `generate_hex(nbytes)` — `secrets.token_hex` wrapper (injectable for tests).
- `ensure_root_env(root)` — creates root `.env` (0600) if missing; fills `MU3LAB_CTL_TOKEN`, `MU3LAB_INGRESS_TOKEN` only when absent. Returns list of keys added (never values — callers log names only).
- `ensure_service_env(service_dir, template)` — same preserve-existing logic for `core/*/.env` from `.env.example` keys.
- `redact(text)` — replaces hex-token-like values with `***` for logs/API (used by pipeline + app).

Tests — extend `tests/test_secrets_leak.py`:

- `test_existing_values_never_overwritten`
- `test_generated_env_is_mode_0600`
- `test_redact_strips_hex_tokens`
- `test_status_payload_with_fake_secrets_comes_back_redacted`

Run: `.venv/bin/python -m unittest tests.test_secrets_leak -v`
Gate: green + `stat -c %a .env` → `600` on your box.

---

## Phase 5 — `ctl/registry.py` (compose lifecycle, thin wrapper)

Functions:

- `compose_up(project_dir)` — `docker compose up -d`; returns `{ok, output_tail}`.
- `compose_down(project_dir, volumes=False)` — volumes flag defaults False (safe).
- `compose_ps(project_dir)` — container states as dict.
- `compose_logs(project_dir, tail=200)` — bounded, passed through `secrets.redact`.
- `project_healthy(project_dir, healthcheck)` — polls a caller-supplied predicate with timeout (used by steps, not hardcoded per app).

Tests — `tests/test_registry.py` (mock `subprocess.run`):

- `test_up_invokes_compose_up_detached_in_dir`
- `test_down_defaults_to_keep_volumes`
- `test_logs_are_redacted`

Run: `.venv/bin/python -m unittest tests.test_registry -v`
Gate: green. (Real-docker verification comes in Phase 8.)

---

## Phase 6 — `ctl/privilege.py` (pkexec runner, handles no passwords)

Functions:

- `has_fresh_sudo()` — `sudo -n true` rc==0 (timestamp alive → no prompt needed).
- `has_polkit_agent()` — graphical session signals (`DISPLAY`/`WAYLAND_DISPLAY`/`DBUS_SESSION_BUS_ADDRESS`); best-effort boolean.
- `run_privileged(cmd, log)` — if fresh sudo → `sudo <cmd>`; else if agent → `pkexec <cmd>`; else return `{ok:False, need_terminal:True, terminal_command:"sudo " + shell-quoted cmd}`. Streams output to job log. Never logs passwords (there are none to log).
- `quote_terminal(cmd)` — builds the copy-paste string for the fallback UI.

Tests — `tests/test_privilege.py` (mock subprocess):

- `test_fresh_sudo_runs_without_pkexec`
- `test_no_agent_returns_terminal_command`
- `test_terminal_command_is_shell_quoted`
- `test_no_password_field_anywhere` (assert module has no password variable/param)

Run: `.venv/bin/python -m unittest tests.test_privilege -v`
Gate: green.

---

## Phase 7 — Real-dashboard pipeline runner (later)

> NOTE: the simple dashboard's card ③ already installs
> (ctl/install.py, built in Phase 2.5). This phase builds the REAL
> dashboard's equivalent (Authentik/Vaultwarden/apps) when Phases 8/11 land,
> reusing the check-first + waiting-prompt patterns proven here.

### 7a. `ctl/steps_docker.py` (needs privilege)

- `DOCKER_PACKAGES`, `DOCKER_NETWORKS = ["mu3lab_frontend", ("mu3lab_backend", internal), "mu3lab_mcp"]` constants with comments.
- `check()` — mirrors `preflight.check_docker` (binary, daemon, group-live, 3 networks).
- `install(log)` — apt keyring → repo (arch+codename args in) → `apt install docker-ce ...` → `systemctl enable --now docker` → `usermod -aG docker $USER` → create networks. Each sub-step logged; first failure aborts with sanitized error.
- `verify()` — == `check()`.
- `group_checkpoint()` — distinguishes `failed` from `waiting-for-relogin` (docker works via sudo but group not live in backend process).

### 7b. `ctl/steps_tailscale.py` (needs privilege)

- `check()` — binary? daemon active? `tailscale status` joined?
- `install(log)` — apt repo → `apt install tailscale` → `enable --now tailscaled` → attempt `tailscale up --hostname=mu3lab`.
- Join handling: if `up` needs interactive login → return `{waiting:True, login_url?, terminal_command}` — **hard gate**: pipeline pauses here (locked decision), never skips.
- `verify()` — `tailscale status` rc==0.

### 7c. `ctl/steps_compose.py` (no privilege once Docker ok)

- `ensure_data_dir_ownership(service_dir)` — `mkdir -p data && chown 1000:1000 data` (vaultwarden sub-step; own verify via `stat`).
- `up_caddy(log)` / `up_authentik(log)` / `up_vaultwarden(log)` — `secrets.ensure_service_env` → `registry.compose_up` → poll health (`:19460/` proxy check; `:9001/-/health/ready/` ≤180s; `:8081/alive`).
- Per-service `check_*()` mirrors from preflight (port/health probes).

### 7d. `ctl/infra_pipeline.py` (orchestrator, background thread)

- `CORE_STEPS = [docker, tailscale, caddy, authentik, vaultwarden]` — each `{id, label, check, install, verify}`.
- `run(job_id)` — for each step: `check()` → ready? mark `ready(skipped:true)`, else `install()` → `verify()`; on failure mark `failed` + halt; on `waiting` mark `waiting` + halt (resumable, not failed).
- `retry(job_id)` — re-check all, resume from first non-`ready`.
- Step statuses: `pending → installing → verifying → ready | failed | waiting`.

Tests — `tests/test_infra_pipeline.py` (all step fns mocked):

- `test_check_ok_skips_install`
- `test_install_failure_halts_before_next_step`
- `test_verify_failure_marks_failed`
- `test_waiting_is_resumable_not_failed`
- `test_retry_resumes_from_first_non_ready`
- `test_step_order_is_docker_first_vaultwarden_last`

Run: `.venv/bin/python -m unittest tests.test_infra_pipeline -v`
Gate: green.

---

## Phase 8 — `ctl/app.py` (FastAPI surface)

Routes (final, no password endpoint):

- `GET /api/health` → `{ok, version}`
- `GET /api/preflight` → `preflight.run_all()`
- `POST /api/infra/install` → create/resume job, spawn `threading.Thread(daemon=True, target=infra_pipeline.run)`, return `{job_id}` immediately
- `GET /api/infra/status/{job_id}` → full job + per-step states + `need_terminal` payloads where present
- `GET /api/infra/logs/{job_id}?step=` → bounded redacted lines
- `POST /api/infra/retry` → `{job_id}` → `infra_pipeline.retry`
- `GET /api/settings` → non-secret config only
- `/` + static → `dashboard/dist/` SPA fallback

Test: no new unit file; exercised manually + via dashboard. Run: `.venv/bin/python -m uvicorn ctl.app:app --host 127.0.0.1 --port 8787`, then `curl localhost:8787/api/health`, `/api/preflight`.
Gate: both endpoints return honest JSON on your box.

---

## Phase 9 — `core/*` compose files + unit (config, copied-not-invented)

| # | File | Source | Gate |
|---|------|--------|------|
| 9.1 | `core/ingress/docker-compose.yml` | M2Lab shape, `caddy:2.10.2-alpine` pinned, host network, `caddy validate` healthcheck | `docker compose config` validates (needs Docker — runs after Step 1a on your box, or `docker compose version`-only syntax eyeball before) |
| 9.2 | `core/ingress/Caddyfile` | **Minimal starter** (`:19460 → 127.0.0.1:8787`), header notes Step-2 will add forward-auth + routes | `docker run --rm caddy:2.10.2-alpine caddy validate --config /mounted/Caddyfile --adapter caddyfile` (once Docker exists) |
| 9.3 | `core/ingress/.env.example` | `MU3LAB_INGRESS_TOKEN=` + comment | visual |
| 9.4 | `core/authentik/docker-compose.yml` | **Verbatim M2Lab** (server+worker+postgres, healthchecks, `127.0.0.1:9001:9000`), networks → `mu3lab_*`, blueprint mount dropped (comment says why + Step-2 return) | `docker compose config` validates |
| 9.5 | `core/authentik/.env.example` | `AUTHENTIK_TAG=latest` (floats — OIDC-native) + secret placeholders | visual |
| 9.6 | `core/vaultwarden/docker-compose.yml` | **Verbatim M2Lab** (hardened: read-only, cap_drop, `1000:1000`, bind `./data`), networks → `mu3lab_*` | `docker compose config` validates |
| 9.7 | `core/vaultwarden/.env.example` | `ADMIN_TOKEN=` + `SIGNUPS_ALLOWED=true` | visual |
| 9.8 | `deploy/mu3lab-ctl.service` | Single unit, `@MU3LAB_ROOT@` placeholder, `uvicorn ctl.app:app --host 127.0.0.1 --port 8787` | `systemd-analyze verify` on rendered file |

Gate: all `compose config` clean (after Docker present) + Caddyfile validates.

---

## Phase 10 — `install.sh` + `start.sh` + script tests

`install.sh` flow (final): guards → summary/confirm → apt (`curl git ca-certificates gnupg python3 python3-pip python3-venv`) → NodeSource Node 20 → `nodejs` + `node --version` check → root `.venv` → `pip install -r ctl/requirements.txt` → `npm ci && npm run build` → bundle verify → root `.env` 0600 (`MU3LAB_CTL_TOKEN`, `MU3LAB_INGRESS_TOKEN`, preserve existing) → render unit → linger → daemon-reload → enable → (unless `--no-start`) start + poll `:8787` ≤30s → print URL + Setup-tab pointer. Flags: `--dry-run --yes --no-start` (no firewall flag).

`start.sh`: `.venv` check → bundle check → `restart mu3lab-ctl` → poll → print. Never fails on missing Docker.

Tests — `tests/test_install_scripts.py`:

- `test_bash_syntax_clean` (`bash -n` both scripts)
- `test_help_lists_dry_run_yes_no_start`
- `test_no_ansible_references` (assert `ansible` absent)
- `test_no_firewall_flag`
- `test_token_keys_are_mu3lab_names`
- `test_unit_uses_mu3lab_root_placeholder`
- `test_no_homelab_omnilab_leftovers` (grep install.sh + unit)

Run: `.venv/bin/python -m unittest tests.test_install_scripts -v`
Gate: green, then live `./install.sh --dry-run` on your box reads correctly.

---

## Phase 11 — Dashboard (React, built from source)

Build order inside `dashboard/` (each file carries the TS header block):

1. `package.json` (`react, react-dom, vite, typescript, @vitejs/plugin-react`) + `vite.config.ts` (outDir `dist`, dev proxy → `127.0.0.1:8787`) + `tsconfig.json` + `index.html`.
2. `src/api.ts` — typed fetch wrapper + `PreflightReport / InfraJob / StepState` types mirroring the backend shapes exactly.
3. `src/components/LogTail.tsx` — bounded scrolling log (props: `lines`, auto-scroll, redaction note).
4. `src/components/StepRow.tsx` — states `pending/installing/verifying/ready/failed/waiting`; props include `terminal_command` display + copy button when `need_terminal`.
5. `src/components/InfraPipeline.tsx` — the 5 rows + overall progress + Install/Retry buttons + password never requested (polkit note in UI copy).
6. `src/pages/SetupPage.tsx` — Step-0 card (preflight table) + Step-1 card (pipeline) + Step-2 placeholder.
7. `src/App.tsx` + `src/main.tsx` — route `/` → SetupPage (router later).

Per-file gate: `npx tsc --noEmit` clean after each file; phase gate: `npm run build` green + `dist/index.html` served by FastAPI static route.

---

## Phase 12 — Docs + end-to-end (on your clean box)

1. `docs/SETUP.md` — written FROM the verified run, not before: exact commands, expected outputs, the docker-group re-login checkpoint, the Tailscale join pause, the two manual account creations.
2. E2E run: `./install.sh --dry-run` → `./install.sh --yes` → open `:8787` → `GET /api/preflight` honest → click Install → pkexec dialog appears → Docker → re-login checkpoint → Tailscale join pause → Caddy → Authentik (≤180s health) → Vaultwarden → manual accounts. Every deviation written back into `PLAN.md` + this file.
3. Full suite: `.venv/bin/python -m unittest discover -s tests -v` green + `npm run build` green.

Final gate: fresh-user path reproducible from your box state today (no docker/tailscale/caddy present).

---

## File count & order summary

~38 files, 12 phases, strictly ordered: skeleton → preflight → jobs → secrets → registry → privilege → steps+pipeline → app → compose configs → scripts → dashboard → docs/e2e. Each phase's gate is stated above; nothing proceeds on red.
