# Mu3Lab review and improvement plan

Date: 2026-11-10 · Branch reviewed: `fix/app-file-updates` at `517a20c` plus 20 uncommitted files

This is a whole-program review: what it does, how it is built, how it looks, and what to do next.
It respects decisions already made (terminal install, x86-64 only, Tailscale-only access, one
household, ContextForge owns tool choice, no fake app servers in tests). Where one of those
decisions now costs more than it did when it was made, it says so and leaves the choice to you.

## How this review was done

- Read the README, the technical deep dive, the earlier reviews and plans, and the project notes.
- Read the backbone code: installer step table, worker loop, API security, snapshot, job store,
  an app manifest, the dashboard shell and data loading. Measured the rest (sizes, counts, patterns).
- Ran the dashboard locally against a read-only copy of this server's real status and looked at
  Home, Apps, Discover, an app page, Settings and the phone layout.
- Checked GitHub: CI runs, open pull requests, releases.

Not done: the test suite was not run, the 29,000 lines of `ctl/` were not audited line by line, and
nothing on the live server was changed. I could not sign in to the live dashboard, so screens that
need live answers beyond the status snapshot (People, AI models, Chat) were judged from source.

## 1. Verdict

Mu3Lab is already a real product, well past a bundle of Compose files. One installer gives a
household sign-in, a vault, private access, 31 apps, backups with restore, updates with rollback,
and chat and voice assistants that reach into the apps. The dashboard is clean and reads well on a
phone. The engineering habits are good: typed API, validated manifests, pinned images, CI that
matches `make verify`.

The gap is between **built** and **proven**. Features have been added faster than they have been
released, tested on a real machine, or watched in operation. The next stretch of work should be
about trust more than reach: ship a release, test installs automatically, get backups off the
machine, and tell the owner when something is wrong. After that, simplify, and only then widen
the catalog.

## 2. What to keep

- **One folder per app** (`apps/<id>/app.yaml`, Compose, hooks, connectors) and the rule that
  shared code never names an app. The check found one leftover: `ctl/mcp_ops.py:312` names a
  connector ID.
- **Reusable rules** (`ctl/rules/`) for first-admin, per-person accounts, staged starts.
- **Durable jobs** with leases, resource locks and process tracking.
- **Typed API to typed dashboard** through OpenAPI, checked in CI.
- **Approved, pinned versions** and backup-before-update with automatic undo.
- **Dashboard structure**: Home, Apps, Calendar, Chat, Settings, with technical detail tucked away.
- **Honest wording** in the README and warnings about what is and is not protected.

## 3. Findings and plan

Each item says what was found, why it matters, and what to do. Sizes: S is about a day, M a few
days, L a week or more.

### Part A. Make it trustworthy (do first)

