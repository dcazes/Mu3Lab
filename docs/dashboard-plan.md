# Home dashboard plan

Status: decided by the owner on 2026-11-23 (section 6). Research sources are listed at the end.

The goal is a Home board that is pleasant to arrange, that has widgets for every app that work well, and that
is built as much as possible from code other people maintain. Mu3Lab keeps its own dashboard: no extra
dashboard app. It uses maintained libraries for the two hard parts, the board's drag-and-resize engine and
talking to each app.

## 1. Why the current board feels bad

The board uses react-grid-layout with free placement (gaps allowed). That library pushes a widget down the
moment the dragged widget overlaps it by a single cell. Each push builds on the previous one. Mu3Lab's own
rule then stops pushed widgets from ever moving back up. So:

- getting near a widget shoves it down a whole widget's height;
- moving back makes it go further down instead of returning;
- widgets drift down the board as you arrange.

None of this is a missing setting. That library has no way to "remember where things were before this
drag". Its open issues also include reordering bugs (#2151) and resize handles that the keyboard cannot reach (#1907).

## 2. The board engine (decision D1)

How the best boards handle a drag:

- **They work from a snapshot.** Every preview is worked out from where things were when the drag started,
  so nothing builds up. Moving a widget back puts the others back.
- **They have a threshold.** Another widget moves only once you cover more than half of it.
- **They swap.** Dropping a widget onto one other widget swaps the two.
- **Escape cancels.** It returns everything to where it was.

| Option | What it is | Gives the behaviour above | Maintained by | Notes |
|---|---|---|---|---|
| **A. gridstack.js 14** (recommended) | Grid library with an official React wrapper (`gridstack/dist/react`) | Yes, built in: the 50% threshold, pushed widgets sliding back to their original row (float mode), swap | gridstack project, MIT, 9.3k stars, v14.1.1 released 2026-11-17 | Also built in: drag in from the widget list, touch, one-column breakpoint, size-to-content. Keyboard moving is ours to keep. |
| B. Copy Homarr's engine | Homarr's own engine (on the dnd-kit drag toolkit), copied into Mu3Lab | Yes, the behaviour above, with tests | Homarr (Apache-2.0, compatible with AGPL), but our copy would be ours to maintain | The most proven behaviour, but a copy does not update itself. |
| C. Keep react-grid-layout, gravity on | Turn on "pack everything to the top" | Partly: widgets return because everything packs up | react-grid-layout, MIT | No gaps can ever be left. Still pushes on one-cell overlap. |

Recommendation: **A**. It is the only option that gives the good behaviour without Mu3Lab owning the
engine. Homarr's engine is the reference for anything gridstack leaves to us: keyboard moving, the
cancel snapshot, and "never push a section".

## 3. What the board should do

Must = needed for a good experience. Should = expected, build next. D = needs your decision.

### Arranging (edit mode)
- **Must:** Edit, then Done to save, Cancel to throw changes away (as now).
- **Must:** Drag by the grip; a shaded preview shows where it will land.
- **Must:** Other widgets move only after more than half is covered, and slide back when you move away.
- **Must:** Drop onto a same-size widget to swap them.
- **Must:** Resize from the right edge, the bottom edge and the corner. The widget redraws at its new size as
  you drag, and each widget has a smallest and largest size.
- **Must:** The page scrolls by itself when you drag near the top or bottom edge.
- **Must:** Undo and redo while editing (Ctrl+Z / Ctrl+Shift+Z, plus buttons), and Escape cancels a drag.
- **Must:** Keyboard: arrows move, Shift+arrows resize, as in Homarr.
- **Must:** The number of columns is chosen per person (as now).
- **Should:** "Tidy up", one button that packs everything up to close the gaps.
- **Should:** Duplicate a widget, and a widget menu with Settings, Refresh, Duplicate and Remove.
- **D2:** Free placement with gaps (recommended), gravity (everything packs to the top), or each person chooses.

### Adding widgets
- **Must:** An "Add widget" list grouped by app, showing only apps that are installed and that this person
  may use, with a search box.
- **Must:** Each entry shows a small live preview of the widget.
- **Must:** A new widget goes into the first free space near the top; it can also be dragged from the list onto the board.

