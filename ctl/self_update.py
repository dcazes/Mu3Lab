"""Update Mu3Lab itself from the dashboard.

The dashboard and worker run from a git checkout. Updating is what
``selecting a tagged release and running ./install.sh`` does, limited to the steps that need no
administrator password: Python packages, the dashboard build, the sign-in
gate, core images and a restart. The checks come from the installer itself,
run by the newly pulled code, so a release that needs more (a new system
package, say) is undone before anything changes, and the person is asked to
run ``./install.sh`` in a terminal instead.

Only a clean checkout that GitHub is ahead of updates this way: a copy with
its own commits or uncommitted edits is a developer's, updated by hand.

Contract between releases: the worker running the *old* code pulls, then runs
``python -m ctl.self_update plan|finish`` from the *new* checkout, which
prints one JSON object as its last line. Keep that interface stable.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from ctl import job_guard, platform_releases, toolchain
from ctl.jobs import JobStore

if TYPE_CHECKING:
    from ctl.install import Step

SERVICE_ID = "mu3lab"
ACTION = "self_update"
# Installer steps whose fixes need no administrator password.
UNATTENDED = frozenset({"dashboard_build", "dashboard_protection", "core_images"})
# Restarting the dashboard and worker happens last, after the job is recorded.
RESTART_STEP = "service"
FETCH_TTL_SECONDS = 600
UNITS = ("mu3lab-ctl.service", "mu3lab-worker.service")

Log = Callable[[str], None]
_cache: dict[str, Any] = {}


def _git(root: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr).strip()


def _head(root: Path) -> dict[str, str]:
    rc, out = _git(root, "log", "-1", "--format=%h%x09%cs")
    commit, _, date = out.partition("\t") if rc == 0 else ("", "", "")
    return {"commit": commit, "date": date}


def status(root: Path, *, refresh: bool = False) -> dict[str, Any]:
    """What GitHub has that this copy doesn't, and whether the dashboard may apply it."""
    from ctl import __version__

    result: dict[str, Any] = {
        "version": __version__,
        **_head(root),
        "available": False,
        "behind": 0,
        "changes": [],
        "blocked_reason": "",
        "checked_at": 0,
    }
    current_tag = platform_releases.exact_tag(root)
    if not current_tag:
        result["blocked_reason"] = (
            "This is a development checkout. Build and update it by hand; automatic updates require a versioned release."
        )
        return result
    rc, remote = _git(root, "remote", "get-url", "origin")
    if rc or not remote:
        result["blocked_reason"] = "This copy has no release remote. Update it from a terminal."
        return result
    _rc, edited = _git(root, "status", "--porcelain", "--untracked-files=no")
    if edited:
        result["blocked_reason"] = "This copy has edited files. Commit or undo them before updating."
        return result
    cached = _cache.get(str(root))
    now = time.time()
    if refresh or not cached or now - cached > FETCH_TTL_SECONDS:
        rc, _out = _git(root, "fetch", "--quiet", "--tags", "origin", timeout=60)
        if rc:
            result["blocked_reason"] = "Mu3Lab couldn't reach its release remote. Try again later."
            return result
        _cache[str(root)] = cached = now
    result["checked_at"] = int(cached)
    _rc, values = _git(root, "tag", "--list", "v*")
    versions = [
        (tuple(int(n) for n in match.groups()), tag)
        for tag in values.splitlines()
        if (match := platform_releases.TAG.fullmatch(tag))
    ]
    latest = max(versions)[1] if versions else current_tag
    result.update({"version": current_tag.removeprefix("v"), "current_release": current_tag, "latest_release": latest})
    if latest == current_tag:
        return result
    rc, _out = _git(root, "merge-base", "--is-ancestor", "HEAD", latest)
    if rc:
        result["blocked_reason"] = (
            "The next release does not follow this installed version. Review it before updating manually."
        )
        return result
    _rc, changes = _git(root, "log", "--no-merges", "--format=%s", "-n", "30", f"HEAD..{latest}")
    _rc, behind = _git(root, "rev-list", "--count", f"HEAD..{latest}")
    result.update({"available": True, "behind": int(behind), "changes": changes.splitlines()})
    return result


# --- Run by the newly pulled code -------------------------------------------------


def _context(root: Path, log: Log) -> dict[str, Any]:
    return {
        "root": root,
        "log_fn": lambda _step: log,
        "inputs": {},
        "wait_input": None,
        "stopped": lambda: False,
        "emit": lambda _event: None,
        "progress": lambda _step, update: log(str(update.get("activity") or "")) if update.get("activity") else None,
        "account": lambda: None,
    }


def _settled(meta: Step, check: dict) -> bool:
    from ctl import install

    return (
        check.get("status") == "ok"
        or install.fix_for_state(meta["id"], str(check.get("state", ""))) == "skip"
        or check.get("state") in meta.get("verify_ok_states", ())
    )


def plan(root: Path, log: Log) -> dict[str, Any]:
    """Installer steps this update needs, and those only ./install.sh can do."""
    from ctl import install

    ctx = _context(root, log)
    unattended, terminal = [], []
    for meta in install.STEPS:
        try:
            check = meta["check"](ctx)
        except Exception as exc:  # a check that cannot run here needs a person at the terminal
            check = {"status": "missing", "state": "check_failed", "detail": str(exc)}
        if _settled(meta, check):
            continue
        if meta["id"] in UNATTENDED or meta["id"] == RESTART_STEP:
            unattended.append(meta["id"])
        else:
            terminal.append(meta["label"])
    return {"ok": True, "unattended": unattended, "terminal": terminal}


