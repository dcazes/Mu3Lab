# Grocy integration validation

Status on 2026-10-04: integrated on the rebuilt per-app manifest layout (`apps/grocy/`);
live acceptance in a disposable deployment remains pending. The owner approved the HTTP 401
extension and deferred phone checks on 2026-10-04. All checks below used disposable containers
and temporary data; no running Mu3Lab installation was touched.

## Implemented

- Grocy 4.7.1, digest-pinned LinuxServer image, loopback port 8003, persistent `/config` data.
- Private HTTPS port 8459, Caddy listener 19476, app icon, Discover entry from `app.yaml`.
- Authentik trusted-header sign-in through `X-Mu3lab-User`; the browser route replaces any
  client-sent value, and the phone-app API-key route (`token_bypass` on `GROCY-API-KEY`) strips it.
- `/login*` redirects to the protected entry point so people never see Grocy's password form.
- A narrow Grocy authentication subclass returns HTTP 401 for rejected API keys and never lets
  an API-key request fall back to a username header.
- `scripts/adopt-owner.php` (a `container_script` rule, after healthy) uses Grocy's own migration
  and user services to adopt the shipped administrator as the owner, retire `admin/admin`, and
  revoke its keys. Existing owner accounts and customized administrators are preserved.
- The health check also makes one PHP request, absorbing the cache-reset redirect Grocy sends
  on its first request after an install or update.
- Android and iOS client instructions, plus mobile web fallback.
- `connectors/grocy-community`: MIT-licensed `mcp-grocy@2.8.0` (lockfile-pinned), Streamable
  HTTP, reachable only through the tool gateway network. `provision.php` mints a dedicated
  `Mu3Lab MCP` key through Grocy's ApiKeyService and refuses when more than one administrator exists.
- 56 tool definitions reviewed: 47 offered, nine blocked (raw API, user listing, printers),
  six core tools; gateway writes start off, and blocked tools are also disabled in the connector.

## Checks performed after the port

| Check | Result |
|---|---|
| Unit tests, lint, format, types, dashboard build (`make verify`) | Passed. |
| Generated Caddy route, `caddy validate` with the pinned Caddy image | Valid configuration. |
| Owner script piped as the installer does, the moment health first passes on fresh data | `MU3LAB_GROCY_OWNER_OK`; shipped admin renamed to the owner with a random password. |
| Owner script repeated; missing username; malformed JSON | Repeat is safe; bad input fails with `MU3LAB_ERROR`. |
| Connector key provisioning, repeated | Same key reused; refused once a second administrator exists. |
| First API call after a fresh start | HTTP 200 (previously a 302 cache-reset redirect). |
| Through Caddy: valid key / wrong key / wrong key + forged identity | 200 / 401 / 401. |
| Through Caddy: browser after Authentik / `/login` / forged identity header | 200 / 302 to `/` / replaced by the verified user; no forged user created. |
| Connector image build, Streamable HTTP `initialize` and `tools/list` | Exactly the 47 reviewed tools; no blocked tool exposed. |
| `inventory_stock_get_volatile` against the real Grocy API | Passed. |

## Live acceptance (pending a disposable test deployment)

1. Discover → Grocy → Install, all stages green.
2. Open lands signed in without a Grocy login; a signed-out window goes through Authentik.
3. A second household member gets their own account with full access.
4. A forged identity from another tailnet device is rejected.
5. The primary phone client connects using the Devices instructions (deferred by the owner).
6. Dashboard backup and restore keeps data; Stop, Start, Restart, Repair keep sign-in.
7. The LobeChat assistant reads low stock, and changes need approval.
8. Uninstall keeping data, reinstall, delete-data uninstall, and reinstall after delete.
