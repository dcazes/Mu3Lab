# Milestone B2 implementation (R10) — 2026-10-08

This records B2 of the [project review](2026-10-07-project-review.md): resource locks and handling of an earlier attempt's still-running effects. It builds on [B1](2026-10-08-milestone-b1-implementation.md), whose journal makes a resumed job *correct*; B2 makes it *exclusive*. Status: **implemented; release acceptance pending**. Nothing here was run on a real host or VM.

Branch: `fix/review-milestone-b`. Owner: Claude Code, authorized by the owner on 2026-10-08.

## Problem

A job lease proves a worker is alive. It cannot stop a worker that was paused past its lease from waking up and finishing a command after another worker reclaimed the job. Killing a worker also does not always stop what it started: systemd stops the unit's children, but a worker run by hand, one killed on its own, or a container started with `docker run` can outlive it.

## What changed

| Mechanism | Where | Guarantee |
|---|---|---|
| Worker execution lock | `resource_locks.WorkerLock`; acquired before `ctl/worker.py` starts any background thread or claims a job | One worker process executes at a time. A paused worker keeps the kernel `flock`, so no second worker starts beside it; the kernel releases it on exit, however the process ends. |
| Resource locks | `resource_locks.hold(*keys)` | Interprocess and thread-exclusive; keys taken in sorted order; re-entrant for keys a thread already holds; `Busy` after a timeout. |
| Job resources | `worker.resources(job)` | Each job holds `app:<id>`, `mcp:<id>`, `provider:<id>` or `platform` while it runs. If still busy after an hour, the job fails with `resource_busy` and changes nothing. |
| Re-wiring consumers | `engine/rewire.rewire_consumers` | Each consumer app is re-wired under its own `app:<id>` lock (waiting up to 10 minutes), and one that needs attention after a failed undo (B1) is skipped. |
| Backup repository | `backups._restic` | Data-changing restic commands hold `backup-repository`, then wait for any labelled restic container that survived an earlier attempt (B1/R35). |
| Chat records | `access._chat`, `lobehub_ops.sync_agents` | Revocation and assistant synchronization hold `chat-assistants`, so they never interleave writes to the same person's records. |
| Leftover commands | `ctl/job_processes.py`, migration `0006_job_processes.sql`, tracker hook in `ctl/process.py` | Every command a job starts is recorded with its pid, the kernel start time for that pid (so a recycled pid is never mistaken for it), the worker that started it, and its deadline. Before claiming, the worker settles leftovers: finished ones are forgotten; one past its own deadline (plus 60 s) is stopped, as its original runner would have done; one still within its deadline blocks claiming until it ends. |

Lock ordering: only a job takes several keys (its own, then each consumer it re-wires). Background tasks take one key and never nest, so no wait cycle is possible.

## Tests

`tests/test_resource_locks.py`:

- exclusivity between threads and between processes;
- re-entrancy and exact release;
- overlapping key sets taken in opposite orders do not deadlock;
- key validation;
- a SIGSTOPped worker keeps a second one from acquiring the execution lock;
- job resource keys;
- a busy resource fails the job without running it;
- commands are recorded only inside a job and until reaped;
- leftover handling (within deadline blocks, past deadline is stopped, finished is forgotten);
- re-wiring skips consumers that need attention or are busy.

Existing backup, maintenance and journal tests run with the repository lock in place.

## Remaining acceptance and limits

- **VM drill:** SIGSTOP worker A past its lease, start B, and confirm at most one destructive action. Repeat with a backup container outliving its Docker CLI, and with two operations sharing a dependency.
- **API and installer:** the API process (R18) and the terminal installer do not take these locks yet. They do not run lifecycle jobs, but the API still writes some app configuration directly (R16).
- **Registration window:** a command is recorded just after it starts. A crash in that instant leaves an unrecorded child; systemd still stops it with the unit.
- **Docker daemon operations:** Docker-managed work other than labelled restic containers (for example a pull the daemon continues) is not tracked individually.
- **Migration:** 0006 cannot be undone; older releases refuse this database.
