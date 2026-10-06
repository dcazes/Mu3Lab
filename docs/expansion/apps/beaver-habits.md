# Beaver Habit Tracker (task 5-02)

Simple daily habit check-ins and streaks, one private list per person.
Chosen instead of Habitica (see [../01-decisions.md](../01-decisions.md) §2.1).

## 1. VERIFY list

- Project `daya0576/beaverhabits`: latest stable release; image
  `daya0576/beaverhabits:<ver>`; digest; licence (MIT reported); arm64.
- Internal port (8080), data path (`/app/.user` reported), process UID.
- **Trusted-header SSO** (reported in its README): the exact env var(s) and
  the header it reads, and whether the value is an **email** or a username.
  Its wiki "Environment variables" page lists all options. Note: the reported
  `TRUSTED_LOCAL_EMAIL` is a **single-user bypass**. Do **not** use it.
  Multi-user requires the header mode.
- Does header SSO auto-create users? Does it still show its own login page?
  Can password registration be disabled (`MAX_USER_COUNT`, a registration
  switch)?
- REST API: authentication method (per-user token?), endpoints for listing
  habits and marking a day complete.
- Health endpoint.
- Any MCP server (unlikely). Home Assistant integration (reported); note it
  for later.

## 2. Services

| Service | Image | Port | Data |
|---|---|---|---|
| `beaver` | daya0576/beaverhabits | 8080 → `127.0.0.1:8086` | `data/beaver-habits/user` → `/app/.user` |

SQLite. No other services.

## 3. Manifest sketch

```yaml
id: beaver-habits
capabilities: [habits]
name: Beaver Habits
tier: optional
group: apps
category: family
tagline: Daily habits and streaks
service: {local_port: 8086, health: {path: <VERIFY>}}
route:
  https_port: 8461
  caddy_port: 19478
  access: trusted_header
  trusted_header: <VERIFY header name>
  trusted_value: email            # new route feature, 05-platform.md §5.1, if the app wants an email
  redirects: [{path: <login page path>, to: /}]
sign_in: {method: trusted_header, note: Authentik signs you in; each person has a private habit list.}
account: {mode: trusted_header, user_action: Open Beaver Habits and add your first habit.}
env: {<VERIFY registration-off env>: '<value>'}
```

## 4. Sign-in

- The proxy **replaces** the trusted header with the Authentik identity
  (existing `trusted_header` behaviour) and the value type matches what Beaver
  expects. If Beaver needs an email and a person has no email in Authentik,
  the request must fail with a clear page, not create a blank user. VERIFY
  what Authentik sends for users without email; Mu3Lab's add-person flow
  requires an email (`ctl/people.py add_person`), so this should not happen
  for Mu3Lab-created people. Test the owner created by the installer.
- Owner: nothing to adopt (no default admin). VERIFY: no default account.
- Registration and password login: disabled if Beaver allows. If its login
  page cannot be disabled, redirect its path to `/` at the proxy (header SSO
  then signs the person in).
- Forged header test (E6) is mandatory.

## 5. Chat connector

Optional for this app. If Beaver's REST API supports per-user tokens, a
small `mu3lab-adapter` connector could offer `list_habits`, `today_status`
(read) and `mark_done` (write, off). **But** a single connector credential
sees only one person's habits. Habits are personal, and the assistant
cannot act per person with one key. Decision: **no connector in this task**.
Record it in `notes/future.md` as "per-person connector credentials needed".
The manifest has no `chat:` section.

## 6. Phone clients

PWA (reported iOS PWA support). Steps: open on the phone with Tailscale
connected, sign in through Authentik, Add to Home Screen. Note: an iOS PWA
keeps its own cookies, so sign-in happens once inside the PWA.

## 7. Backups

`data/beaver-habits/user`. Small. Stop-snapshot-start.

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| B1 | Person's email changes in Authentik | Beaver sees a new identity, so a new empty list. VERIFY whether Beaver keys by email; if so, document that email changes need an operator to merge (rare). |
| B2 | Header present but Authentik session expired | Forward auth redirects to Authentik first; the header is never set without a session. |
| B3 | `MAX_USER_COUNT` reached | Set unlimited (or household size + margin); VERIFY the default. |

## 9. Acceptance (VM)

Standard journey plus: two people each see only their own habits; a forged
header from another tailnet device does not reach another person's list.
