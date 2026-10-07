# 03. Workflow: branches, checks, and handling problems

## 1. Branch layout

```text
main ─────────────────────────────────────────────────────────► (merge after Phase 6 + owner approval)
  └─ expansion/apps-2026  (feature branch, worktree /home/dak/Desktop/Mu3Lab-expansion)
        ├─ exp/0-1-grocy-fixes        → merged back into expansion/apps-2026
        ├─ exp/1-1-port-registry      → merged back
        └─ …one branch per task in README.md
```

- **Feature branch:** `expansion/apps-2026`. Never commit directly to it
  except to merge a task branch or update `docs/expansion/README.md`'s status
  table.
- **Task branches:** `exp/<phase>-<task>-<slug>`, named exactly as in
  [README.md](README.md). Create each from the **current** tip of
  `expansion/apps-2026`.
- **Never commit to `main`** during this plan. Never force-push `main` or
  `expansion/apps-2026`.

## 2. Starting a task (every time)

```bash
cd /home/dak/Desktop/Mu3Lab-expansion
git worktree list
git status --short
git switch expansion/apps-2026
git pull --ff-only
git switch -c exp/<phase>-<task>-<slug>
```

If `git status` shows changes you did not make, or `git worktree list` shows
worktrees you do not recognise (the owner runs Codex sessions in
`~/.codex/worktrees/`), **stop**. Tell the owner what you found and ask whether
to merge it in. Never stash, discard, reset or exclude someone else's work.

Keep the feature branch current with `main`, because the owner may fix things
on `main` meanwhile. At the start of each **phase**:

```bash
git switch expansion/apps-2026
git fetch origin
git merge origin/main     # merge, never rebase a shared branch
make verify
```

Resolve conflicts by keeping both intents. If you cannot tell what `main`'s
change intended, stop and ask.

## 3. While working

1. Read the task's spec section **and** [04-app-playbook.md](04-app-playbook.md)
   (for apps) before writing code.
2. Do every **VERIFY** item first. Record findings in a scratch file
   `docs/expansion/notes/<task>.md` (commit it with the task). Include the
   image digest checked, the command run and the result.
3. Write a failing test before each behaviour change, as the rebuild plan
   requires (Part E2, Tests).
4. Follow the coding standards in [../rebuild-plan.md](../rebuild-plan.md)
   Part E2 and the ground rules in [../rebuild-handoff.md](../rebuild-handoff.md) §6.
   In short: typed code; functions under 60 lines; modules under 500; no
   function-level imports; no app IDs in `ctl/`; no `except Exception: pass`;
   every user-facing failure has a plain message plus a stable `code`;
   secrets never logged (use `ctl.jobs.redact`).
5. Commit in small, coherent steps. Commit messages explain **why**.

## 4. Checks before every commit and merge

```bash
make format
make verify                       # app-ID check, ruff, API-schema check, mypy, all tests, dashboard build
python tools/validate_compose.py  # renders and validates every Compose project; run after any compose change
```

If you changed API models: `make api-schema` and commit the generated
`dashboard/openapi.json`, `schema.ts` and `models.ts` together.

**Never** skip, disable or loosen a test to make it pass. If a test encodes
behaviour this plan deliberately removes (for example restic), delete it in
the same commit as the behaviour and say so in the message.

## 5. Finishing a task

1. All acceptance boxes in the spec are ticked, with evidence (command output,
   test names, screenshots) written in `docs/expansion/notes/<task>.md`.
2. Lifecycle-affecting tasks ran their VM checks
   ([10-acceptance.md](10-acceptance.md)).
3. Merge:

   ```bash
   git switch expansion/apps-2026
   git merge --no-ff exp/<task-branch> -m "Merge <task>: <one-line purpose>"
   make verify
   ```

4. Update the status table in [README.md](README.md) in a separate small
   commit.
5. Delete the merged task branch (`git branch -d exp/<task-branch>`). Remove
   any extra worktree you created for it (`git worktree remove <path>`).
6. **Pushing** to GitHub is outward-facing. Push only when the owner has said
   pushing is fine for this plan. Ask once, then remember it in
   `docs/expansion/notes/owner-permissions.md`.
7. Tell the owner, in plain English (§8), what changed and what to test.

## 6. When to stop and ask (escalation rules)

Stop work on the task, write what you found, and ask the owner when:

| Situation | Example |
|---|---|
| A **VERIFY** fails and the section has no fallback | The app's OIDC has no auto-registration switch. |
| An open question from [01-decisions.md](01-decisions.md) §3 is reached | Q1–Q6 |
| Following the plan would break a standing rule from [01-decisions.md](01-decisions.md) §1 | The only way to create the admin is SQL in the app's database. |
| The task is clearly larger than described (more than ~2× the touched files) | The chat-provider interface needs dashboard routing changes too. |
| Instructions conflict with code you find | A function the spec names does not exist or does something else. |
| A security property cannot be kept | A phone app needs a path that bypasses Authentik without its own authentication. |
| An app's licence or maintenance status has changed | Upstream archived, licence changed to non-free. |
| The same failure persists after two good-faith fixes | Health check flaps after two adjustments. |
| Anything would touch the owner's running stack, `sudo`, or `install.sh` | |

How to ask: one short message stating (1) what you were doing, (2) what you
found, with evidence, (3) two or three options with your recommendation and
its cost, and (4) what you will do while waiting (usually: move to the next
independent task, or nothing).

**Do not** silently pick a weaker design, add a TODO, or mark a task done with
a known gap. A task with a gap is `blocked: <reason>` in the status table.

## 7. Handling issues (procedure)

When something fails, use this sequence. [09-troubleshooting.md](09-troubleshooting.md)
has the catalogue of known failures and fixes.

1. **Reproduce** it in the smallest setting: a unit test, a single container
   (`docker run` of the pinned image with a temporary volume, named with
   `-test-`), or the VM. Never reproduce against the owner's stack.
2. **Collect evidence:** the job log (redacted), `docker compose logs` for the
   project under `/srv/mu3lab/projects/<id>` in the VM, the generated `.env`
   (secrets masked), the generated Caddy block, and HTTP responses (`curl -v`
   through the route and directly to the loopback port).
3. **Classify** using [09-troubleshooting.md](09-troubleshooting.md) §2. Is it
   ours (manifest, rule, route, engine), the app's (upstream bug or changed
   behaviour), or the environment's (VM, network, GPU, disk)?
4. **Fix at the right layer:**
   - App-specific → `apps/<id>/` (manifest, script, `hooks.py`).
   - Shared behaviour wrong for every app → `ctl/` with a test, and re-run
     the tests of apps already integrated.
   - Upstream bug → pin a version without the bug if one exists; otherwise a
     documented work-around in `apps/<id>/` that is easy to remove, and a note
     in `docs/expansion/notes/upstream-issues.md` with the upstream issue
     link. Never patch upstream source inside the image.
5. **Add a regression test** that fails without the fix.
6. **Record** the problem and fix in the task notes. If it is a new pattern,
   add it to [09-troubleshooting.md](09-troubleshooting.md) §3 in the same
   task.

## 8. Talking to the owner

The owner is a solo, non-specialist operator who wants professional
standards explained in terms of value. In every message:

- Lead with whether you are **finished or still working**. If anything runs in
  the background, say "Not done yet: waiting on X" first. The owner reinstalls
  as soon as a turn looks finished.
- Say what changed in plain words, why it matters, and **exactly what to test**
  (numbered steps).
- Never claim a check passed that you did not run. Say "not run" and why.
- Ask for the owner's hands (phone checks, Tailscale approval, camera
  hardware) as a short numbered list.
