# Milestone B1 implementation (R35, R09) — 2026-10-08

This records B1 of the [project review](2026-10-07-project-review.md): the bounded command runner (R35) and the operation journal for app backup, restore and update (R09). The review tracker remains the authoritative status. Both findings are **implemented; release acceptance pending**. Nothing here was run against a real Docker host or VM.

Branch: `fix/review-milestone-b`, stacked on `fix/review-priority-a` (PR #3), so it includes all Milestone A and C1/C2 work. Owner: Claude Code, authorized by the owner on 2026-10-08.

## Owner decisions

| Question | Decision |
|---|---|
| Journal scope | Backup, restore and update only. Install, uninstall and reset stay re-runnable by design and are not journaled. |
| Restart after an update applied its new release but before it was verified | Start it and check health again; keep it if healthy, otherwise roll back to the pre-update backup and previous release. |
| When automatic recovery fails | Block the app ("needs attention"), name the backup to use, and offer **Retry recovery** and **Restore the backup from before**. |

## Interruption inventory

| Operation | Boundary | Before | After |
|---|---|---|---|
| Update | New release recorded, health not yet verified | A reclaimed job reported "already up to date"; no rollback; the pre-update backup ID was lost | Resumes in `apply_update`: re-applies the recorded target (idempotent) and re-checks health; rolls back if unhealthy |
| Update rollback | Restoring the pre-update data | Retry backed up the mixed data as a new "pre-update" backup; pruning could remove the good one | Resumes in `rollback` with the recorded backup and previous release; no new backup is taken |
| Restore | After the safety backup, during the in-place swap | A second safety backup of half-restored data; the original became unreferenced and prunable | Resumes in `restore` with the same safety backup; finishes the restore |
| Restore undo | Putting the safety backup back | As above | Resumes in `rollback` |
| Backup | App stopped | Re-ran; low risk | Re-runs; app restarted if it was running |
| Any | Job ended without finishing (cancelled while the worker was down, unexpected exception) | Operation state was lost | The worker queues a `recover` job that brings the app to a safe end |
| Install, uninstall, reset | Any | Re-runnable | Unchanged (owner decision) |
| Self-update, rewiring | — | — | Out of scope: R17 (Milestone C4), R12/R26 |

## R35 — bounded command runner

[`ctl/process.py`](../../ctl/process.py) is now the single runner behind `privilege._exec` (so every `docker_cmd`), the streaming, stdin and `docker load` helpers in `ctl/actions.py`, `self_update` (`_git`, `_run`, `_new_code`), `toolchain.sync_python`, the status probes in `service_state`, `status/host`, `hostinfo`, `compute`, `core_setup` and `preflight`.

Guarantees, each covered in `tests/test_process.py`:

- one monotonic deadline for start, output and exit, so a child that closes its output and then hangs is still stopped;
- concurrent stdout/stderr draining, so a full pipe cannot deadlock;
- a byte-bounded output tail with a dropped-line count, a per-line byte cap, and a cap on lines written to job logs;
- its own session, with SIGTERM then SIGKILL to the whole process group on timeout, so `sg docker -c` grandchildren are stopped too;
- a raising log callback (a cancelled job) no longer abandons the command: output keeps draining until exit, then the interruption is re-raised. Before this, a cancel during `docker compose up` left the command running with nobody reading its output;
- streams closed and children reaped in `finally`, with no ResourceWarnings.

Restic containers are named and labelled `mu3lab.role=restic`. A timed-out restic container is stopped with `docker stop`. A data-changing restic command (`init`, `backup`, `check`, `forget`, `restore`) first waits up to 30 minutes for a restic container that survived an earlier attempt, and refuses to run if one is still there.

## R09 — operation journal

Migration `0005_operation_journal.sql` adds `operations` (one per journaled job; with kind, phase, state, original running state, previous and target release, chosen and recovery backup, attempt count and schema version) and `operation_steps` (an append-only begin/resumed/finished log). At most one operation per app may be `active`. [`ctl/operations.py`](../../ctl/operations.py) is the store; it lives in the job store's database.

[`ctl/lifecycle/maintenance.py`](../../ctl/lifecycle/maintenance.py) records each phase before acting on it: `prepare` → `stop_app` → `backup` → `backed_up` (recovery backup recorded) → `apply_update` or `restore` → `start_app` → `housekeeping`, with `rollback` for undoing. The swap and undo steps stay `uncancellable`. Final states are `succeeded`, `failed`, `rolled_back`, `cancelled`, `needs_attention` and `resolved`.

- **Reclaimed job** (the worker died and the lease expired): continues from the recorded phase, as in the inventory above.
- **Orphaned operation** (job terminal, operation still active): every 60 seconds the worker queues one `recover` job (actor `worker`). It ends the operation safely: nothing changed → `cancelled` (restarting the app if it was stopped); update applied → re-check, keep or roll back; restore mid-swap → put the data from before back; restore finished → start the app.
- **Needs attention**: a failed undo, or more than 5 attempts. The API refuses every action except `recover` and `restore` (`409 recovery_required`). The service view shows the app as needs attention with a `recovery` object. The Backups card offers **Retry recovery** and **Restore the backup from before…**. A later successful restore or recovery marks it `resolved`.
- **Pruning**: backups taken inside these operations no longer prune immediately. Pruning runs in `housekeeping`, after the app is back up (which also shortens downtime), and is skipped while any other active or needs-attention operation for that app names a backup.

Tests: `tests/test_operation_journal.py` interrupts at each boundary (while the new release starts, during rollback, before the backup, during a restore swap, during the download), covers orphan recovery, the attempt cap, cancellation and the API block; `tests/test_maintenance.py` keeps the original behaviour; dashboard: `advancedTab.test.tsx`.

## Not done here (remaining acceptance)

- **VM interruption matrix** (Milestone B exit): kill the worker at each boundary on a real host, including a surviving restic container.
- **R10**: lease fencing. A paused worker whose lease expired can still finish a command while another worker resumes the job. The journal makes the resume correct, not exclusive.
- **R12**: rollback restores data, images and the release record, but not the previous Compose file, overrides or templates (`_refresh_definition` still re-renders from the current checkout).
- **R13**: restores are still in place; a crash mid-swap is recovered by repeating or undoing the restore, not by atomic staging.
- **R15**: per-app verification evidence and deeper verification levels.
- The Bitwarden CLI and installer-only `dpkg`/`tailscale` probes still call `subprocess.run` with a timeout.
- **Downgrade**: migration 0005 cannot be undone, so older releases refuse this database (as with 0004).
