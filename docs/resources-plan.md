# Resources, Calendar tab and settings tidy-up

Owner's 9 requests from 2026-10-14, with the decisions taken for each. All
work stays uncommitted until the whole set is done, then is committed together
with the other session's pending fix (turned-off apps don't fail verification;
dashboard rides out brief refresh failures; contract-error logging).

## 1. RAM and disk per app (Home)

- **Form:** two donuts side by side (RAM, Disk), total in the centre
  ("7.4 GB of 62 GB"), legend underneath.
- **Placement:** inside the existing System status chip on Home; expanding it
  shows the donuts instead of the RAM/Disk bar rows.
- **Slices add up to the whole machine:** each app, "Mu3Lab core" (Authentik,
  Caddy, gateway, dashboard, relay…), "Other (not Mu3Lab)" and "Free".
- **Small apps:** the six biggest apps get their own colour; the rest merge
  into "Other apps" (hover lists them).
- **App disk = data + program:** the app's data folder plus its Docker images;
  a shared image layer is counted once.
- **Disk freshness:** the worker measures every 24 hours and right after an
  install or uninstall; the chart says "measured 3 h ago".
- **RAM** is read straight from each container's cgroup (no `docker stats`).

## 9. Settings > System

- Server card shows the same donuts, but its legend lists every app (no
  grouping) and splits disk into data and program.
- Activity card collapsed by default.

## 2 & 7. Calendar tab

- Left bar: Home, Apps, Calendar, Chat, Settings.
- Home no longer shows or loads the calendar.
- Calendar settings leave Settings; a gear button in the calendar header opens
  them in a side panel.

## 3 & 4. Settings > AI

- Fix the cause of the jump (one request for the page's data, started on
  entering Settings) and show every card at its final size with "Loading…".
- Where AI runs: with outside providers allowed, only the search model option
  shows (no model runner, no local chat model). No "Last changed" / "Changed
  by" rows.

## 5. Chat integrations

- List every installed app. Apps without an integration say so.
- Research MCP servers for each missing app (official first, then
  well-maintained community) and bring the owner a shortlist per app; each
  chosen one gets a review.yaml before it is offered. Missing today:
  Audiobookshelf, Baby Buddy, Beaver Habits, Dawarich, Navidrome, Outline,
  RomM; Actual Budget has a connector but no review.

## 6. Sign-in

- A stopped app shows grey "Off"; amber only for a real SSO problem.
- Both lists collapsible, collapsed unless something is wrong.
- Page moves to the bottom of Settings, just above "What's new".

## 8. Security & backups

- Backups and Backup schedule become one card. Without Kopia it is collapsed
  to one line, "Backups are off — Kopia isn't installed", with Install Kopia.
- "Mu3Lab itself" card removed. Scheduled backups already include Mu3Lab's
  core source; the combined card shows one "Mu3Lab core" line, and the
  recovery sheet moves into it as a button.

## Lighter and more reliable

- **Polling — fast only while busy.** Settings pages load once on open and
  after an action, no timers. The main refresh runs every 3 s only while
  something is in progress (install, backup, key check, model download), then
  stops; otherwise a heartbeat every 5 minutes, plus an immediate refresh on
  returning to the tab. The "3 failed refreshes" rule becomes "30 seconds".
- **Removed apps' leftovers** (images, containers, data folders of apps no
  longer in the catalog, e.g. Unsloth's 32.8 GB image) are cleaned up; data
  folders only after the owner confirms.
- **Old images pruned** after a successful update, keeping one previous
  version for rollback.
- **Cheaper RAM readings** from cgroups instead of `docker stats`.
- **Follow-up, not in this work:** server push (SSE) instead of polling.

### Chart details (asked during implementation)

- LiteLLM and FreeLLMAPI get their own slices like any app; "Mu3Lab core" is
  Authentik, Caddy, Vaultwarden, the tool gateway, the relay and the dashboard.
- Removed apps' leftovers and old program versions nothing runs show as one
  hatched "Can be freed" slice with a "Free up space" button (asks before
  deleting any data); it disappears when nothing can be freed.
- Each app keeps the same colour in both donuts, on Home and System. Core is
  neutral grey, "Other (not Mu3Lab)" light grey, Free an empty track.
- Hover shows the figures ("Immich — 52 GB, data 12 GB · program 40 GB");
  clicking an app opens its page.

### Chat integrations to build next (follow-up task, decided 2026-10-14)

Researched on GitHub; each gets a pinned revision, a review.yaml and a live check.

- **Outline:** the official MCP server built into Outline (`server/mcp` in
  outline/outline, present in the installed 1.10.1). Nothing extra runs.
- **Baby Buddy:** the official `babybuddy/babybuddy-mcp` (BSD-2-Clause).
- **Navidrome:** `Blakeem/Navidrome-MCP` (AGPL-3.0, community, actively maintained).
- **Beaver Habits:** none exists; Mu3Lab builds its own small connector on
  Beaver's REST API (list habits, mark done, streaks), like the Grocy one.
- Not now: Audiobookshelf, Dawarich, RomM (only tiny, new community projects).
