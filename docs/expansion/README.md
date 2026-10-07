# Mu3Lab expansion 2026: implementation guide

This folder tells an implementer exactly how to add ten household apps, the
services they need, Kopia backups and a choice of chat app to Mu3Lab. It is
written for a careful implementer that has **not** seen the planning
conversation. Read every linked file before starting a task; nothing here is
optional reading.

Planned on 2026-10-06 with the owner. The decisions are final unless a
document tells you to stop and ask.

## Read in this order

| # | File | What it gives you |
|---|---|---|
| 1 | [01-decisions.md](01-decisions.md) | Every owner decision, and the apps that were rejected and why. **Do not re-ask these.** |
| 2 | [03-workflow.md](03-workflow.md) | Branches, worktrees, commits, checks, and when to stop and ask. |
| 3 | [02-grocy-review.md](02-grocy-review.md) | Review of the Grocy integration, the template every app follows, and the fixes to make first. |
| 4 | [04-app-playbook.md](04-app-playbook.md) | The step-by-step recipe for integrating **any** app. Every app spec assumes it. |
| 5 | [05-platform.md](05-platform.md) | Shared platform changes the apps need: ports, media folder, GPU sharing, new route and manifest features. |
| 6 | [06-kopia.md](06-kopia.md) | Replace restic with Kopia: web interface, schedules, copy to another computer. |
| 7 | [07-chat-choice.md](07-chat-choice.md) | LobeChat or Open WebUI as the household chat app, plus Hermes Agent as an owner add-on. |
| 8 | [08-speech.md](08-speech.md) | Whisper/Piper speech for every app, Home Assistant, and devices outside the stack. |
| 9 | `apps/*.md` | One spec per app (below). |
| 10 | [09-troubleshooting.md](09-troubleshooting.md) | How to diagnose and handle problems, with known failure patterns. |
| 11 | [10-acceptance.md](10-acceptance.md) | The test matrix that decides when something is done. |

## Phases and order of work

Work strictly in this order. Each line is one task branch (see
[03-workflow.md](03-workflow.md)). Do not start a phase until the previous one
is merged into `expansion/apps-2026` with `make verify` passing.

| Phase | Task branch | Spec | Depends on |
|---|---|---|---|
| 0 | `exp/0-1-grocy-fixes` | [02-grocy-review.md](02-grocy-review.md) §4 | nothing |
| 0 | `exp/0-2-grocy-live-acceptance` | [02-grocy-review.md](02-grocy-review.md) §5 | 0-1 |
| 1 | `exp/1-1-port-registry` | [05-platform.md](05-platform.md) §2 | 0 |
| 1 | `exp/1-2-media-root` | [05-platform.md](05-platform.md) §3 | 1-1 |
| 1 | `exp/1-3-gpu-sharing` | [05-platform.md](05-platform.md) §4 | 1-1 |
| 1 | `exp/1-4-route-features` | [05-platform.md](05-platform.md) §5 | 1-1 |
| 1 | `exp/1-5-soft-dependencies` | [05-platform.md](05-platform.md) §6 | 1-1 |
| 1 | `exp/1-6-per-person-accounts` | [05-platform.md](05-platform.md) §7 | 1-1 |
| 1 | `exp/1-7-host-network-devices` | [05-platform.md](05-platform.md) §8 | 1-1 |
| 1 | `exp/1-8-install-gates` | [05-platform.md](05-platform.md) §9 | 1-1 |
| 1 | `exp/1-9-oidc-extras` | [05-platform.md](05-platform.md) §10 | 1-1 |
| 2 | `exp/2-1-kopia-engine` | [06-kopia.md](06-kopia.md) §4 | 1 |
| 2 | `exp/2-2-kopia-ui` | [06-kopia.md](06-kopia.md) §5 | 2-1 |
| 2 | `exp/2-3-kopia-schedules` | [06-kopia.md](06-kopia.md) §6 | 2-1 |
| 2 | `exp/2-4-kopia-sftp-copy` | [06-kopia.md](06-kopia.md) §7 | 2-1 |
| 3 | `exp/3-1-chat-provider-interface` | [07-chat-choice.md](07-chat-choice.md) §3 | 1 |
| 3 | `exp/3-2-gateway-approvals` | [07-chat-choice.md](07-chat-choice.md) §4 | 3-1 |
| 3 | `exp/3-3-open-webui` | [07-chat-choice.md](07-chat-choice.md) §5 | 3-2 |
| 3 | `exp/3-4-chat-switch` | [07-chat-choice.md](07-chat-choice.md) §6 | 3-3 |
| 3 | `exp/3-5-hermes-agent` | [07-chat-choice.md](07-chat-choice.md) §7 | 3-2 |
| 4 | `exp/4-1-speaches` | [08-speech.md](08-speech.md) §3 | 1-3 |
| 4 | `exp/4-2-speech-routing-keys` | [08-speech.md](08-speech.md) §4–5 | 4-1 |
| 4 | `exp/4-3-wyoming-bridge` | [08-speech.md](08-speech.md) §6 | 4-1 |
| 5 | `exp/5-01-homebox` | [apps/homebox.md](apps/homebox.md) | 1 |
| 5 | `exp/5-02-beaver-habits` | [apps/beaver-habits.md](apps/beaver-habits.md) | 1 |
| 5 | `exp/5-03-audiobookshelf` | [apps/audiobookshelf.md](apps/audiobookshelf.md) | 1-2, 1-9 |
| 5 | `exp/5-04-outline` | [apps/outline.md](apps/outline.md) | 1 |
| 5 | `exp/5-05-romm` | [apps/romm.md](apps/romm.md) | 1-2 |
| 5 | `exp/5-06-photon` | [apps/dawarich.md](apps/dawarich.md) §Photon | 1-5 |
| 5 | `exp/5-07-dawarich` | [apps/dawarich.md](apps/dawarich.md) | 5-06 |
| 5 | `exp/5-08-n8n` | [apps/n8n.md](apps/n8n.md) | 1-4 |
| 5 | `exp/5-09-comfyui` | [apps/comfyui.md](apps/comfyui.md) | 1-3 |
| 5 | `exp/5-10-mosquitto` | [apps/home-assistant.md](apps/home-assistant.md) §Mosquitto | 1 |
| 5 | `exp/5-11-home-assistant` | [apps/home-assistant.md](apps/home-assistant.md) | 1-6, 1-7, 5-10 |
| 5 | `exp/5-12-frigate` | [apps/frigate.md](apps/frigate.md) | 1-8, 5-10 |
| 6 | `exp/6-1-full-acceptance` | [10-acceptance.md](10-acceptance.md) | all |