def finish(root: Path, log: Log) -> dict[str, Any]:
    """Run the password-free installer steps, then prepare the restart."""
    from ctl import install
    from ctl.bootstrap import stamps

    ctx = _context(root, log)
    for meta in install.STEPS:
        if meta["id"] not in UNATTENDED:
            continue
        check = meta["check"](ctx)
        if _settled(meta, check):
            continue
        log(f"{meta['label']}…")
        result = meta["fix"](check, ctx)
        if not result.get("ok") or not _settled(meta, meta["check"](ctx)):
            return {"ok": False, "error": f"{meta['label']}: {result.get('error') or 'still not ready'}"}
    error = install.write_service_units(root, log)
    if error:
        return {"ok": False, "error": error}
    rc, out = install.actions.privilege._exec(["systemctl", "--user", "daemon-reload"])
    if rc:
        return {"ok": False, "error": f"systemd could not reload Mu3Lab's services: {out}"}
    stamps.write(install._services_stamp(), stamps.control_plane_digest(root))
    return {"ok": True}


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]

    def log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    command = argv[0] if argv else ""
    if command not in {"plan", "finish"}:
        print(json.dumps({"ok": False, "error": "usage: python -m ctl.self_update plan|finish"}))
        return 2
    try:
        result = plan(root, log) if command == "plan" else finish(root, log)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


# --- Run by the worker, still on the old code ---------------------------------------


class _Failed(Exception):
    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


def _new_code(root: Path, command: str, log: Log) -> dict[str, Any]:
    """Run ``plan`` or ``finish`` from the checkout as it is now on disk."""
    argv = [str(root / ".venv" / "bin" / "python"), "-m", "ctl.self_update", command]
    try:
        proc = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    assert proc.stderr is not None and proc.stdout is not None
    for line in proc.stderr:
        if line.strip():
            log(line.rstrip())
    output = proc.stdout.read().strip().splitlines()
    proc.wait()
    try:
        result = json.loads(output[-1]) if output else {}
    except ValueError:
        result = {}
    return result if isinstance(result, dict) and "ok" in result else {"ok": False, "error": "no result"}


def _python_packages(root: Path, log: Log) -> None:
    """The new code may need packages the old environment lacks; uv.lock is the truth."""
    log("Matching Python packages to the new release…")
    rc, out = toolchain.sync_python(root)
    if rc:
        raise _Failed("python_packages", "uv_sync_failed", f"Python packages could not be installed: {out[-300:]}")


def _run(argv: list[str], root: Path, timeout: int = 1800) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _update(root: Path, step: Callable[[str, str], None], log: Log) -> str:
    current = status(root, refresh=True)
    if current["blocked_reason"]:
        raise _Failed("check", "update_blocked", current["blocked_reason"])
    if not current["available"]:
        return "Mu3Lab is already up to date."
    rc, before = _git(root, "rev-parse", "HEAD")
    step("download", f"Downloading {current['behind']} change(s) from GitHub.")
    rc, out = _git(root, "checkout", "--quiet", "--detach", current["latest_release"], timeout=300)
    if rc:
        raise _Failed("download", "pull_failed", f"The update could not be applied: {out[-300:]}")
    with job_guard.uncancellable():
        try:
            platform_releases.assets(root, current["latest_release"])
        except (OSError, ValueError, httpx.HTTPError) as exc:
            _git(root, "checkout", "--quiet", "--detach", before)
            raise _Failed("download", "release_verification_failed", str(exc)) from None
        _python_packages(root, log)
        step("plan", "Checking what this update needs.")
        needs = _new_code(root, "plan", log)
        terminal = list(needs.get("terminal") or []) if needs.get("ok") else ["the installer checks"]
        if terminal:
            # Leave the running version exactly as it was.
            _git(root, "checkout", "--quiet", "--detach", before)
            raise _Failed(
                "plan",
                "needs_terminal",
                "This update needs administrator access for: "
                + ", ".join(terminal)
                + ". Nothing changed. In a terminal, run: cd "
                + str(root)
                + f" && git checkout {current['latest_release']} && ./install.sh",
            )
        step("install", "Installing the update.")
        done = _new_code(root, "finish", log)
        if not done.get("ok"):
            raise _Failed(
                "install",
                "install_failed",
                f"Mu3Lab downloaded the update but couldn't finish it ({done.get('error', 'unknown error')}). "
                f"Your apps keep running. In a terminal, run: cd {root} && ./install.sh",
            )
    head = _head(root)
    return f"Mu3Lab updated to {head['commit']} ({head['date']}). The dashboard restarts now; reload the page."


def restart(log: Log) -> None:
    """Restart the dashboard and this worker; the worker stops after its current job."""
    rc, out = _run(["systemctl", "--user", "--no-block", "restart", *UNITS], Path.cwd(), timeout=30)
    if rc:
        log(f"Restart failed ({out}); run ./start.sh to restart Mu3Lab.")


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    job_id, actor = str(job["id"]), str(job.get("actor") or worker_id)

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    def step(stage: str, detail: str) -> None:
        store.append_event(job_id, "stage", f"{stage}: {detail}")

    store.transition(job_id, "running", actor=actor, detail="Updating Mu3Lab.", step_id="check")
    try:
        detail = _update(root, step, log)
    except _Failed as failure:
        store.transition(
            job_id, "failed", actor=actor, detail=failure.message, error_code=failure.code, step_id=failure.stage
        )
        return
    store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
    if detail.startswith("Mu3Lab updated"):
        restart(log)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