**A1. Nothing has ever been released.** (M)
The repository has no version tags and no GitHub release. `main` is 10 commits and 20 uncommitted
files behind the working branch, and one open pull request (#19) carries Jellyfin, power on/off,
backup changes and core updates together. Mu3Lab's own updater, the published connector images and
the release checksums have therefore never run for real. The only install that exists runs from
this development folder.
*Do:* merge #19, tag `v0.1.0`, and fix whatever the first real release run breaks. Then install
that tag on a spare machine or VM and update it to `v0.1.1` from the dashboard. From then on, one
topic per branch and a tag at least every two weeks.
*Value:* the update path is the feature every other user depends on, and it is untested.

**A2. Automatic install tests on a real machine.** (L)
The project notes hold about forty groups of "check this on the live server" items going back to
early October, most still unchecked. The acceptance document's VM journey is marked "not run".
The first live turn-off left the server unreachable. Unit tests (1,192 of them) cannot catch that
class of problem, and fake app servers were rightly removed.
*Do:* extend `tools/vm/fresh-vm.sh` into a scripted run with no person involved: fresh Ubuntu VM,
install with a Tailscale auth key, then for each app: install, healthy, sign-in check, back up,
restore, update, uninstall. Run it nightly on a self-hosted runner (this computer has the memory),
and before every tag. Start with the foundation plus five apps, and grow it.
*Value:* replaces the growing manual checklist, and makes it safe to keep adding apps. This is
the real-app testing the fake servers never gave.

**A3. Backups never leave the computer, and are optional.** (M)
Settings shows "Off-site copy: Not configured" with nothing behind it, and
`ctl/recovery_sheet.py:61` says copies to another computer come later. Kopia is optional and is
stopped on this server right now, so nothing is being backed up. A dead drive, theft or fire loses
everything, including the vault.
*Do:* (1) install Kopia with the core apps and switch the weekly schedule on by default;
(2) add a second destination in Settings: another computer on the tailnet, a USB drive, or
S3-compatible storage, all of which Kopia supports itself; (3) once a month, restore one small
app's backup into a scratch folder automatically and report the result; (4) fix the README, which
says media libraries are not backed up while the deep dive says they are.
*Value:* this is the difference between a hobby server and something a family can put its photos on.

**A4. Mu3Lab cannot tell anyone that something is wrong.** (M)
There is no notification code of any kind. A failed backup, a full disk, a stopped app or a
waiting update is visible only to someone who opens the dashboard.
*Do:* add a small notification service in the worker with three outlets: a bell and list in the
dashboard, web push from the installed phone app, and optionally ntfy as a small core app for
people who want it on every device. Send only what needs a person: backup failed or overdue, disk
below the limit, app down for ten minutes, update ready, sign-in key expiring. Add a weekly
"all is well" summary so silence is not mistaken for health.
*Value:* "low maintenance" needs the server to speak up, so nobody has to go and look.

**A5. Nothing watches the things that take the whole server down.** (M)
- This server's Tailscale key expires on 2027-04-19. Nothing in Mu3Lab reads that date. On that
  day every device loses the dashboard and every app at once.
- No drive health (SMART), temperature or "restart needed" checks. No operating-system security
  updates are set up.
- None of the 31 Compose files sets a memory limit, so one runaway app can freeze the machine.
- GPU sharing between Ollama, Immich, Jellyfin and speech has no arbiter (already on your list).
*Do:* a **Server health** card under Settings, System, fed by a daily worker check, with each row
wired to A4. At install, offer to switch off Tailscale key expiry for this server and turn on
automatic security updates. Add an optional `memory_max` to the manifest and set it for the heavy
apps. Before an install, compare the app's stated memory need with what is free.

**A6. One display error blanks the whole dashboard.** (S)
The dashboard has no error boundary. In the local run, one missing field in the AI settings data
turned the entire page white, with no message and no way back except editing the address.
*Do:* wrap each page in a boundary that shows "This page hit a problem", a Reload button, and the
navigation still working, and posts the error to the server log.

**A7. Chat can change anything, for anyone.** (M, needs your decision)
As the README says plainly, tools that change data run without approval, every household member
gets every assistant, and most connectors act through one shared app account. A child's chat can
delete a recipe, move a budget entry or remove a torrent, and a prompt hidden in a web page or
document can ask for the same.
*Do, keeping ContextForge in charge:* register two virtual servers per app, "read" and "full".
Members get "read" by default, administrators get "full", and an administrator can raise a member
per app. Later, where an app's API accepts the person's own sign-in, pass it through so the
connector acts as that person.
*Value:* the default becomes safe without bringing back the approval queue.

**A8. Housekeeping that erodes trust.** (S)
Two Dependabot pull requests have been open with failing CI for up to two weeks. Merge or close
them, and protect `main` so it only takes passing pull requests.

### Part B. Simplify the inside

**B1. The worker does one job at a time.** (M)
`ctl/worker.py` claims a job and runs it to the end before looking for the next. Copying a big
app's databases for a backup may take up to 2 h 50 min, a model download or the backup-drive move
can take longer, and during that time "Start Jellyfin" waits in the queue. Per-app locks already
exist, so the safety work is done.
*Do:* run a small pool, for example one heavy job and two light ones, each holding its own app's
lock. Show "Waiting for: Immich backup" on a queued job.

**B2. Upkeep is hand-written inside the worker loop.** (S)
Ten periodic tasks sit inline in `run()`, each with its own timer and `try/except` that logs and
moves on. A task that fails every time is invisible outside the log.
*Do:* one table of tasks (name, pace when active, pace when idle, function), with last run and
last error stored and shown on the Server health card.

**B3. `ctl/` is flat and the installer is one 2,849-line file.** (M, no behaviour change)
Ninety modules sit side by side at the top of `ctl/`, and `ctl/install.py` holds 112 functions.
*Do:* move files into packages by subject, in separate commits with no logic changes:
`host/` (install steps, Docker, Tailscale, privilege), `ai/` (providers, models, voice),
`chat/` (assistants, ContextForge, connectors), `backup/` (Kopia, dumps, rebuild, recovery sheet),
`household/` (people, access, vault). Split the installer into one file per group of steps.
*Value:* this is standard practice at this size. It makes changes smaller to review and makes it
obvious where a new feature belongs.

**B4. The always-on core is heavy and long.** (decision, then M to L)
Measured on this server, the core uses 1.8 GB of memory before any optional app, 1.3 GB of it
Authentik. A chat message passes Open WebUI, LiteLLM, FreeLLMAPI, then the provider. A tool call
passes Open WebUI, ContextForge, a connector, then the app. Every hop is a version to pin and a
place to fail.
*Do:* (1) publish a tested minimum (for example 16 GB) and say so in the installer; (2) measure
whether LiteLLM alone can do what FreeLLMAPI adds, and drop one if it can; (3) keep Authentik: it
is expensive, but replacing it would undo months of sign-in work for little user-visible gain.

**B5. Two chat apps, two integrations.** (decision)
Open WebUI is the default and gets shared assistants and forwarded sign-in. LobeChat needs
per-person approval and per-person keys, and has its own sync code. Every chat feature is built
and checked twice.
*Recommend:* keep Open WebUI only, and mark LobeChat "community, as-is" or remove it.

**B6. Catalog pruning.** (decision)
Each app costs version reviews, a connector, and test time. Candidates to drop or mark as-is:
SurfSense (5 GB image, no accounts, no API, overlaps with Open WebUI's own document search),
Firecrawl (several backing services, overlaps with SearXNG for most questions), and the niche
trackers if nobody in the household uses them. Let real use decide: which apps does the household actually open?

**B7. Documentation is mostly history.** (S)
`docs/` holds about 9,700 lines, most of them dated plans and reviews. Some now contradict the
code: the expansion tracker lists Kopia as "not started", and acceptance results date from
October 4. Move dated material to `docs/archive/`, keep the six living documents, and add one
`ROADMAP.md` (this plan, trimmed) as the single place for "what next".

**B8. Left as decided.** The dashboard and worker having full control of Docker stays deferred
until Mu3Lab is offered to people outside your household. At that point, a small separate helper
that only accepts a fixed list of operations is the standard answer.

### Part C. Product: screens, settings and features

**C1. "Add a person" as one guided flow.** (M)
Today, adding someone creates their account and an invitation link. Getting them onto Tailscale,
into Bitwarden and into each phone app is explained in separate places. Make one page per person,
opened from a QR code: join the private network, set a password, install Bitwarden, then the phone
apps for the apps they will use, each with its own QR code. Show the administrator how far each
person has got. Finish the guest work on `access/guest-invites` inside the same flow.

**C2. "Move in" importers.** (L, highest product value)
The hardest part of leaving the big services is bringing the data. Add guided imports as jobs:
Google Photos Takeout into Immich, Google Drive or Dropbox into Nextcloud, a password export into
the vault, browser bookmarks, calendar files. Each is a known tool plus a progress screen.

**C3. Starter bundles.** (S)
The catalog API already carries `profiles`, but only one exists. Offer three or four on first
run and in Discover: "Family photos and files", "Media centre", "Paperwork and money", "AI only",
each with total download, disk and memory shown before installing.

**C4. Server health and notifications.** Covered by A4 and A5, shown as one card and one bell.

**C5. Readable addresses.** (investigate, M)
Every app is the same name with a different port (20 of them). Nobody remembers that Immich is
`:8449`, and typing an address on a TV is painful. Investigate per-app names on the tailnet
(Tailscale Services) and, for the home network, local names. If it holds up, keep the ports as a
fallback so nothing breaks.

**C6. Idle apps stop themselves.** (M)
Whole-server on/off exists. Add a per-app option, "stop when unused for N hours, start when
opened", for heavy apps, reusing the wake page. This frees memory and GPU without anyone managing it.

**C7. Routines.** (L, later)
Scheduled assistant tasks: "every morning, send me today's calendar and dinner plan", "when a
bill lands in Paperless, add it to the budget". The worker, LiteLLM and ContextForge already hold
the pieces, and A4 delivers the result. Build it only after A7, because a routine is an unattended
write. n8n, already specified in the expansion plan, is the alternative for people who want a
full workflow builder.

**C8. Smaller dashboard improvements.** (S each)
- Home: a member's Home should lead with "Open" tiles and their own setup steps, with nothing
  that they cannot act on.
- Apps list: the Sign-in column is empty for most rows and says "Protected, separate login" for
  others. Show it only as an exception badge, as the design refresh intended.
- Discover: say why an app is "Unavailable" on the card itself.
- Backups: a Home tile with "Last backup: 2 days ago, next: Sunday" for administrators, so backup
  state shows up before there is a warning.
- Wording: keep all user-facing text in one place per feature. This costs little now and is the
  first step if a second language is ever wanted.

**C9. Apps to add, in order.** Add one only when A2 can test it.
1. **Home Assistant** (with Mosquitto). Already specified. It is the app most households ask for,
   and it gives the voice assistant something physical to do.
2. **A shared to-do and chores app** (Vikunja or Donetick). The clearest gap for a household. It
   also makes the calendar and assistants more useful.
3. **Stirling-PDF.** No accounts, no data to back up, useful with Paperless on day one.
4. **Requests and library managers for Jellyfin** (Jellyseerr with Sonarr, Radarr, Prowlarr),
   administrators only, following the qBittorrent rules.
5. **An ebook library** (Kavita or Calibre-Web), sharing the media folder model.
6. **Bookmarks with AI tagging** (Karakeep) or a news reader (FreshRSS).
7. **AdGuard Home** for ad blocking on every device through the tailnet's DNS setting.
8. **HomeBox, Frigate, ComfyUI, n8n** as already specified, when there is a need.
Sign-in support and licence need checking against the exact release of each before it is promised.

## 4. Suggested order

| Step | Contents | Result |
| --- | --- | --- |
| 1. Release | A1, A6, A8 | A tagged version exists and updates itself |
| 2. Proof | A2 (foundation + five apps), B7 | Installs are tested nightly without you |
| 3. Safety net | A3, A4, A5 | Backups off the machine, and the server speaks up |
| 4. Safer chat | A7 | Members read, administrators change |
| 5. Simpler inside | B1, B2, B3, then B4 to B6 as decided | Faster, smaller, easier to change |
| 6. People | C1, C3, C8 | A new household member is set up in ten minutes |
| 7. Reach | C2, C5, C6, then C9 and C7 | Importers, readable addresses, new apps |

Steps 1 to 3 are the ones I would not reorder. Until they are done, each new app or feature adds
to the pile of unverified work.

## 5. Decisions needed from you

1. **A7:** members get read-only assistants by default. Yes or no?
2. **B5:** keep LobeChat as a supported second chat app, mark it as-is, or remove it?
3. **B6:** which apps does the household actually use? Any you would drop?
4. **A3:** make Kopia part of the core install with backups on by default?
5. **A2:** may the nightly test VM run on this computer, with a Tailscale auth key kept for it?
