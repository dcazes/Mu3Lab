# Rebuild acceptance

Tasks A–J are implemented on `main` (merged from `codex/rebuild-architecture-handoff`
after review on 2026-10-04). Passing unit tests does
not establish that a complete new household installation works. Run this
checklist in a disposable installation before replacing the working stack.
Record the commit, platform, browser, results and failures below.

## Isolated installation

Use the existing `tools/vm/fresh-vm.sh` helper from this checkout. It requires
QEMU/KVM, an Ubuntu cloud image, `genisoimage`, SSH and rsync on the test host.
Use a unique VM directory and SSH port so an existing test VM is unaffected:

```bash
export MU3LAB_VM_DIR="$HOME/.cache/mu3lab-rebuild-acceptance"
export MU3LAB_VM_SSH_PORT=2229
tools/vm/fresh-vm.sh up
tools/vm/fresh-vm.sh ssh
# Inside the disposable VM:
cd Mu3Lab
./install.sh
```

The helper copies sources without Git history or generated assets. This tests
a development installation, not release checkout/update behavior. For release
acceptance, clone the repository at a published normal tag inside a separate
VM instead. Tailscale approval and sign-in require the tester's interaction.
To remove the disposable VM afterwards, run the helper's `down` command with
the same environment values. Do not run install, uninstall or container cleanup
on the working household server for this exercise.

## User journey

1. **Fresh installation and restart.** Install in the empty VM. Expected:
   setup completes, opens a protected dashboard and saves app logins. Re-run
   the installer, then reboot: completed steps survive and services recover.
   The host does not need Node.js; builds use the pinned Node container.
2. **Two devices and two roles.** Sign in on a laptop and phone with Tailscale
   connected. Expected: app states agree within the polling interval. Mark a
   Get started step on one device and refresh the other: it stays complete.
   Repeat with an administrator and household member: health is shared,
   personal progress is separate and only authorized actions are offered.
   In the disposable VM, stop the worker while leaving the API running:
   after 30 seconds the dashboard says “Status may be out of date.” Restart it.
3. **Apps and sign-in.** Install Mealie, Immich and Nextcloud from Apps.
   Expected: progress resumes after a refresh, each reaches Running, and Open
   uses its manifest-defined sign-in path without asking for another app
   account when native SSO applies. Check Devices instructions and QR links.
   A missing setting produces a clear message and a usable next action.
4. **Chat and assistants.** Approve the one-time chat connection for the person.
   Open web chat on laptop and phone, including the installed web app.
   Expected: the same app assistants are available after synchronization.
   Ask Mealie “what recipes do I have?” against seeded test recipes. Results
   must contain real app data. A write asks for approval; rejection performs
   no write. Stop an app: its assistant remains, with availability reflecting
   the stopped app. Personal instruction edits and conversations survive sync.
5. **Household invitation.** Add a test household member in Settings → People.
   Complete their invitation and one-time chat approval. Expected: assistants
   synchronize for that person; their conversations and checklist stay personal.
   Administrative settings/actions remain unavailable. Verify their vault
   organization membership and shared logins without sharing a master password.
6. **Lifecycle and data.** Add a test recipe to Mealie; stop and start it;
   create a backup, change the recipe and restore the backup after confirming
   the app name. Uninstall with Keep data, then reinstall. Expected: saved
   data returns, no duplicate owner is created, and routes/connectors recover.
   Repeat a deliberately interrupted operation and inspect its clear error,
   retry action and redacted job log. Confirm no other installation is touched.
7. **Vault and provider.** Verify SurfSense and FreeLLMAPI login entries in
   Vaultwarden. Sign the Bitwarden extension into this test vault and exercise
   autofill. Add a test provider key and verify a real chat response through
   the approved route. No key or master password may appear in browser error
   messages, URLs, job logs or audit records.

## Release acceptance

Publish a normal `vMAJOR.MINOR.PATCH` tag only after reviewing the release.
Confirm CI passes, amd64 and arm64 images are published and accessible,
`release-images.json` names immutable digests, and dashboard/archive checksums
verify. In a clean VM install that tag, then update from an older published
normal tag through Settings → System. Expected: verified assets are used,
connector images use published digests and the dashboard/worker restart.
Preview tags and development branches must not be offered as automatic updates.
Test a failed asset download/checksum in a disposable installation: it must
stop with a clear error. Local generation tests cannot prove GHCR permissions
or the actual published download path.

## Results recorded on 2026-10-04

| Check | Result |
| --- | --- |
| Python suite | 647 tests passed; four opt-in integration cases skipped |
| Dashboard | 95 tests passed; lint, formatting, TypeScript and production build passed in pinned Node container |
| Contracts | OpenAPI and generated types checked; no host commands during service/snapshot reads; roles share health; checklist merges and isolates people |
| State | Private storage, multi-process migration, key loss, expiry and atomic job preparation covered |
| Compose | 26 rendered projects validated |
| Stylesheet | Home, Apps, Mealie, Chat and Security settings: ten desktop/phone screenshots identical before/after; compiled CSS identical |
| Service projection timing | Median 3.3 ms, slowest 7.6 ms over 20 isolated empty-database reads; not a measurement of the live stack |
| Full VM / laptop / phone journey | Not run in this session |
| Published release / GHCR / arm64 | Not run; requires an actual release |
| Baseline before rebuild | Owner's working installation deliberately untouched; no new baseline acceptance run |

Earlier task-specific integration evidence is recorded in
[rebuild-handoff.md](rebuild-handoff.md). The four skipped integration cases
are not counted as passing real integrations in the current suite run.

For visual regression checks, `tools/visual_check.py` captures fixture-backed
pages using Playwright and compares PNGs with Pillow. Run in a separate browser
test environment against a locally served `dashboard/dist` with SPA fallback;
all `/api/` requests are intercepted using the committed test fixture:

```bash
python tools/visual_check.py --browser /path/to/chromium --output /tmp/ui-before
# Rebuild the dashboard after a style change, using the same browser/server.
python tools/visual_check.py --browser /path/to/chromium --output /tmp/ui-after --reference /tmp/ui-before
```

## Tester record

Commit / release: ___ · OS / CPU architecture: ___ · browsers / devices: ___

Steps 1–7: ___ · release checks: ___ · failures and reproduction steps: ___
