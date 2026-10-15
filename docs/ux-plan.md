# User-experience pass (branch `ux/user-experience`)

Owner's 20 issues from 2026-10-12, with the decisions taken for each. Items
marked *default* were not asked; they are the obvious fix and can be changed.

## Installer

1. **Starting stage shows progress.** "Starting apps: 2 of 4 — LiteLLM
   (waiting for it to answer, 0:42)" with a checklist under it: finished apps
   ticked, the current one marked, the rest listed.
2. **Discover installs finish stopped.** Apps installed from Apps > Discover
   are started only long enough to finish setup (accounts, SSO, vault logins),
   one at a time, then stopped. The installer itself is unchanged: after
   install, Authentik, Caddy, Vaultwarden and the chat stack (Open WebUI,
   LiteLLM, FreeLLMAPI, Ollama) stay running. When a Discover batch finishes,
   a toast says "N apps installed and ready to start"; each app shows Stopped
   with a Start button on its Home quick panel, Installed row and app page.
9. **Kopia and Firecrawl become optional** (not installed by the installer).
   - Firecrawl moves to Discover > Knowledge & search.
   - Kopia moves to Discover > Backups & system; once installed it shows under
     Installed > Infrastructure (12).
   - Without Kopia: Settings > Security & backups shows one "Backups are off —
     Install Kopia" callout in place of the schedule/backup/recovery cards;
     each app's Backups card says backups are off with an Install link; Home
     shows a quiet attention row until Kopia is installed or dismissed.
   - App updates still run without Kopia, with the warning "No backup will be
     taken, so this update can't be undone. Install Kopia to turn on backups."

## Home

3. **Status bar: four items per row.**
   - Security: Caddy, Authentik, Tailscale, Vaultwarden.
   - AI: Ollama, LiteLLM, FreeLLMAPI, the household chat app.
   - System: Memory, Disk, CPU, Docker; Background worker appears only when it
     is not running.
4. *default* Remove the "Finish setting up Mu3Lab" attention row.
5. **Clicking an app on Home opens a quick panel**: status, memory, Start /
   Stop / Restart, sign-in type, Open button, link to all details.
7. **Get started can always come back.**
   - When hidden, Home shows a small "Show Get started" button.
   - New "Get started" section at the top of Settings (everyone): compact
     checklist + "Show on Home" switch.
   - The user menu (6) has a "Get started" item.

## Shell

6. **User menu** (bottom-left name): Settings, Get started, Update, Apps,
   What's new, Changelog (GitHub Releases), GitHub, theme, Log out.
   - "What's new" is a page in Settings with the release notes of the
     installed and newer versions.

## Apps

8. **Everything can be stopped except Authentik and Caddy.** Firecrawl
   becomes stoppable. Vaultwarden becomes stoppable, and its Stop
   confirmation warns that Bitwarden can't sync or save new logins while it
   is stopped.
12. Kopia is listed under Installed > Infrastructure.
13. **Discover is grouped by category**, with heading sections and filter
    chips at the top:
    - Finance: Actual Budget
    - Home & family: Grocy, Mealie, Baby Buddy, Beaver Habits
    - Photos & media: Immich, Audiobookshelf, RomM
    - Documents & notes: Nextcloud, Outline, Paperless-ngx
    - Places & travel: Dawarich (with Photon), AdventureLog
    - Knowledge & search: SurfSense, Firecrawl
    - AI & voice: LobeChat, Speaches
    - Backups & system: Kopia
14. *default* "Download at once" offers 1 up to the number of apps selected,
    and is hidden when only one app is selected.
16. "Installation complete" becomes a toast (see 15) and never reappears. The
    progress card shows only while installs are running or have failed.
18. **GPU versions.** Audit every app; add an NVIDIA version wherever the
    app's makers publish one (Immich machine learning and SurfSense's
    document reader are likely). Install picks the version for the current
    compute mode. If the mode changes later, each affected app shows a
    "Switch to GPU version" (or CPU) button; nothing changes until pressed.
    App pages show "Running on: GPU/CPU".
19. **Photon is a hidden part of Dawarich.** It is not listed or installable
    on its own. Dawarich's page shows "Address lookup (Photon)" status and
    size, and Dawarich's start/stop/uninstall cover Photon.
20. **Installed rows use fixed columns**: app (icon, name, tagline) · status ·
    sign-in · update · fixed-width Open button (always "Open"; details like
    "API" go in its tooltip) · chevron. Empty cells keep their width.

## Chat

11. **Open WebUI is the default chat app, with full parity first.** Before
    the switch, Open WebUI gets the per-app assistants, app connectors (MCP)
    and change approvals that LobeChat has today. The installer installs Open
    WebUI; LobeChat moves to Discover > AI & voice. Settings > AI has a
    household "Chat app" picker listing installed chat apps; the Chat page
    shows the chosen one; the other stays installed (stop/uninstall as usual).
