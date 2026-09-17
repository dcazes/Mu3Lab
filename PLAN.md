# Mu3Lab — Build Plan v2

> Project root: `~/Desktop/Mu3Lab/`
> Fresh project inspired by M2Lab. No Ansible. Two-step init.
> React dashboard (Node 20+ is a bootstrap dependency — decided).
> All 5 open questions resolved — see §0 decisions table (all locked).
> Privilege model: native polkit dialog (pkexec) + terminal fallback — decided.
> Image policy: float OIDC-native (authentik, vaultwarden), pin the rest — decided.

---

## 0. Decisions locked

| # | Decision | Choice |
|---|----------|--------|
| 1 | Dashboard stack | **React + Vite + TypeScript**, built from source. `install.sh` installs Node 20 LTS via NodeSource apt repo, runs `npm ci && npm run build`, verifies bundle. No committed `dist/`. |
| 2 | `.venv` location | **Repo root** (`~/Desktop/Mu3Lab/.venv`). All scripts, units, docs use root. |
| 3 | Naming | **`mu3lab-*` everywhere**: units `mu3lab-ctl.service`, networks `mu3lab_frontend / mu3lab_backend / mu3lab_mcp`, env placeholder `@MU3LAB_ROOT@`, `.env` keys `MU3LAB_*` (new keys; see §9 for compat note). No `homelab-`/`omnilab-` leftovers. |
| 4 | MCP server | **Dropped from Step 0/1.** No `mu3lab-ctl-mcp.service`, no `ctl/mcp_server.py`, no `fastmcp` dependency. Returns in Step 2 with the MCP work. |
| 5 | Firewall | **Dropped flag + package from bootstrap.** No `--with-firewall`, no `ufw` install. A `POST /api/infra/firewall` opt-in step may return later; not in this build. |
| 6 | Compose `version:` key | **Omitted everywhere** (obsolete in Compose v2, warns). |
| 7 | `clean` vs `nuke` | `make clean` = `compose down` (keep volumes) + rm `.state`. `make nuke` = `down -v` + rm `.venv`, `node_modules`, `.state`, requires typing `NUKE`. |
| 8 | Tailscale install method | **apt repo** (Tailscale official apt, pinned), not `curl | sh`. Auditable, `--dry-run`-friendly. |
| 9 | Pipeline execution | **Background thread**; `POST /api/infra/install` returns `job_id` immediately; frontend polls `GET /api/infra/status/{job_id}`. Resume = re-check each step, skip `ready`, run from first non-ready. |
| 10 | Authentik/Caddy/Vaultwarden composes | **Copied from M2Lab** (networks remapped to `mu3lab_*`), minimal starter Caddyfile — locked. Authentik blueprint mount dropped until Step 2. |
| 11 | Tailscale gate | **Hard gate — locked.** Pipeline waits at Step 1b until `tailscale status` shows joined. No soft-continue. |
| 12 | Image policy | **Float OIDC-native, pin the rest — locked.** Float: `goauthentik/server` (native OIDC provider), `vaultwarden/server` (native SSO). Pin: `caddy:2.10.2-alpine`, `postgres:16-alpine` (need config changes / are pure infra). Floating images upgrade on `compose pull`; health-verified after each pull. |
| 13 | Caddy scope | **Minimal — locked.** `:19460 → dashboard` only. Forward-auth + app routes arrive in Step 2 with the apps. |
| 14 | Privilege model | **Native polkit (pkexec) + terminal fallback — locked.** No browser password field. See §7. |
| 15 | Env names | **Fresh `MU3LAB_*` — locked.** `MU3LAB_CTL_TOKEN`, `MU3LAB_INGRESS_TOKEN`. No M2Lab compat keys. |
| 16 | Dashboard split | **Simple (stdlib, :8799) vs real (React/FastAPI, :8787) — locked.** Simple dashboard install card delivers: host deps + Node + venv + pip + dashboard source check + dashboard build + root `.env` + systemd service + **Docker engine + Tailscale (up+serve) + Caddy**, with Docker group authorization handled inline (`sg`, no logout — direct `docker` in the user's own terminals still wants one login cycle, stated as end-of-job FYI, never a gate). Authentik + Vaultwarden are **user apps installed from the real dashboard**, not infra. |
| 17 | Tailscale join | **Auth-key field, programmatic — locked.** Install card takes a reusable auth key (paste once, memory-only, never stored/logged) → `tailscale up --authkey=… --hostname=mu3lab` + `tailscale serve` mappings run with zero manual steps. No key → fallback shows the `tailscale up` login URL as a terminal command (exception path, not the flow). Unattended join without a key is impossible — Tailscale requires human login otherwise. |
| 18 | Serve mappings | **Programmatic — locked.** Install card configures `tailscale serve` for Caddy `:19460` (exact flags verified live via `tailscale serve --help` during build, not from memory). Verified by `tailscale serve status` + remote curl. Phone test: `https://mu3lab.<tail>.ts.net:<port>` from a second tailnet device. |

### Resolved questions (was: open questions)

- Q1 Tailscale → hard gate (locked, row 11). Join itself is automatic via auth key (row 17); the gate only bites on the no-key fallback path.
- Q2 Images → OIDC policy (locked, row 12).
- Q3 Caddy → minimal (locked, row 13).
- Q4 Privilege → polkit native (locked, row 14).
- Q5 Env names → fresh `MU3LAB_*` (locked, row 15).
- Q6 Dashboard split → simple installs docker/tailscale/caddy, real dashboard installs authentik/vaultwarden/apps (locked, row 16).

---

## 1. Project structure (corrected)

```
~/Desktop/Mu3Lab/
├── install.sh
├── start.sh
├── Makefile                    # install / start / test / dry-run / clean / nuke
├── .env                        # root secrets, mode 0600, gitignored (CTL token + ingress token)
├── .env.example
├── .gitignore                  # .env, core/*/.env, .state/, .venv/, node_modules/, dashboard/dist/
├── README.md
├── ctl/
│   ├── __init__.py
│   ├── app.py                  # FastAPI: /api/preflight, /api/infra/*, /api/health, static dashboard
│   ├── preflight.py            # pure check_*() fns, no side effects
│   ├── infra_pipeline.py       # background runner, per-step {check, install, verify}
│   ├── steps_docker.py         # docker install + networks (apt repo method)
│   ├── steps_tailscale.py      # tailscale install + up (apt repo method)
│   ├── steps_compose.py        # caddy/authentik/vaultwarden compose up + health waits
│   ├── privilege.py            # pkexec runner + need_terminal signalling (never handles passwords)
│   ├── setup_jobs.py           # SQLite jobs/events (M2Lab pattern, secret-free)
│   ├── registry.py             # compose_up/down/status/logs per project dir
│   ├── secrets.py              # secret generation (authentik/vaultwarden/ingress tokens)
│   └── requirements.txt        # fastapi, uvicorn[standard], docker, pyyaml, psutil (NO fastmcp)
├── core/
│   ├── ingress/
│   │   ├── docker-compose.yml  # caddy:2.10.2-alpine, network_mode host, Caddyfile mount
│   │   ├── Caddyfile           # MINIMAL starter (§5), not M2Lab's full file
│   │   └── .env.example        # MU3LAB_INGRESS_TOKEN
│   ├── authentik/
│   │   ├── docker-compose.yml  # verbatim from M2Lab (networks remapped, blueprint mount dropped)
│   │   └── .env.example        # AUTHENTIK_TAG / SECRET_KEY / POSTGRES password
│   └── vaultwarden/
│       ├── docker-compose.yml  # verbatim from M2Lab (networks remapped)
│       └── .env.example        # ADMIN_TOKEN (hex; argon2 optional), SIGNUPS_ALLOWED
├── deploy/
│   └── mu3lab-ctl.service      # single unit (no -mcp unit in this build)
├── dashboard/
│   ├── package.json            # react, vite, typescript
│   ├── vite.config.ts          # build to dist/, dev proxy → 127.0.0.1:8787
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api.ts              # fetch wrapper + types
│       ├── pages/SetupPage.tsx # Step-0 card + Step-1 pipeline card + Step-2 placeholder
│       └── components/
│           ├── InfraPipeline.tsx
│           ├── StepRow.tsx
│           └── LogTail.tsx
├── tests/
│   ├── __init__.py
│   ├── test_preflight.py
│   ├── test_infra_pipeline.py
│   ├── test_install_scripts.py
│   ├── test_setup_jobs.py
│   └── test_secrets_leak.py    # NEW: asserts no secret values in job DB / API responses
├── docs/
│   └── SETUP.md
└── .state/                     # runtime only: setup-jobs.sqlite3
```

---

## 2. Step 0: `install.sh` — full flow (revised)

```
1. Parse args: --dry-run | --yes | --no-start        (NO --with-firewall)
2. Guards (fail fast, each a preflight.py mirror):
   - $EUID != 0 (run as normal user)
   - OS: debian>=12 | ubuntu>=22.04 | mint via ID_LIKE ubuntu/debian + UBUNTU_CODENAME
   - arch: x86_64 | aarch64
   - python3 >= 3.10 present
   - sudo + apt-get present
   - path has no spaces/pipes
3. Summary print (user confirms unless --yes; --dry-run prints + exits 0)
4. sudo apt-get update
5. sudo apt-get install -y: curl git ca-certificates gnupg python3 python3-pip python3-venv
     (NO docker, NO ufw, NO ansible)
6. Node 20 LTS via NodeSource:
     - keyring /etc/apt/keyrings/nodesource.gpg, repo deb.nodesource.com/node_20.x
     - sudo apt-get install -y nodejs  → verify `node --version` (v20.x)
7. python3 -m venv .venv  (ROOT .venv; reuse if present)
8. .venv/bin/pip install -r ctl/requirements.txt
9. cd dashboard && npm ci && npm run build && cd ..   (Node required here)
10. Verify dashboard/dist/index.html + every /assets/* referenced file exists
11. Root .env (0600): create if missing; add MU3LAB_CTL_TOKEN=<hex32> if missing
    (+ MU3LAB_INGRESS_TOKEN=<hex32> if missing — shared with core/ingress/.env at Step 1c)
    Never overwrite existing values.
12. Render deploy/mu3lab-ctl.service (@MU3LAB_ROOT@ → real path) to
    ~/.config/systemd/user/mu3lab-ctl.service
13. loginctl enable-linger $USER; systemctl --user daemon-reload
14. systemctl --user enable mu3lab-ctl.service
    (unless --no-start: also `restart` it; poll http://127.0.0.1:8787 ≤30s via curl --fail)
15. Print: dashboard URL + "Open the Setup tab to install infrastructure."
```

Docker-absence must never fail install.sh/start.sh. `start.sh`: check `.venv/bin/python` + `dist/index.html` exist → `restart mu3lab-ctl` → poll `:8787` → print URL. No docker checks at all in Step 0.

---

## 3. `mu3lab-ctl.service` (single unit)

```ini
[Unit]
Description=Mu3Lab Control Plane
After=network.target

[Service]
Type=exec
WorkingDirectory=@MU3LAB_ROOT@
ExecStart=@MU3LAB_ROOT@/.venv/bin/uvicorn ctl.app:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

---

## 4. Backend API (`ctl/app.py`)

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/api/health` | `{ok:true, version}` — no auth |
| `GET` | `/api/preflight` | `preflight.run_all()` JSON: list of `{name, status: ok\|missing\|fail, detail, action}`. Never includes secret values. |
| `POST` | `/api/infra/install` | Creates job (or resumes existing active), spawns background thread running `infra_pipeline.run()`, returns `{job_id}` immediately. |
| `GET` | `/api/infra/status/{job_id}` | Full job: per-step `{id, label, status, started_at, ended_at, error, log_tail}` + overall `status/progress`. |
| `GET` | `/api/infra/logs/{job_id}?step={id}` | `{step, lines:[...]}` (bounded, last 200 lines; secrets redacted). SSE optional later; polling is fine for v1. |
| `POST` | `/api/infra/retry` | `{job_id}` → re-checks all steps, reruns from first non-`ready`. If a step needs privilege and no Polkit agent is present, its status carries `{need_terminal:true, terminal_command}` instead of failing. |
| `GET` | `/` + static | Serves `dashboard/dist/` (SPA fallback to index.html). |
| `GET` | `/api/settings` | Non-secret config: ports, paths, versions. Secret values never returned. |

No password-accepting endpoint exists by design (see §7).

Background runner: `threading.Thread(daemon=True)` + `setup_jobs` updates per transition. Guard: only one active infra job (second `POST` returns existing `job_id`).

---

## 5. Compose stacks (verbatim sources, Mu3Lab adaptations)

### 5a. Networks (created in Step 1a, before any compose)

- `mu3lab_frontend` (egress allowed)
- `mu3lab_backend` (internal: `docker network create --internal`)
- `mu3lab_mcp` (reserved for Step 2 sidecars; created now so later composes don't race)

### 5b. `core/ingress/` — Caddy (adapted, minimal)

`docker-compose.yml` — M2Lab shape kept (host network mode, Caddyfile mount, `caddy validate` healthcheck), image **pinned `caddy:2.10.2-alpine`**:

```yaml
services:
  caddy:
    image: caddy:2.10.2-alpine
    restart: unless-stopped
    network_mode: host
    env_file: ${INGRESS_ENV_FILE:-.env}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    healthcheck:
      test: ["CMD", "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  caddy-data:
  caddy-config:
```

`Caddyfile` — **minimal starter** (deliberately NOT M2Lab's full tailnet file; that file hard-codes ~15 app routes + forward-auth + tailnet host and belongs to Step 2):

```
{
	auto_https off
	admin off
}

# Step 1: dashboard only. App routes + Authentik gates arrive in Step 2.
http://:19460 {
	reverse_proxy 127.0.0.1:8787
}
```

Health verify for Step 1c: `caddy validate` in container AND `curl --fail http://127.0.0.1:19460/` (dashboard reachable through Caddy).

`.env.example`: `MU3LAB_INGRESS_TOKEN=replace_with_hex32` (reserved for Step 2 `dashboard_headers`; written now so the file exists).

### 5c. `core/authentik/` — verbatim from M2Lab minus blueprint mount

M2Lab's file uses double-underscore env (`AUTHENTIK_POSTGRESQL__PASSWORD`), `AUTHENTIK_TAG` pin, `server` + `worker` split, healthchecks on all three services, `127.0.0.1:9001:9000` mapping. **Copied exactly**, with two changes:

1. Networks remapped: `frontend-net: {external:true, name: mu3lab_frontend}`, same for backend.
2. **Blueprint mount dropped**: M2Lab mounts `./blueprints/omnilab.yaml` (SSO bootstrap for its apps). Mu3Lab has no apps yet; mounting a nonexistent file breaks startup. The mount + `blueprints/` dir return in Step 2 with the SSO work. `volumes:` keep `authentik-media/templates` (needed) — keep `authentik-postgresql` volume.

`.env.example` (same keys as M2Lab; tag floats per image policy §0-row-12):

```
# Floating tag: authentik ships native OIDC, so it tracks updates.
# Health endpoint is the gate after every pull (see Step 1d verify).
AUTHENTIK_TAG=latest
AUTHENTIK_SECRET_KEY=replace_with_openssl_rand_hex_32
AUTHENTIK_POSTGRESQL__PASSWORD=replace_with_a_long_random_password
# First owner setup is completed in Authentik's UI. Never put owner passwords here.
```

`secrets.py` generates: `AUTHENTIK_SECRET_KEY=<hex64>`, `AUTHENTIK_POSTGRESQL__PASSWORD=<alnum32>` into `core/authentik/.env` (0600), preserving existing values.

Health verify: `GET http://127.0.0.1:9001/-/health/ready/ == 200` (poll ≤180s; postgres first-boot is slow).

### 5d. `core/vaultwarden/` — verbatim from M2Lab

Kept exactly (hardened shape: `ROCKET_PORT=80` inside container, host map `127.0.0.1:8081:80`, bind `./data`, `user 1000:1000`, `no-new-privileges`, `cap_drop ALL`, `read_only + tmpfs`), networks remapped to `mu3lab_*`.

`.env.example`: `ADMIN_TOKEN=<hex32 from secrets.py>` (+ `SIGNUPS_ALLOWED=true`; `DOMAIN`/SSO lines deferred to Step 2 tailnet work). Image floats (`vaultwarden/server:latest`) per policy — native SSO support tracks updates; `/alive` is the gate after every pull.

Health verify: `GET http://127.0.0.1:8081/alive == 200`.

**Data-dir ownership note**: bind `./data` + `user: 1000:1000` fails if the dir is root-owned (happens when a prior `compose up` ran under sudo-docker). `steps_compose.py` must `mkdir -p data && chown 1000:1000 data` (as user; sudo only if needed) before first up. Recorded as an explicit sub-step with its own verify (`stat -c %u data == 1000`).

---

## 6. Install pipelines — split across two dashboards (locked)

**Simple dashboard** (`check_server.py`, :8799) install card runs, in order:
host apt deps → Node 20+ → root `.venv` → pip requirements → `npm ci/build` →
root `.env` → `mu3lab-ctl.service` render/enable/start → **Docker+networks →
Tailscale (install, up via auth key, serve mappings) → Caddy**. Ends with the
real dashboard up on `:8787` AND tailnet-served (phone-reachable). Authentik
and Vaultwarden are NOT here.

**Real dashboard** (`:8787`) then offers Authentik + Vaultwarden as the first
user-facing installs (same check→install→verify pattern, code in
`steps_compose.py`), followed by Step-2 apps. Their compose specs (§5c–5d)
are unchanged — only the UI that triggers them moved.

Each step runs `check()` first: if `ok`, mark `ready` (record `skipped:true`) and move on. Else `install()` then `verify()`. Any failure → mark `failed` with `{error, failing_command_sanitized, log_tail}`, halt pipeline. `retry` re-runs `check()` for every step and resumes from the first non-ready.

### Step 1a — Docker + networks (`steps_docker.py`, needs sudo)

- check: `docker info` rc==0 AND `id -nG` contains `docker` (live in backend process) AND `docker network inspect` ok for all three `mu3lab_*`.
- install: NodeSource-style apt repo for Docker (`/etc/apt/keyrings/docker.asc`, `docker.list` with arch+codename from preflight), `apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`, `systemctl enable --now docker`, `usermod -aG docker $USER`, create the 3 networks.
- verify: same as check.
- **Group checkpoint**: if `docker info` passes (via sudo fallback) but group not live in the backend process, status = `waiting` (not `failed`) with message "Log out/in (or `newgrp docker`), then Retry. The dashboard service restarts itself on retry to pick up the group." Retry handler restarts `mu3lab-ctl` before re-check when this flag is set. (Restart is free — with polkit there is no in-memory password to lose.)

### Step 1b — Tailscale (`steps_tailscale.py`, needs sudo)

- check: `which tailscale` AND `systemctl is-active tailscaled` AND `tailscale status` rc==0 (joined).
- install: Tailscale apt repo (`/etc/apt/keyrings/tailscale.gpg`, `tailscale.list`), `apt install tailscale`, `systemctl enable --now tailscaled`, then join.
- join UX (no authkey in v1): status = `waiting` with the exact command + (if `tailscale up` printed a login URL, surface it in logs + status detail). Poll `tailscale status` on retry. Future: authkey field in dashboard.
- verify: `tailscale status` shows node (rc==0).
- **Hard gate (locked):** pipeline halts at `waiting` until joined. No soft-continue; Caddy/Authentik/Vaultwarden stay blocked behind it.

### Step 1c — Caddy (`steps_compose.py`, no sudo once Docker ok)

- check: TCP `127.0.0.1:19460` connectable AND `curl --fail http://127.0.0.1:19460/` (proxies to dashboard).
- install: ensure `core/ingress/.env` exists (0600, ingress token = root token value), `docker compose up -d` in `core/ingress/`.
- verify: `docker inspect` health + curl through the proxy.

### Steps 1d–1e — Authentik / Vaultwarden (REAL dashboard, not simple)

Spec unchanged from below (compose files §5c–5d, secrets, health polls,
manual admin/master-account tails). Only the trigger UI moved: these run
from `:8787` after the user arrives via tailnet, not from `:8799`.

### Step 1d — Authentik (no sudo)

- check: `GET :9001/-/health/ready/ == 200`.
- install: `secrets.py` writes `core/authentik/.env` (0600, preserve existing), `docker compose up -d`, poll health ≤180s.
- verify: health endpoint 200.
- manual tail: status detail links `http://127.0.0.1:9001` — "create the initial admin account in Authentik's UI" (backend never sees it).

### Step 1e — Vaultwarden (no sudo)

- check: `GET :8081/alive == 200`.
- install: `mkdir -p data && chown 1000:1000 data`, `secrets.py` writes `core/vaultwarden/.env`, `docker compose up -d`.
- verify: `/alive` 200.
- manual tail: link `http://127.0.0.1:8081` — "create your master password directly in Vaultwarden" (backend never sees it).

---

## 7. `ctl/privilege.py` — privilege model (locked: native polkit + terminal fallback)

No browser password field. The backend never accepts, holds, or forwards passwords. Flow per privileged step:

1. Try `sudo -n true` first (timestamp already fresh, e.g. user used sudo recently) → run directly, no prompt at all.
2. Else launch the step under **`pkexec`** (Polkit). On your Mint/Cinnamon graphical session this produces the **system-native dialog** — screen dims, system prompt, same as keyring access. The password goes to the **system auth agent only**: it never enters the browser DOM, never touches dashboard JS memory, never reaches `ctl/app.py`, is never logged, never persisted. This is exactly the UX you described, and it is what `pkexec` (not classic sudo-askpass) achieves.
3. Headless/SSH sessions with no Polkit agent: `pkexec` fails → step returns `{need_terminal: true, terminal_command}` → frontend shows the exact copy-paste command → you run it in your terminal (where sudo prompts natively) → click Retry.
4. Every privileged step exposes `terminal_command` regardless, so the manual path always exists. `install.sh --dry-run` prints the same class of preview.

Consequences vs v1 draft: the `POST /api/infra/sudo-password` endpoint is **deleted** (no password intake means nothing to leak). `ctl/sudo.py` + `ctl/askpass.py` become a single `ctl/privilege.py` (pkexec runner + `need_terminal` signalling). `test_secrets_leak.py` asserts job DB/API/logs carry no secret patterns AND asserts no API route accepts a password field.

---

## 8. `ctl/preflight.py` — check list (final)

`check_os` (release parse + ID_LIKE + codename) · `check_arch` · `check_python (>=3.10)` · `check_node (v20.x — required, dashboard builds from source)` · `check_privilege` (polkit agent present? else terminal-path) · `check_docker` (binary, daemon, group-live, 3 networks) · `check_tailscale` (binary, daemon, joined) · `check_ports` (**only 8787, 19460, 9001, 8081**) · `check_bundle` (root `.venv`, `dashboard/dist/index.html` + assets) · `check_compose_projects` (per `core/*`: `.env` present, containers running). `run_all()` → `{ok, checks:[{name, status, detail, action}]}`. Pure reads; safe to call any time.

---

## 9. Remaining compat/clarification notes

- Root `.env` keys (locked): `MU3LAB_CTL_TOKEN` + `MU3LAB_INGRESS_TOKEN`. Fresh names, no M2Lab compat.
- `services.yaml`/`catalog.yaml`: intentionally absent in this build (Step 2 introduces the registry/catalog when apps arrive).
- `requirements.txt`: `fastapi>=0.115, uvicorn[standard]>=0.30, docker>=7.1, pyyaml>=6.0, psutil>=6.0`. No fastmcp. `requests` or httpx for health polls — pick `httpx` (async-friendly) — one-line addition, no new service.
- Dashboard dev flow: `cd dashboard && npm run dev` (proxy → :8787) for hacking; `npm run build` output is what `install.sh` + `app.py` serve.
- Acceptance table (per-step "done means"): §6 verify lines ARE the criteria; manual equivalents: `docker info`, `docker network inspect mu3lab_frontend/backend/mcp`, `tailscale status`, `curl -f http://127.0.0.1:19460/`, `curl -f http://127.0.0.1:9001/-/health/ready/`, `curl -f http://127.0.0.1:8081/alive`, `systemctl --user status mu3lab-ctl`.

---

## 10. Build order (for the implementing model)

1. Repo skeleton: `.gitignore, .env.example, README, Makefile, deploy/mu3lab-ctl.service, ctl/{__init__,requirements}, dashboard/{package.json,vite.config,tsconfig,index.html,src skeleton}`.
2. `ctl/{preflight,privilege,setup_jobs,secrets,registry}.py` + `tests/test_{preflight,setup_jobs,secrets_leak}.py` — all unit-testable, no root needed.
3. `core/{ingress,authentik,vaultwarden}/{docker-compose.yml,.env.example,Caddyfile}` verbatim-per-§5.
4. `ctl/{steps_docker,steps_tailscale,steps_compose,infra_pipeline,app}.py` + `tests/test_infra_pipeline.py` (mock subprocess/docker).
5. `install.sh, start.sh` + `tests/test_install_scripts.py` (bash -n, --dry-run, no-ansible assert, bundle assert).
6. Dashboard pages/components (`SetupPage, InfraPipeline, StepRow, LogTail, api.ts`) + `npm run build` green.
7. End-to-end on your box (you're clean: no docker/tailscale): `./install.sh --dry-run`, `./install.sh --yes`, open `:8787`, click Install, walk the 5 steps, record deviations back into this plan.
8. `docs/SETUP.md` written last, from the actual verified run — not from memory.
