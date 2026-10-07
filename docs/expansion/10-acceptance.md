# 10. Acceptance

Unit tests prove pieces. Acceptance proves people can use the result.
Everything here runs in a **disposable VM** ([../acceptance.md](../acceptance.md)
"Isolated installation"), never on the owner's stack. Steps that need a real
phone, the real GPU or real hardware are written as **owner steps** and
handed to the owner.

## 1. VM setup

```bash
export MU3LAB_VM_DIR="$HOME/.cache/mu3lab-expansion-acceptance"
export MU3LAB_VM_SSH_PORT=2231
tools/vm/fresh-vm.sh up
tools/vm/fresh-vm.sh ssh
# inside the VM
cd Mu3Lab && ./install.sh
```

- Give the VM enough resources for the app under test (Dawarich + Photon and
  Open WebUI want 8 GB+ RAM; VERIFY how `fresh-vm.sh` sets memory and disk,
  and raise them via its environment variables if supported).
- The VM has **no GPU**: GPU apps run in CPU mode.
- Tailscale approval needs the owner (or the tester) once per VM.
- Use a second tailnet device (the host machine, or a second VM) for
  "another device" tests (forged headers, webhooks).

## 2. Platform checks (after Phase 1)

| Check | Expected |
|---|---|
| Port registry | Adding a deliberately clashing app manifest fails catalog load with a clear message. |
| Media root | Created with the owner's ownership; uninstall with delete-data keeps it. |
| GPU card | VM: "No graphics card". Owner host (owner step): the card lists apps and memory. |
| Route features | Rendered blocks validate with the pinned Caddy; forged headers replaced on every path. |
| Soft deps | Installing ComfyUI rewires Open WebUI once; removing it rewires back. |
| Per-person accounts | Fake app test passes; HA covered in its own journey. |
| Host network check | An app listening on `0.0.0.0` fails install with `host_port_exposed`. |
| Install gates | Frigate's Install disabled until a camera is entered; the API refuses too. |

## 3. Standard per-app journey

Run for **every** app in Phase 5, then the app-specific steps in its spec.

1. **Discover → Install.** All stages green. Note the install time.
2. **Open as owner.** Lands signed in (SSO apps) or Bitwarden fills
   (vault-login apps). No setup wizard, no default-account screen.
3. **Default credentials** (if the image ships any) fail directly and
   through the route.
4. **Sign out of Authentik**, open again: goes through Authentik (gated or
   OIDC apps); HA shows its own login.
5. **Second person** (household) added in Settings → People, completes
   invitation, opens the app: gets the designed access (role, shared or
   private data).
6. **Forged identity** from another tailnet device (trusted-header apps):
   ignored.
7. **Chat** (apps with connectors): the assistant appears for a connected
   person; a read returns seeded data; a write asks first (LobeChat prompt or
   Mu3Lab approval); declining changes nothing.
8. **Backup → change → restore**: data returns; sign-in still works.
9. **Stop / Start / Restart**: sign-in and connectors survive.
10. **Update path**: approve a newer patch version on a task branch (if one
    exists), update from the dashboard, roll back via the pre-update backup.
    Restores the old release.
11. **Uninstall keeping data → reinstall**: same owner, no duplicates,
    connectors reprovisioned (keys reused).
12. **Uninstall deleting data → reinstall**: behaves as fresh; media
    folders untouched.
13. **Interrupted install** (kill the worker mid-install, restart): resumes
    or fails with a clear retry; no duplicate accounts.

Record each step's result in `docs/<id>-validation.md` (pass / fail with
details / not applicable with reason).

## 4. Cross-cutting journeys (Phase 6)

| # | Journey | Expected |
|---|---|---|
| X1 | Fresh install choosing **Open WebUI** | Chat works; assistants for installed apps appear for everyone; voice works if speech installed. |
| X2 | Switch LobeChat → Open WebUI → LobeChat | 07 §6 behaviour; no assistant lost; old history still in the stopped app until removed. |
| X3 | Speech end to end | Personal key created; `curl` transcription works from another tailnet device; Open WebUI voice input works; HA Assist pipeline uses Wyoming (text test via HA's pipeline debug API). |
| X4 | Kopia | Nightly schedule runs (time it 5 minutes ahead in the VM); Kopia UI shows snapshots (operator) and 403 for household; SFTP copy to a second VM; disaster recovery on a third VM following `docs/backup-recovery.md`. |
| X5 | Hermes | Owner-only; read tool OK; write → approval; household 403. |
| X6 | n8n → HA → Mosquitto | An n8n workflow receives an HA webhook (header auth) and publishes an MQTT message HA sees. |
| X7 | Resource sanity | With every app installed in the VM: idle RAM total recorded; no container restart loops over 30 minutes. |
| X8 | Full uninstall | `./uninstall.sh` in the VM removes apps; media and backups behave as documented. |

## 5. Owner steps (hand to the owner, collect results)

Write these as a short numbered list per item, using plain language:

1. GPU card on the real host; ComfyUI image from Open WebUI; memory released
   after 5 idle minutes; Whisper transcription speed.
2. Phones: Audiobookshelf app OIDC login; HA Companion login plus location;
   Dawarich/OwnTracks reporting with Tailscale always-on; Open WebUI or
   LobeChat PWA voice; Grocy app (deferred item from 0-2).
3. Desktop dictation into Google Docs and LibreOffice with the verified
   client.
4. Real cameras for Frigate (only when the owner has them).

## 6. Merging to `main`

Only when:
- [ ] Every task in [README.md](README.md) is `merged` (or explicitly
      deferred by the owner, recorded with the date).
- [ ] Phase 6 journeys X1–X8 pass in a fresh VM from the tip of
      `expansion/apps-2026`.
- [ ] Owner steps done or deferred by the owner.
- [ ] `make verify` passes on the tip.
- [ ] The owner says "merge". Then: `git switch main && git merge --no-ff
      expansion/apps-2026`, run `make verify`, and push **only with the
      owner's go-ahead**.
- [ ] `docs/architecture.md` "Adding an app" points to
      `docs/expansion/04-app-playbook.md`. Restic mentions are gone from the
      docs. `docs/app-updates.md` mentions Kopia where it mentions backups.
