# 09. Troubleshooting: diagnosing and handling problems

Use with the procedure in [03-workflow.md](03-workflow.md) §7. All commands
run in the **disposable VM** (or against `-test-` containers you started),
never against the owner's running stack.

## 1. First five minutes for any failure

1. Read the job's redacted log in the dashboard (or the job record) and note
   the **stage** and **code**.
2. `cd /srv/mu3lab/projects/<id> && docker compose ps` shows which service
   is unhealthy or restarting.
3. `docker compose logs --tail=200 <service>` shows the app's own error.
4. `curl -sv http://127.0.0.1:<local_port><health.path>` checks the app
   directly.
5. `curl -sv https://<host>:<https_port>/` checks it through Tailscale and
   Caddy (from a tailnet machine).
6. Compare the generated files with what you expect: `.env` (mask secrets
   when copying anywhere), the Caddy block for the app, the rendered config
   templates.

## 2. Classify

| Symptom | Likely layer |
|---|---|
| Fails in `make verify` / unit tests | Ours: code or manifest |
| `docker compose config` / `validate_compose.py` errors | Ours: compose or template |
| Container exits immediately | App config, env names, permissions |
| Healthy directly, broken through the route | Caddy route, Tailscale Serve, headers |
| Sign-in loop or error page | OIDC settings, redirect URIs, cookies, clock |
| Works once, breaks after restart | Persistence (volume path), env vs DB-stored config |
| Only in the VM | Environment: no GPU, DNS, resources |
| Only on a phone | Client auth path (gate vs open), Tailscale on the phone, custom-scheme redirect |

## 3. Known failure patterns and fixes

### 3.1 Images and containers

| Problem | Cause | Fix |
|---|---|---|
| `manifest unknown` on pull | Tag or digest typo; arch missing | Re-run `make approve`'s lookup; check arm64 vs amd64. |
| Permission denied on `/data`, `/config` | Container UID ≠ folder owner | Use the image's `PUID/PGID` or `user:` per VERIFY; create the folder with the right owner in a pre-start step; never `chmod 777`. |
| Exits with "missing env" | Env name differs in this version | Re-check VERIFY item 2 of the playbook; fix names; add a test asserting required names. |
| Restart loop during first-run migrations | `start_period` too short | Measure the first start; set `start_period` and `start_timeout_seconds` with a margin. |
| Works with `cap_drop: ALL` removed | Missing capability | Add back one capability at a time (`CHOWN`, `SETUID`, `SETGID`, `DAC_OVERRIDE`, `FOWNER`) and keep the minimum. |
| Health check passes but the app 500s | Health path too shallow | Choose an endpoint that touches the database (VERIFY), or add a second check. |

### 3.2 Routing (Caddy, Tailscale Serve)

| Problem | Cause | Fix |
|---|---|---|
| 502 through the route | Upstream port wrong; app bound to the container's localhost only | Check `local_port` vs compose mapping; make the app listen on `0.0.0.0` inside the container. |
| Redirects to `http://` or the wrong port | App does not trust forwarded headers | Set the app's proxy-trust option (`TRUST_PROXY`, `N8N_PROXY_HOPS`, HA `trusted_proxies`) and its public URL env (`{{public_url}}`). See the LobeChat lesson: `X-Forwarded-Proto https` is required behind two hops. |
| WebSocket fails (editor or live updates broken) | Something in the chain strips `Upgrade` | Caddy proxies WebSockets by default; check no `header_up` removes `Connection`; test through Tailscale Serve. |
| Streaming responses arrive all at once | Buffering | `route.streaming: true` (`flush_interval -1`). |
| Big upload fails at a fixed size or time | Timeout or body limit | `route.timeout_seconds`; check Tailscale Serve limits (VERIFY); recommend copying into the media folder for multi-GB files. |
| Forged identity header accepted | Header not replaced or stripped on some path | Every path variant (`token_bypass`, `app_auth_paths`, launcher) must replace or strip it; add a test per path. |

### 3.3 Sign-in (Authentik, OIDC)