Phases 2, 3, 4 and 5 touch different code and may proceed in any order after
Phase 1, but **one task at a time**, each merged before the next starts. The
app order in Phase 5 goes from simplest to hardest on purpose: the early apps
prove the platform features that the later apps depend on.

## Status tracker

Update this table in the same commit that merges each task. Use only:
`not started`, `in progress`, `blocked: <reason>`, `merged <short sha>`.

| Task | Status | Notes |
|---|---|---|
| 0-1 grocy-fixes | not started | |
| 0-2 grocy-live-acceptance | not started | Needs owner for phone checks |
| 1-2 media root, 1-4 (email header, form launch), 1-5 soft deps, 1-6 per-person accounts, 1-9 role claims | implemented 2026-10-06 | See [notes/implementation-2026-10-06.md](notes/implementation-2026-10-06.md) |
| 1-1, 1-3 (GPU card), 1-7, 1-8 | not started | Ollama idle unload done |
| 2-1 … 2-4 kopia | not started | |
| 3-3 open-webui (as an optional app) | implemented 2026-10-06 | Chat choice 3-1, 3-2, 3-4 and Hermes 3-5 not started |
| 4-1 speaches, 4-2 routing and voice keys | implemented 2026-10-06 | 4-3 Wyoming bridge waits for Home Assistant |
| 5-02 beaver, 5-03 audiobookshelf, 5-04 outline, 5-05 romm, 5-06 photon, 5-07 dawarich | implemented 2026-10-06 | VM acceptance pending |
| 5-01 homebox, 5-08 … 5-12 | not started | |
| 6-1 acceptance | not started | |

## Conventions used in these documents

- **VERIFY:** A fact that was true at planning time but must be confirmed
  against the exact image or release you pin, before you write code that
  depends on it. Record what you found in the task's PR description. If the
  fact turns out false, follow that section's fallback. If there is none,
  **stop and ask** (see [03-workflow.md](03-workflow.md) §6).
- **STOP:** Do not improvise. Write down what you found and ask the owner.
- **MUST / MUST NOT:** Hard requirements. **SHOULD:** Default you may depart
  from only with a written reason in the PR.
- Paths like `ctl/...` are relative to the repository root.
- The owner reads plain English. Every message to the owner explains what
  changed and what to test, without jargon. See [03-workflow.md](03-workflow.md) §8.
