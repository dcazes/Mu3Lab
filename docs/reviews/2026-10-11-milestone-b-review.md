# Milestone B review — 2026-10-11

Review date: **2026-10-11**. Reviewer: Claude Code, requested by the owner. It follows the [Milestone A review](2026-10-11-milestone-a-review.md).

Scope: [Milestone B](2026-10-07-project-review.md#milestone-b--durable-recovery-and-deployment-identity):

| Part | Findings | Topic |
|---|---|---|
| B1 | R09, R35 | Operation journal, bounded commands |
| B2 | R10 | Locks, leftover commands |
| B3 | R11, R16 | Release IDs, canonical settings |
| B4 | R12, R13 | Rollback bundles, staged restore |
| B5 | R15 | Truthful protection, retention |

C3 later replaced restic with Kopia beneath this code, so B was reviewed against today's code.

Code: branch `fix/review-milestone-c` at `967c4a4`. Live: `Mu3Lab-live` at `e74b5da`; the B code is the same in both.

**Verdict:** the recovery machinery works. Killing the worker mid-restore was recovered automatically twice, and the one-worker lock holds. But **one HIGH bug defeats deployment identity (R11) in practice**, and two MEDIUM issues affect whether backups are there when needed. Health score for Milestone B: **6.5 / 10**. It rises to about 8.5 once B-H1 and B-M1 are fixed.

## Method

- Read the code:
  - `ctl/operations.py`
  - `ctl/lifecycle/maintenance.py`
  - `ctl/backups.py` (plan, stage, swap, undo, wipe, retention, protection)
  - `ctl/worker.py`
  - `ctl/lifecycle/app_releases.py`
  - `ctl/lifecycle/deployments.py`
- The full test suite passes (1064 tests, recorded in the A review). It includes 133 tests in the B areas.
- An isolated reproduction against a temporary database (B-H1).
- **Live tests on Beaver Habits**, a freshly installed app with no owner data, run through the API as operator `review-probe`:
  1. a manual backup;
  2. a restore while the app is stopped;
  3. two restores with the worker **SIGKILLed** mid-operation, once while unpacking and once just after the swap;
  4. a second worker started by hand.

## Live results

| Check | Result |
|---|---|
| Manual backup | Saved in about 1 s, checked, tagged with release ID; protection `checked/structure` |
| Restore needs the app's name typed | Yes (400 without it) |
| Staged restore | Unpacked beside live data while running, safety backup, swap, housekeeping, no leftovers |
| Worker SIGKILL during `stage_restore` | systemd restarted it; resumed about 40 s later from the journaled phase, re-staged, finished; no leftover folders, no orphaned operation |
| Worker SIGKILL just after swap | Resumed in `housekeeping`, finished cleanly |
| Second worker started by hand | "Another Mu3Lab worker is running; this one waits" |
| Bundle files | 0600 files, 0700 bundle folders; the parent `state/deployments` is 0775 (see L3) |
| Other apps' release records | All 23 clean before testing; Ollama and Speaches correctly `nvidia` |

## Findings

### B-H1 — HIGH — The operation journal loses each release's variant and definition

[`ctl/operations.py`](../../ctl/operations.py) `_release_text` stores only `{version, images}`, and `_release` rebuilds `Release(version, images)`. Every release read back from the journal therefore has `variant="cpu"` and `definition=""`, so its `Release.id` differs from the real one. Every update, restore and recovery reads its releases from the journal (`journal.begin` returns `Operation.load(row)`).

Reproduction (temporary database): saved as `nvidia 'defdigest123' 37c30728…`; read back as `cpu '' 47a209d4…`; same id: `False`.

Consequences:

1. **GPU apps.** After any update, `_apply` writes the journal's target to the record. After a restore, `_switch_deployment` does the same. Either way the record now says `cpu`. `app_releases.status` and `_begin` then evaluate `approved(service, root, variant="cpu")`, so the dashboard can offer an "update" that would move Ollama or Speaches onto CPU images. This was shown by code path; it was not run against the GPU apps.
2. **Every restore looks like a release switch** (observed live). A backup made one minute earlier from the same release triggered "Downloading … the release this backup is from". It ran `docker pull` and rewrote the record without its definition digest.
3. **R12 bundle lookup never matches** (observed live). Bundles are named with the wrong ID (`fa74c9d7d653-8faa691fe2adeeb9`), so `latest_for_release(real_id)` finds nothing. The log reads "That release's own deployment files were not kept". Update rollback, which uses `op.bundle` directly, is unaffected.
4. **Phantom history.** A second record of the same version appears in history. Later version-only lookups then raise `AmbiguousRelease`.

Test gap: no `OperationStore` test round-trips a variant or definition.

Fix:

```python
# Before (ctl/operations.py)
def _release_text(release):
    return json.dumps({"version": release.version, "images": release.images}, sort_keys=True) if release else ""


def _release(text):
    ...
    return Release(str(value.get("version") or ""), {str(k): str(v) for k, v in (value.get("images") or {}).items()})


# After: store the whole release; older rows fall back to the current defaults
def _release_text(release):
    return json.dumps(release.as_dict(), sort_keys=True) if release else ""


def _release(text):
    ...
    return Release(
        str(value.get("version") or ""),
        {str(k): str(v) for k, v in (value.get("images") or {}).items()},
        str(value.get("variant") or "cpu"),
        str(value.get("definition") or ""),
    )
```

Add a test that begins an operation with an `nvidia` release with a definition, reloads it, and asserts the same `id`. Add another that restoring a same-release backup does not download or switch.

### B-M1 — MEDIUM — Retention can delete a deliberately made backup the same day

`expired()` keeps the newest `KEEP_LATEST = 3` snapshots plus the newest per day, week and month, **across all reasons**. Each restore and update first takes its own safety backup, and those count toward the 3.

Observed live: after three restores, the manual backup `b3b1d936`, the one being restored, was **deleted**. Only three `pre-restore` snapshots remained. Restoring repeatedly to find the right backup can delete the candidates being tried. A manual "before I try something" backup can disappear after three later same-day operations.

Fix: do not count safety backups toward `KEEP_LATEST`, or keep the newest manual backup and the newest of each reason for a minimum age. Never prune the snapshot an operation just restored from.

### B-M2 — MEDIUM — Restores always need the internet and the upstream image

[`app_releases.download`](../../ctl/lifecycle/app_releases.py) runs `docker pull` for every image, even when the pinned digest is already local. Because of B-H1, every restore currently reaches this step. A restore during an internet outage, or after an upstream tag or digest is removed, fails with `image_pull_failed`. Fix: skip the pull when `docker image inspect <image@digest>` succeeds.

### LOW

| # | Finding |
|---|---|
| L1 | "Restore tested" without a start: restoring a **stopped** app never starts it, yet `record_restore_tested` stamps `restore_tested_at`. Its docstring says "the app came up healthy". Observed live (no containers running). Record it only when the app was actually started and passed health checks. |
| L2 | `backups.swap` crash window: a crash after `rename(staged, live)` but before `save(plan)` makes the retry fail with "restored copy is missing". The caller then undoes instead of finishing. This is safe (old data returns) but not the documented "finishes on retry". Separately, in `undo`, a crash in the same window for a folder that did not exist before (`absent`) leaves the restored folder in place, because its state was never saved as `swapped`. The window is narrow. |
| L3 | `state/deployments` is 0775, while the B4 record says 0700 at every level. The parent `state/` is 0700, so nothing is exposed. |
| L4 | `_maintain_lease` treats any exception, including a briefly locked database, as a lost lease. The job then stops at its next checkpoint and is resumed later. This is safe, but it adds avoidable interruptions under database contention. |
| L5 | No free-space pre-check before staging (already documented); restore of a large app fails late. |
| L6 | The worker reports through `print()`; there is no structured logging or levels. |

## What is strong

- Phases are journaled before every side effect.
- Swaps and undos are written to be repeatable.
- The staged restore leaves live data untouched until the swap.
- A start failure on restored data swaps the old data back.
- After 5 failed attempts, the app is put into "needs attention" with recovery choices.
- The API blocks actions during recovery (`recovery_required` / `recovery_pending`).
- Commands run with deadlines and process-group kills.
- Leftover-command tracking keyed by pid and start time.
- Sorted lock keys prevent deadlock.
- The bundle hash is verified before restore.
- Leftovers are wiped only if dot-prefixed, under the data root, and not symlinks.

## Live side effects of this review

- Beaver Habits: 1 manual backup and 3 restores, all from its own fresh data.
- The worker was SIGKILLed twice; systemd restarted it each time.
- B-H1 corrupted Beaver Habits' release record during the test. The reviewer put the original record back (`6c6f907c0e6dd0bc`) and removed the phantom history entry it created (`8faa691fe2adeeb9`).
- Three `pre-restore` snapshots remain. The manual snapshot was pruned (B-M1).
- One deployment bundle named with the wrong ID remains; it is harmless.
- Activity-log entries under `review-probe`.

## Remediation order

1. **B-H1:** serialize the full release in the journal, add the round-trip tests, then re-check that restore of a same-release backup does not switch.
2. **B-M1:** make retention reason-aware so safety backups cannot push out manual ones.
3. **B-M2:** skip pulls for images already present.
4. **L1–L6.**

The VM interruption matrix (Milestone B exit) is still not run. The owner declined a VM; these live kill tests cover two boundaries on the real host.