### Widget states (every widget the same way)
- **Must:** Loading shows a grey placeholder shaped like the content, not a spinner.
- **Must:** Each state says what is wrong and offers the fix: **app stopped** (with Start for administrators),
  **app not installed**, **app unreachable** (with Retry), **you need to connect** (a person's own data).
- **Must:** Last-known data stays on screen while it refreshes, with "updated 2 min ago" on hover.
- **Must:** Sensible refresh rates per widget: about 10 s for downloads and now playing, 5 min for counts,
  15 min for weather. Refreshing pauses when the tab is hidden.
- **Should:** A Refresh item in each widget's menu.
- **Should:** Tapping a widget's title opens the app; tapping an item opens that item in the app.

### Phone and tablet (D3)
- **Must:** On a phone the board is one column. Widgets keep their height and cannot be dragged.
- **D3:** Phone order follows the computer layout (top-left first) and each widget can be hidden on the phone
  (recommended). Or the phone gets its own separate arrangement, edited on the phone (Homarr's way).

### Organising (D4)
- **D4:** One board with collapsible sections such as "Media" or "Home" (recommended, as Homarr's
  containers), several boards as tabs, or one plain board.

### Household and personal (D5)
- **Must:** Each person arranges their own board; widgets show only what that person may see.
- **D5:** An administrator makes a **household board** that everyone starts from, with "Reset to household
  board" (recommended). Or keep Mu3Lab's fixed starter board.

### Look (D6)
- **Must:** Widgets fill their whole cell with no odd padding, which is the rule Android gives for widgets people keep.
- **D6:** Optional appearance settings: a background picture, see-through widgets, compact spacing.

### Widget design rules (every widget)
1. **One purpose.** Each widget answers one question ("what's downloading?", "what's for dinner?").
2. **It adapts to its size.** Small shows the headline number. Wider adds a list. Taller adds more rows.
3. **It shows the important number first,** with a label underneath. Lists show at most what fits, then "and 4 more".
4. **It tells the truth.** Never a stale "0" when the app is down; show the state instead.
5. **It shows only what the viewer is allowed to see.** Administrators manage, they do not snoop: personal
   data appears only on the owner's own board.

## 4. Talking to the apps: package shortlist

Mu3Lab's server (Python) calls each app with credentials Mu3Lab already holds. Where Home Assistant
uses a package, that package is battle-tested by a very large user base. "Platinum" is Home Assistant's
highest quality grade.

| App | Use | Version, released | Licence | Why |
|---|---|---|---|---|
| Immich | `aioimmich` | 0.18.0, 2026-10-08 | Apache-2.0 | Home Assistant uses it (platinum) |
| Paperless-ngx | `pypaperless` | 6.0.4, 2026-11-22 | MIT | Home Assistant uses it (platinum) |
| qBittorrent | `qbittorrent-api` | 2026.10.1, 2026-10-21 | MIT | Home Assistant uses it; very active |
| Sonarr, Radarr | `aiopyarr` | 23.4.0, 2023-04-09 | MIT | Home Assistant uses it; old but stable APIs |
| Nextcloud | `nextcloudmonitor` | 1.5.1, 2024-06-24 | MIT | Home Assistant uses it for server info |
| Audiobookshelf | `aioaudiobookshelf` | 0.2.2, 2026-11-02 | Apache-2.0 | Async, recent (not in Home Assistant) |
| Authentik | `authentik-client` | 2026.8.6, 2026-11-20 | MIT | Official, generated from Authentik's API |
| Ollama | `ollama` | 0.6.3, 2026-09-29 | MIT | Official |
| Mealie | `aiomealie` | 2.1.4, 2026-11-01 | MIT | Home Assistant (platinum), **but needs Python 3.13** (D7) |
| Seerr | `python-overseerr` | 0.10.1, 2026-11-10 | MIT | Home Assistant (platinum), **but needs Python 3.13** (D7) |
| Jellyfin | Direct API | n/a | n/a | Home Assistant's package is incomplete; Jellyfin publishes an OpenAPI description |
| Navidrome | Direct API (Subsonic) | n/a | n/a | Only a basic package exists; the Subsonic API is small and stable |
| Prowlarr | Direct API | n/a | n/a | No package; same style as Sonarr and Radarr |
| Grocy | Direct API | n/a | n/a | `pygrocy` was last released in 2024 and Home Assistant dropped Grocy; Grocy ships an OpenAPI file |
| Baby Buddy | Direct API | n/a | n/a | Ships an OpenAPI file |
| Dawarich | Direct API | n/a | n/a | Ships an OpenAPI (Swagger) file |
| Beaver Habits | Direct API | n/a | n/a | Small documented API (login, habits, completions) |
| HomeBox, Wallos, RomM, AdventureLog, Outline, n8n, LiteLLM | Direct API | n/a | n/a | No maintained package; each needs only a few read calls |
| Kopia | Mu3Lab's own backup status | n/a | n/a | Mu3Lab already runs and reads the backups |
| Actual Budget | `actualpy` (to check) | 0.22.4, 2026-09-28 | MIT | It downloads the whole budget file; to check that it is fast enough |

"Direct API" means a few documented web requests per app, which Mu3Lab owns. They are small and covered by
each app's published API description, and they are checked whenever Mu3Lab approves a new app version.

## 5. The widgets

Who sees each widget follows who may use the app. "Admins" means administrators only. "Own data" means
each person's own account.

**Apps that already have widgets elsewhere** (Homepage/Homarr show what is useful):

| Widget | Shows | Who |
|---|---|---|
| Jellyfin | Library counts; "Continue watching" (own data); number streaming now | Everyone |
| Seerr | Requests: pending, approved, available; recent requests | Everyone |
| Upcoming releases | Next 14 days from Sonarr and Radarr; can also appear in the Calendar widget | Everyone |
| Downloads | qBittorrent speed, active downloads with progress, Sonarr/Radarr queue | Admins |
| Indexers | Prowlarr grabs, queries, failures | Admins |
| Backups | Last backup per app, next run, problems | Admins |
| Sign-ins | Authentik: people, sign-ins and failed sign-ins in the last 24 h | Admins |
| Server storage | Nextcloud free space, files, shares | Admins |
| Photos | Immich "On this day" and album slideshow (own data); server stats for admins | Everyone / admins |
| Documents | Paperless inbox count and latest documents (own data) | Everyone |
| Subscriptions | Wallos: this month's cost, next renewal (own data) | Everyone |
| Recipes | Mealie: counts | Everyone |
| Audiobooks | Continue listening (own data); library counts | Everyone |
| Music | Navidrome now playing (own data); library counts | Everyone |
| Games | RomM platforms, games, recently added | Everyone |
| Inventory | HomeBox items, total value, warranties ending soon | Everyone |

**New widgets nobody has built** (custom, same design rules):

| Widget | Shows | Who |
|---|---|---|
| Habits | Today's habits with tick boxes, current streaks (Beaver) | Own data |
| Meal plan | Today's and tomorrow's meals (Mealie) | Everyone |
| Pantry | Expiring soon, missing items, shopping list count (Grocy) | Everyone |
| Chores | Due today and overdue (Grocy) | Everyone |
| Baby | Last feeding, sleep, diaper, with times since (Baby Buddy) | Everyone |
| Budget | Spent this month vs budget, top categories (Actual) | Own data |
| Places | Where you are now, today's visits (Dawarich) | Own data |
| Trips | Next trip and countdown (AdventureLog) | Own data |
| Wiki | Recently updated pages (Outline) | Everyone |
| Automations | Failed runs in the last 24 h (n8n) | Admins |
| AI usage | Today's requests and cost (LiteLLM), loaded models (Ollama) | Admins |

The Mu3Lab widgets (Apps, Weather, Calendar, Stocks, Ask, Thermostat, Camera) move over to the new
engine unchanged.

## 6. Decisions (owner, 2026-11-23)

- **D1 Board engine: gridstack 14** with its official React wrapper. Homarr's engine is the reference for
  keyboard moving and Escape-cancel.
- **D2 Placement: each person chooses.** An edit-mode switch between **Free** (gaps allowed, pushed widgets
  slide back, with a "Tidy up" button) and **Gravity** (everything packs to the top). Free is the default.
- **D3 Phone: one column in the computer board's order,** with a "Hide on phone" switch on each widget.
- **D4 Organising: collapsible sections in one board** ("Media", "Home"…), each folding to its title bar.
- **D5 Household board: yes.** An administrator arranges it; new people start from it; "Reset to household board" for anyone.
- **D6 Look: all three:** a background picture (with blur and dimming), see-through widgets and compact spacing.
- **D7 Python: move Mu3Lab to Python 3.13,** so Mealie and Seerr use Home Assistant's packages. Done as its
  own step with a full check of the server before any widget depends on it.

## 7. Build order

1. **Done 2026-11-23.** Python 3.13 for the server (D7). Same 75 locked packages, 1,244 server tests run;
   the only failures were already there on 3.12 and are fixed.
2. **Done 2026-11-23.** gridstack board (section 3, arranging). Checked in the browser on the owner's real board:
   a widget only moves once more than half covered, pushed widgets return, same-size widgets swap (also in
   Free: Mu3Lab's `gridEngine.ts` tries gridstack's swap before pushing), edge and corner resizing, Free and
   Gravity, Tidy up (straight up only), undo and redo, keyboard moving, add to the first free space, Cancel,
   Hide on phone, and the one-column phone board. Escape to cancel a drag is not offered by gridstack
   (Undo does the same job).
3. **Done 2026-11-23.** The widget template: one shared app widget, with headline figures, a list,
   and plain states (not installed, starting, stopped with Start for administrators, could not answer
   with Try again). It also has grey placeholders while loading, keeps the last figures when a refresh
   fails, and refreshes on each widget's own clock (paused while the tab is hidden).
   - **Widget menu:** Refresh and Open the app in view mode, Duplicate while editing.
   - **Add list:** grouped by app, with a search box and live previews; you can drag a widget from it
     onto the board (one Undo takes it back).
   - **First real widgets:** Backups (Kopia), Sign-ins (Authentik) and Local AI (Ollama).

4. Widgets for the running apps first, checked against real data with each app started; then the rest.
5. The custom widgets.
6. Household board, sections and look, as decided.

## How an app adds a widget

1. In the app's `app.yaml`, add an entry under `widgets:`. It gives:
   - `id`, `title` and `description`;
   - `audience` (`household` or `operators`);
   - `refresh_seconds`;
   - `needs_running` (false for figures Mu3Lab keeps itself);
   - sizes: `smallest`, `largest` and `start`, in columns of 12 and rows.
2. In `apps/<id>/widgets.py`, add a function named after the id (dashes as underscores). It takes a
   `WidgetContext` and returns `{stats, items, items_title, empty, note}`. The context gives:
   - `ctx.get(path)` for the app's API on this server;
   - `ctx.env()` for its secrets;
   - `ctx.credentials(connector)` for a chat connector's key;
   - `ago()` and `until()` (from `ctl.app_widgets`) for times.

   Raise `WidgetError` with a plain sentence when the app does not give its figures.
3. `ctl/app_widgets.py` does everything else: access, states, caching and trimming. The dashboard draws
   it with no new code.

## Sources

- gridstack 14.1.1 engine source (float-mode restore of `_orig` rows, 50% coverage rule, swap):
  github.com/gridstack/gridstack.js `src/gridstack-engine.ts`; React wrapper `react/README.md`; npm registry.
- Homarr engine (snapshot transactions, swap, pinned reflow): github.com/homarr-labs/homarr
  `apps/nextjs/src/components/board/sections/grid/dnd/engine.ts`, `layout/reflow.ts`; Homarr board docs
  (homarr.dev/docs/management/boards): keyboard moving, layouts per screen size, default boards, access levels.
- react-grid-layout v2 README and open issues #2023, #2151, #1907.
- Home Assistant core manifests (`homeassistant/components/<app>/manifest.json`): package pins and quality grades.
- PyPI JSON for every package above: version, date, licence, required Python.
- Android widget design guides (single purpose, adaptive sizes, fill the cell).
- Homepage and Dashy docs for widget fields and refresh/status conventions.