| Problem | Cause | Fix |
|---|---|---|
| "Redirect URI mismatch" | Path or port differs; custom scheme missing | Compare the exact URI the app sends (Authentik logs) with the blueprint; add `extra_redirect_uris` for phone schemes. |
| App cannot fetch discovery (`/.well-known/openid-configuration`) | The container cannot resolve or reach the tailnet hostname | Use `discovery_url` (internal) per existing OIDC apps; keep the browser-facing issuer as the public URL. If the app validates that issuers match, VERIFY how existing apps solved it (Mealie, Immich) and copy it. |
| "Issuer mismatch" | Internal vs public issuer | Same as above; some apps need the public issuer and an internal fetch override. |
| Login works, then logged out at once | Cookie `Secure`/`SameSite` issues; different ports share a host | Set the app's secure-cookie option; apps on different ports share cookies by host. Use distinct cookie names if the app allows. |
| Owner gets a normal user, not admin | Role claim missing or wrong values | Inspect the ID token claims (05 §10.3); check `role_claim` mapping and the app's expected values. |
| Second person blocked | `initial_owner_guard` not lifted | Check finalisation evidence for the owner; see `ctl/identity_reconcile.py`. |
| Stale provider after reinstall | Blueprint ordering (known lesson) | Mu3Lab applies blueprints itself (`ctl/authentik_apply.py`); never rely on the file watcher. |
| Clock-related token errors | VM clock drift | Ensure NTP in the VM. |

### 3.4 Accounts and data

| Problem | Cause | Fix |
|---|---|---|
| Default admin still works | Retirement step did not run or ran before migrations | Run it `after_healthy`; make it idempotent; test login with default credentials fails. |
| Duplicate owner after reinstall | Bootstrap ran on existing data | Check "already initialised" first (API), as Immich does. |
| App ignores changed env after first start | App stores config in its DB (Open WebUI) | Use the app's switch to prefer env, or set via API after start. |
| Restore "succeeds" but the app is broken | Data from a newer version restored onto older images | Existing behaviour restores the release too; confirm `releases.json` updated. |

### 3.5 GPU

| Problem | Cause | Fix |
|---|---|---|
| `could not select device driver "nvidia"` | NVIDIA Container Toolkit missing | The installer installs it when a GPU and driver exist; in the VM there is none: use CPU mode. Never install drivers yourself. |
| CUDA out of memory | Other apps hold VRAM | Check the GPU card; wait for idle release; the plain message `gpu_memory_busy`. |
| `CUDA driver version is insufficient` | Image CUDA newer than the host driver | Choose a base image whose CUDA works with the installer's minimum driver; report `gpu_driver_too_old`. |

### 3.6 Chat connectors

| Problem | Cause | Fix |
|---|---|---|
| Assistant missing | One of the three gates: review, live connector, person connected (LobeChat) | Check the Chat page list (`assistant_report`) first. |
| New tools invisible after an update | Unreviewed tools are hidden by design | Update `review.yaml` in the approve step. |
| `transport` errors | Server is SSE or stdio only | `platform/mcp-adapter` if it bridges; else STOP. |
| Write tool runs without asking | Provider `asks_before_changes` wrong | Gateway approvals (07 §4); test it. |

### 3.7 Backups (Kopia)

| Problem | Cause | Fix |
|---|---|---|
| `backup_busy` | Another job holds the lock | Wait; check for a stuck job; the lock is released when the process exits. |
| Leftover `*.mu3lab-restoring` folders | Crash mid-restore | Operator chooses finish or undo (06 §3.1 step 5). |
| SFTP copy host-key error | Target reinstalled | Show the old and new fingerprints; operator confirms again. |

## 4. Upstream problems

When the app itself misbehaves:

1. Search the upstream issue tracker for the error text and version.
2. If fixed in a newer release, propose approving that release (after
   reading its notes) and record it.
3. If not fixed: a minimal, removable work-around in `apps/<id>/` (env,
   config, a script using the app's own interfaces), recorded in
   `notes/upstream-issues.md` with the issue link and a "remove when"
   condition. No patching of the app's code.
4. If no acceptable work-around exists: STOP and ask (03 §6).

## 5. Rolling back your own change

- Task branches are cheap: if a design does not work, `git switch
  expansion/apps-2026` and start a new task branch. Do not leave half-done
  work on the feature branch.
- In the VM, `tools/vm/fresh-vm.sh down` then `up` gives a clean machine.
  Prefer a fresh VM over trying to undo a broken install by hand.