15. **Chat page notices.**
    - The connector/operator note is removed.
    - "Where chat runs", "<app> is now available in chat" and errors become
      toasts.
    - "Assistants ready X of Y" moves to Settings > Chat integrations.
    - The one-time Authorize step stays as a thin bar until done (it may not
      be needed with Open WebUI).

## Settings

10. *default* Backup schedule card: the three dropdowns (frequency, day,
    time) sit side by side on one row under the description.
17. *default* Remove the "Finish setup" card from Settings > System.

## App-wide

- **Toasts.** One thin toast style for passing notices across the
  dashboard. It disappears after about 4 seconds and stays while hovered or
  focused.

## Follow-up: Unsloth and Navidrome (decided 2026-10-12)

> **Reversed on 2026-10-14:** Unsloth Studio was removed. It was too big
> (about 33 GB unpacked) and a poor fit for what Mu3Lab needs from a model
> runner. Ollama is again the core runner that the installer sets up, and the
> install needs 20 GB of free disk again. Item 21 and the deferred smaller-image
> plan below are kept only as a record.

21. **Unsloth Studio is the default model runner, and Ollama is the alternative.**
    It works like the chat app picker.
    - The installer always installs Unsloth Studio, on every server. It does
      not install Ollama. On a server without a GPU, Unsloth runs on the CPU.
    - Ollama moves to Discover > AI & voice.
    - Settings > AI gets a "Model runner" picker that lists the installed
      runners.
    - Unsloth runs both the local chat model and the search (embedding)
      model.
    - Switching runners downloads the chosen models again in the new runner
      and rebuilds the search index. The picker warns about this before it
      switches.
    - Mu3Lab's own model picker keeps the reviewed list (local models
      decision).
    - **Only operators reach Studio's web app.** Authentik limits the page to
      operators. Studio's own sign-in can't be turned off, so Mu3Lab creates
      a Studio account for each operator and saves its password in that
      operator's vault.
    - Inside Studio, operators can download any model and fine-tune.
    - Household members can't reach Studio or change models. They chat in
      the chosen chat app with the models operators have loaded.
    - LiteLLM reaches Studio's API with a key that Mu3Lab generates. The API
      is not opened without a key.
22. **Navidrome is the music app, a Spotify replacement in Discover > Media.**
    - The household shares one music library. It lives in Navidrome's data
      folder, so Kopia backs it up with the app.
    - While Navidrome is installed, Nextcloud shows that folder to everyone as
      "Music", so people add songs by dropping them there.
    - The web page signs people in through Authentik.
    - Phone apps use Navidrome's Subsonic API, which needs a password. Mu3Lab
      creates each person's Navidrome account and saves that password in
      their vault, the same as other non-SSO logins.

## Deferred: a smaller Unsloth Studio image (decided 2026-10-13)

This branch still installs Unsloth's own image: a 10.7 GB download (about 33 GB
unpacked), most of it PyTorch and fine-tuning tools Mu3Lab never uses. The next
change replaces it; nothing below is built yet.

- **Decided:**
  - Unsloth stays the default runner, for chat and search only (no
    fine-tuning).
  - It runs in a container, and always on the GPU: no CPU build on NVIDIA or
    AMD machines.
  - Document search may be unavailable for the first minutes after install.
- **Planned:** Mu3Lab builds its own image with Unsloth's installer in GGUF-only
  mode (`--no-torch`), plus one GPU layer per vendor:
  - NVIDIA: Unsloth's CUDA build of llama.cpp (0.24 GB) and NVIDIA's CUDA
    runtime and cuBLAS libraries (0.6 GB).
  - AMD: llama.cpp's Vulkan build (31 MB) and Mesa's Vulkan driver.
  - Estimated downloads: about 1.5 GB on NVIDIA and under 1 GB on AMD.
  - The installer no longer waits for it: Studio and its first models download
    in the background.
- **Measured (2026-10-13):**
  - The GGUF-only install is 2.3 GB on disk. About 1.6 GB stays in an image
    once the build-only Node.js and the download cache are dropped.
  - Without the CUDA libraries on disk, Unsloth's installer falls back to a
    CPU-only llama.cpp build.
- **Context:** other apps' GPU images each carry their own CUDA libraries and
  share no layers: Immich adds 2.3 GB, SurfSense 3.8 GB and Speaches 3.2 GB
  over their CPU images. Only the host's GPU driver is shared.
- **Open questions:**
  - What should happen on a machine with no GPU at all?
  - Is Vulkan (rather than ROCm) right for AMD?
  - For branch installs: build the image on the machine during setup, or
    publish a test image to GHCR?
