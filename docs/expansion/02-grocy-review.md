# 02. Review of the Grocy integration

Grocy (integrated 2026-10-04, see [../grocy-validation.md](../grocy-validation.md))
is the most recent app added on the rebuilt per-app layout. It is the
**template** for every app in this plan. This review records what it does
well (copy it), what is missing from its instructions (now covered by
[04-app-playbook.md](04-app-playbook.md)), and the defects to fix **before**
copying the pattern (task `exp/0-1-grocy-fixes`).

Files reviewed: `apps/grocy/app.yaml`, `apps/grocy/docker-compose.yml`,
`apps/grocy/Mu3labReverseProxyAuthMiddleware.php`,
`apps/grocy/scripts/adopt-owner.php`,
`apps/grocy/connectors/grocy-community/*`, `tests/test_grocy.py`,
`docs/grocy-validation.md`, `docs/architecture.md` ("Adding an app").

## 1. What to copy (good patterns)

| Pattern | Where | Why it matters |
|---|---|---|
| Image pinned by version tag **and** digest | `docker-compose.yml` | Reproducible installs; the approve flow moves it. |
| Loopback-only published port (`127.0.0.1:8003:80`) | compose | The app is reachable only through Caddy and Tailscale. |
| `security_opt: no-new-privileges`, `cap_drop: ALL`, minimal `cap_add` | compose | Least privilege. Copy it and add back only the capabilities the image proves it needs. |
| The health check absorbs a known first-request quirk (Grocy's cache-reset redirect) | compose `healthcheck` | No person or chat tool ever sees the quirk. Look for the equivalent in each app. |
| The proxy **replaces** the identity header and strips it on the API-key path | `route.trusted_header` plus `token_bypass` | Prevents forged identities from other tailnet devices. |
| `/login*` redirected away from the app's own password form | `route.redirects` | People never see a login screen they cannot use. |
| Owner adoption through the app's **own services**, not SQL | `scripts/adopt-owner.php` via the `container_script` rule | Satisfies "official interfaces". Idempotent, with clear `MU3LAB_*_OK` and `MU3LAB_ERROR` markers. |
| A narrow extension class instead of patching source | `Mu3labReverseProxyAuthMiddleware.php` via `GROCY_AUTH_CLASS` | Uses an extension point the app provides. |
| Connector pinned with a lockfile, reachable only from the gateway network | `connectors/grocy-community/` | Supply-chain safety and isolation. |
| Dedicated, named API key for chat (`Mu3Lab MCP`) | `provision.php` | Revocable, auditable, separate from people's keys. |
| Every tool reviewed; raw-API and admin tools blocked | `review.yaml` | Small models stay accurate and safe. |
| Phone clients with exact, numbered steps, plus a web fallback | `mobile:` | Owner's "no-think walkthrough" rule. |
| A validation record listing every check and its evidence | `docs/grocy-validation.md` | Each app in this plan produces the same record (see [04-app-playbook.md](04-app-playbook.md) §12). |

## 2. Gaps in the instructions

`docs/architecture.md` "Adding an app" is four short steps. That was enough
for the person who built Grocy, but not for a smaller model. Missing:

1. **No research step.** Nothing says to confirm, against the pinned image,
   which sign-in modes, first-run behaviour, default accounts, environment
   variable names, phone-client auth and API endpoints actually exist.
   Grocy's history shows the cost: the HTTP 401 middleware and the
   cache-reset health check were found only by testing.
2. **No decision tree for sign-in.** The choice between native OIDC, trusted
   header, gate with a vault login, and open-with-own-login is not written
   down, and neither is how phone apps affect it.
3. **No household-member model.** Nothing says to decide what a second person
   gets (own account? shared data? which role?) or to test it. This gap
   caused defect D1 below.
4. **No default-account hunt.** Many images ship a default admin (Grocy
   `admin/admin`, Mealie `changeme@example.com`, Dawarich
   `demo@dawarich.app`). Retiring it is critical and was handled ad hoc.
5. **No backup-consistency guidance.** For example, which folders hold data,
   whether a database needs stopping, and what to exclude.
6. **No uninstall/reinstall-with-kept-data guidance**, beyond the acceptance
   list.
7. **No failure-handling guidance**, such as what to do when a VERIFY fails or
   an upstream behaviour differs.
8. **Locale defaults are hard-coded** (see D2).

[04-app-playbook.md](04-app-playbook.md) fills every gap above.

## 3. Defects found

### D1. Chat key provisioning breaks once a second household member exists (bug)

`apps/grocy/connectors/grocy-community/provision.php` counts users with
`ADMIN` permission and throws `expected exactly one administrator` unless the
count is 1. It does this **before** it looks for the existing `Mu3Lab MCP`
key.

Grocy's shipped default for new users is `DEFAULT_PERMISSIONS = ['ADMIN']`
(VERIFY in `/app/www/config-dist.php` of the pinned image). The trusted-header
middleware creates every household member on first visit, so a second person
is also an administrator. After that, every re-run of provisioning fails:
switching connectors, recovering the runtime credential file, repair, or
reinstalling with kept data. The note "connect chat before adding other
household members" documents the symptom but does not prevent it.

**Fix (task 0-1):**

1. Pass the owner's username to `provision.php`, the same way
   `adopt-owner.php` receives `{{owner_json}}`. VERIFY that `CredentialScript`
   in `ctl/manifest/models.py` can pass arguments. If it cannot, add an
   optional `args: tuple[str, ...]` with the same template support as the
   `container_script` rule, plus a unit test.
2. In `provision.php`, look the user up **by that username**. Require that
   user to have `ADMIN` permission and not to be the shipped
   `admin`/`admin`. Reuse an unexpired `Mu3Lab MCP` key for that user if one
   exists; otherwise create one. Remove the "exactly one administrator" rule.
3. Update `provision_note` in `connector.yaml`. Delete the "connect chat
   before adding other household members" advice.
4. Tests in `tests/test_grocy.py`: two administrators present, existing key
   reused, owner missing (clear `MU3LAB_ERROR`), and owner still `admin/admin`
   (refused). Run the PHP script in the pinned image as the existing tests do;
   if tests cannot run PHP, test the argument plumbing in Python and record
   a manual container run in the PR.

### D2. Time zone and currency are hard-coded

`docker-compose.yml` defaults `TZ` to `America/Toronto` and `GROCY_CURRENCY`
to `CAD`. A household elsewhere gets wrong expiry times and currency.

The engine already writes the host's time zone into every project's `.env`
(`ctl/engine/project.py` sets `env["TZ"] = hostinfo.timezone()`), so the
`America/Toronto` fallback is used only if that fails. It is still wrong as a
fallback.

**Fix (task 0-1):** Change the fallback to `${TZ:-UTC}`, matching Mealie and
Baby Buddy. Expose `GROCY_CURRENCY` as a `configuration:` field. Default it
from the host locale if `ctl/hostinfo.py` can supply one (add a small
function with a test if needed), otherwise `USD`, and make the setting
visible. Every new app in this plan uses `${TZ:-UTC}` (or the app's own
time-zone variable fed from `${TZ}`); never hard-code a zone.

### D3. The assistant text names LobeChat

`docs/grocy-validation.md` live-acceptance step 7 and similar text elsewhere
say "LobeChat assistant". After [07-chat-choice.md](07-chat-choice.md) this
must read "chat assistant". Fix wording in docs and any user-facing strings
during task 3-1, not now.

### D4. Live acceptance never ran

All eight live-acceptance items in `docs/grocy-validation.md` are pending.
The pattern is unit-tested and container-tested, but no full install, Open,
second person, backup, restore or uninstall has been run in the VM. That is
task 0-2. **Every later app copies this pattern**, so a flaw found here would
otherwise be copied ten times.

### D5. Minor: phone API-key bypass covers all of `/api/*`

`token_bypass` matches any request with a `GROCY-API-KEY` header on `/api/*`.
That is correct: Grocy validates the key, and D1's 401 middleware rejects bad
keys. Keep it, but add a regression test to `tests/test_manifest_routes.py`
that a request on `/api/*` **without** the header still goes through
Authentik. VERIFY whether such a test already exists before adding one.

## 4. Task 0-1: Grocy fixes

Branch `exp/0-1-grocy-fixes`. Scope: D1, D2, D5 only.

Acceptance:
- [ ] Provisioning succeeds with two or more administrators, reuses the key,
      and refuses `admin/admin`. Tests prove each case.
- [ ] No hard-coded time zone remains in `apps/*/docker-compose.yml`
      (`grep -rn "America/" apps/` returns nothing).
- [ ] `make verify` passes.
- [ ] `docs/grocy-validation.md` gains a dated "Fixes" section.

## 5. Task 0-2: Grocy live acceptance (in the VM)

Run [../acceptance.md](../acceptance.md) "Isolated installation", then the
eight steps of `docs/grocy-validation.md` "Live acceptance", plus:

9. Add a second household member **before** connecting chat, then connect
   chat. With D1 fixed this must succeed.
10. Uninstall keeping data, reinstall, and confirm the chat connector
    re-provisions with the existing key.

Phone checks (step 5) need the owner. Prepare the exact steps, ask the owner
to run them, and record their result. If the owner defers again, record
"deferred by owner <date>" and continue: phones are not a blocker for Phase 1.

Record results in `docs/grocy-validation.md`. Anything that fails is fixed in
this task (a new commit with a reproducing test first) before Phase 1 starts.
If a failure points to a **platform** defect (engine, routes, sign-in check),
stop and report it. It affects every app.
