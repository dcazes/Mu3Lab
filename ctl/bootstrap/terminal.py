"""Mu3Lab :: ctl/bootstrap/terminal.py

WHAT: The installer the user sees. Runs every step of ctl/install.py in the
      terminal with one plain-language line per step. Everything a person must
      do happens in the first few minutes: choose their Mu3Lab name, email and
      password (or confirm them on a re-run), then approve this computer in
      Tailscale (the browser opens by itself and the installer continues on its
      own once approved). The long downloads run afterwards, unattended. It
      then waits for the core apps, saves the generated logins to the owner's
      vault, checks that every private address really loads, and opens the
      dashboard for the owner to sign in.
WHY:  Anything that needs no decision should just happen; anything that does
      needs step-by-step instructions written for someone new to all of this,
      and must come first so the owner can walk away during the downloads.
RUN:  ./install.sh (which runs `python -m ctl.bootstrap.terminal`).
SECURITY: The password is read without echo, kept only in this process's
      memory, sent only to the local Vaultwarden and Authentik containers, and
      never written to disk or to the log.
DEBUG: Full command output goes to .state/install-<time>.log; the path is
      printed on failure. Re-running skips every finished step.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from ctl import bootstrap_state, browser_extension, install
from ctl.service_state import tailscale_status

ROOT = Path(__file__).resolve().parents[2]
MIN_PASSWORD = 12
CORE_TIMEOUT = 45 * 60
# How long to wait for the owner's first dashboard sign-in before reminding them.
SIGN_IN_REMINDER = 5 * 60
# Checks that pass without anything ever being installed; "already set up" would mislead.
NO_SKIP_NOTE = frozenset({"host_supported", "dashboard_src", "nvidia_toolkit", "browser_extension"})
# After this step finishes, nothing needs the owner until the final sign-in.
LAST_QUESTION_STEP = "tailscale_join"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BOLD, DIM, GREEN, RED, BLUE, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[34m", "\033[0m"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Screen:
    """Prints steps; animates the running one when the output is a terminal."""

    def __init__(self, log_path: Path) -> None:
        self.tty = sys.stdout.isatty()
        self.lock = threading.Lock()
        self.current = ""
        self.activity = ""
        self.frame = 0
        self.log_file = log_path.open("a", encoding="utf-8")
        if self.tty:
            threading.Thread(target=self._animate, daemon=True).start()

    def color(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.tty else text

    def _clear(self) -> None:
        if self.tty and self.current:
            sys.stdout.write("\r\033[K")

    def _draw(self) -> None:
        if not (self.tty and self.current):
            return
        width = shutil.get_terminal_size((100, 20)).columns
        text = f"  {SPINNER[self.frame % len(SPINNER)]} {self.current}"
        if self.activity:
            text += self.color(DIM, f" — {self.activity}")
        visible = re.sub(r"\033\[[0-9;]*m", "", text)
        if len(visible) > width - 1:
            text = f"  {SPINNER[self.frame % len(SPINNER)]} {self.current}"[: width - 1]
        sys.stdout.write("\r\033[K" + text)
        sys.stdout.flush()

    def _animate(self) -> None:
        while True:
            time.sleep(0.12)
            with self.lock:
                self.frame += 1
                self._draw()

    def say(self, text: str = "") -> None:
        with self.lock:
            self._clear()
            print(text, flush=True)
            self._draw()

    def start(self, label: str) -> None:
        with self.lock:
            self.current, self.activity = label, ""
            if not self.tty:
                print(f"  … {label}", flush=True)
            self._draw()

    def progress(self, activity: str) -> None:
        with self.lock:
            self.activity = re.sub(r"\s+", " ", activity).strip()[:90]

    def finish(self, mark: str, text: str) -> None:
        with self.lock:
            self._clear()
            self.current, self.activity = "", ""
            print(f"  {mark} {text}", flush=True)

    def pause(self) -> None:
        """Stop animating so a prompt can use the terminal."""
        with self.lock:
            self._clear()
            self.current, self.activity = "", ""
            sys.stdout.flush()

    def log(self, step_id: str, line: str) -> None:
        self.log_file.write(f"[{time.strftime('%H:%M:%S')}] {step_id}: {line}\n")
        self.log_file.flush()


# --- Account --------------------------------------------------------------------


def _ask(prompt: str, secret: bool = False) -> str:
    try:
        return (getpass.getpass(prompt) if secret else input(prompt)).strip()
    except EOFError:
        raise SystemExit(
            "\nThe installer needs a terminal to ask questions. Run ./install.sh in a terminal window."
        ) from None


def ask_new_account(screen: Screen) -> dict[str, str]:
    screen.pause()
    print(f"\n{BOLD}Create your Mu3Lab account{RESET}" if screen.tty else "\nCreate your Mu3Lab account")
    print("  You will use this email and password to sign in to Mu3Lab.")
    print("  The same password also unlocks your password vault (Vaultwarden),")
    print("  where Mu3Lab saves the logins for your apps.\n")
    name = _ask("  Your first name: ") or "Owner"
    while True:
        email = _ask("  Your email address: ").lower()
        if EMAIL_RE.match(email):
            break
        print("  That doesn't look like an email address. Example: alex@example.com")
    while True:
        password = _ask(
            f"  Choose a password (at least {MIN_PASSWORD} characters, nothing is shown as you type): ", True
        )
        if len(password) < MIN_PASSWORD:
            print(f"  Too short: use at least {MIN_PASSWORD} characters. A short sentence is easy to remember.")
            continue
        local = email.split("@")[0]
        if len(local) >= 4 and local in password.lower():
            print("  Please don't use your email address in the password.")
            continue
        if _ask("  Type the same password again: ", True) != password:
            print("  The two passwords were different. Let's try again.")
            continue
        break
    print()
    print("  Important: write this password down and keep it somewhere safe.")
    print("  If you forget it, the logins saved in your vault cannot be recovered.")
    _ask("  Press Enter when you have written it down... ")
    print()
    return {"name": name, "email": email, "password": password}


def vault_login_error(email: str, password: str) -> str:
    """Why the vault refused this login, or "" when it was accepted."""
    from ctl.integrations import vaultwarden as vaultwarden_api

    try:
        with vaultwarden_api.VaultSession(f"http://127.0.0.1:{install.VAULTWARDEN_PROXY_PORT}") as session:
            session.login(email, password)
    except vaultwarden_api.VaultError as exc:
        return str(exc)
    return ""


def ask_existing_account(screen: Screen, verify: bool = True) -> dict[str, str]:
    """Vault exists but Authentik still needs its owner: confirm the same login.

    With verify=False (the vault is not running yet) the answers are checked
    later, when the vault is up, by the caller.
    """
    screen.pause()
    print("\nSign in with your Mu3Lab account")
    print("  Enter the email and password you chose when you first installed Mu3Lab.\n")
    for _attempt in range(5):
        email = _ask("  Your email address: ").lower()
        password = _ask("  Your password (nothing is shown as you type): ", True)
        error = vault_login_error(email, password) if verify else ""
        if error:
            print(f"  {error} Please try again.\n")
            continue
        print()
        return {"name": email.split("@")[0], "email": email, "password": password}
    raise SystemExit("Too many attempts. Run ./install.sh again when you have your password.")


def sign_in_needs_owner(ctx: dict) -> bool:
    """True when a later step (starting or finishing Authentik) needs the owner's login."""
    try:
        return install.check_authentik_setup(ctx).get("status") != "ok"
    except Exception:  # any doubt means asking now, not mid-install
        return True


# --- Tailscale ------------------------------------------------------------------------


def wait_for_tailscale(screen: Screen, job: dict, ctx: dict) -> None:
    step = next(item for item in job["steps"] if item["id"] == "tailscale_join")
    link = str((step.get("prompt") or {}).get("login_url") or "")
    meta = next(item for item in install.STEPS if item["id"] == "tailscale_join")
    screen.pause()
    if (step.get("prompt") or {}).get("connection_pending"):
        print("\nTailscale has started. Waiting for this device to be online with its private DNS name.")
        screen.start("Waiting for your private network connection")
    else:
        print("\nConnect this computer to Tailscale")
        print("  Tailscale is the free, private network that lets only your own devices")
        print("  reach Mu3Lab, from home or anywhere else.\n")
        print("  Your web browser is opening the Tailscale page. There:")
        print('    1. Sign in. No account yet? Choose "Sign up"; it is free.')
        print('    2. When Tailscale asks about this computer, click "Connect".')
        print("  You don't need to come back and press anything: this window continues")
        print("  by itself as soon as the computer is connected.\n")
        if link:
            print("  If the browser did not open, copy this link into it:")
            print(f"    {link}\n")
        else:
            print("  If no browser page opened, open a second terminal in this folder and run:")
            print("    ./tools/open_tailscale_login.sh\n")
        screen.start("Waiting for you to approve this computer in Tailscale")
    while not ctx["stopped"]():
        check = meta["check"](ctx)
        if check.get("status") == "ok":
            break
        time.sleep(3)
    screen.pause()


# --- After the foundation --------------------------------------------------------------


def wait_for_core_apps(screen: Screen) -> tuple[bool, str]:
    from ctl.jobs import JobStore

    store = JobStore.runtime()
    if store is None:
        return False, "The dashboard database is not available."
    screen.start("Starting your core apps (AI chat and models)")
    deadline = time.monotonic() + CORE_TIMEOUT
    while time.monotonic() < deadline:
        jobs = store.jobs_for_service("core-suite", limit=1)
        if jobs:
            latest = jobs[0]
            if latest["state"] == "succeeded":
                return True, ""
            if latest["state"] == "waiting_for_confirmation":
                # Normal on a new install: the apps run, AI chat waits for a provider.
                return True, "needs_provider"
            if latest["state"] in {"failed", "cancelled"}:
                return False, str(latest.get("detail") or latest.get("error") or "")
            screen.progress(str(latest.get("detail") or ""))
        time.sleep(3)
    return False, "The core apps are taking longer than expected."


def save_logins(account: dict[str, str], host: str) -> tuple[bool, str]:
    from ctl import vault_setup
    from ctl.control_state import ControlState
    from ctl.integrations import vaultwarden as vaultwarden_api
    from ctl.registry import load

    items = vault_setup.desired_items(
        registry=load(),
        host=host,
        owner_uid="",
        username=account["email"],
        email=account["email"],
        authentik_password=account["password"],
    )
    try:
        with vaultwarden_api.VaultSession(f"http://127.0.0.1:{install.VAULTWARDEN_PROXY_PORT}") as session:
            session.login(account["email"], account["password"])
            result = vault_setup.seed(session, items)
    except vaultwarden_api.VaultError as exc:
        return False, str(exc)
    state = ControlState.runtime()
    if state:
        state.mark_vault_seeded(account["email"])
    return True, f"{len(result.created) + len(result.updated)} logins saved"


def warm_up(url: str, timeout: float = 120, *, redirect_host: str = "") -> bool:
    """Require HTTPS success or an expected private Authentik redirect."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=20) as response:
                if response.status == 200:
                    return True
        except urllib.error.HTTPError as exc:
            if (
                exc.code in (302, 303, 307, 308)
                and redirect_host
                # Authentik's own pages redirect with a relative Location
                # ("/flows/..."); resolve it against the URL that was asked.
                and install._authentik_redirect_is_expected(
                    urllib.parse.urljoin(url, exc.headers.get("Location") or ""), redirect_host
                )
            ):
                return True
        except OSError:
            pass
        time.sleep(3)
    return False


def tailnet_name() -> str:
    status = tailscale_status()
    if status["state"] != "connected":
        return ""
    name = str(status["dns_name"])
    return name if re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net", name) else ""


def verify_dashboard(screen: Screen, host: str, stopped, timeout: float | None = None) -> tuple[bool, str]:
    """Check routes, then wait for a fresh snapshot from a signed-in operator.

    The owner may have walked away during the downloads, so by default this
    waits for them however long it takes (Ctrl+C stops it safely) and only
    reminds them what to do after a few minutes.
    """
    if not host:
        return False, "Tailscale is not online with a private DNS name. Run ./install.sh again to reconnect."
    screen.start("Checking your private HTTPS addresses")
    for port in (install.AUTHENTIK_SERVE_PORT, install.VAULTWARDEN_SERVE_PORT, install.DASHBOARD_SERVE_PORT):
        url = install.tailnet_https_origin(host, port)
        if not warm_up(url, redirect_host=host):
            return False, f"The private address {url} did not become ready."
    screen.finish(screen.color(GREEN, "✓"), "Private HTTPS routes are answering")
    dashboard = install.tailnet_https_origin(host, install.DASHBOARD_SERVE_PORT)
    started = time.time()
    screen.say(f"\n  Open {dashboard} and sign in with your Mu3Lab account.")
    screen.say("  Setup finishes after your dashboard data loads successfully.")
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        try:
            webbrowser.open(dashboard)
        except (OSError, webbrowser.Error):
            screen.say("  The browser could not open automatically; use the address above.")
    screen.start("Waiting for you to sign in to your dashboard")
    begun = time.monotonic()
    reminded = False
    while True:
        waited = time.monotonic() - begun
        if timeout is not None and waited >= timeout:
            break
        if stopped():
            return False, "Dashboard verification was stopped."
        if bootstrap_state.dashboard_ready_since(started):
            screen.finish(screen.color(GREEN, "✓"), "Signed-in dashboard data loaded successfully")
            return True, ""
        if not reminded and waited >= SIGN_IN_REMINDER:
            reminded = True
            screen.say(f"\n  Still waiting for you to sign in at {dashboard}")
            screen.say("  If the dashboard shows an error, check journalctl --user -u mu3lab-ctl.")
            screen.say("  You can also press Ctrl+C and run ./install.sh later to finish this step.")
        time.sleep(2)
    return False, (
        "No successful signed-in dashboard load was received in time. "
        "If the dashboard shows an error, check journalctl --user -u mu3lab-ctl. "
        "Run ./install.sh again to retry verification."
    )


# --- Main --------------------------------------------------------------------------------


def run() -> int:
    if not sys.stdin.isatty():
        print("Run ./install.sh in a terminal window; it needs to ask you a few questions.")
        return 1
    log_dir = ROOT / ".state"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"install-{time.strftime('%Y%m%d-%H%M%S')}.log"
    screen = Screen(log_path)
    stop = threading.Event()
    account: dict[str, str] = {}
    verified = True
    fresh = not install._vaultwarden_account_exists()
    # Every question is asked here, before anything is downloaded, so the
    # owner can walk away once Tailscale (the first step that needs them) is
    # connected.
    print_intro(screen, fresh)
    if fresh:
        account.update(ask_new_account(screen))
    elif sign_in_needs_owner({"root": ROOT}):
        verify = install.vaultwarden_health().get("state") == "ready"
        account.update(ask_existing_account(screen, verify=verify))
        verified = verify

    def get_account() -> dict[str, str]:
        nonlocal verified
        if account and not verified:
            # Asked up front while the vault was not running; it is now.
            verified = True
            error = vault_login_error(account["email"], account["password"])
            if error:
                account.clear()
                screen.pause()
                print(f"\n  Your vault did not accept the email and password you entered: {error}")
        if not account:
            account.update(ask_existing_account(screen))
        return account

    job = install.new_job()
    labels = {step["id"]: step["label"] for step in job["steps"]}
    shown_phases: set[str] = set()

    def emit(event: dict) -> None:
        step_id = str(event.get("id", ""))
        if event.get("type") == "step" and step_id in labels:
            status = event.get("status")
            if status == "installing":
                phase = install.PHASE_OF[step_id]
                if phase not in shown_phases:
                    shown_phases.add(phase)
                    screen.say("\n" + screen.color(BOLD, phase))
                screen.start(labels[step_id])
            elif status == "ready":
                quiet = step_id in NO_SKIP_NOTE
                note = screen.color(DIM, " (already set up)") if event.get("skipped") and not quiet else ""
                label = bitwarden_line() if step_id == "browser_extension" else ""
                screen.finish(screen.color(GREEN, "✓"), (label or labels[step_id]) + note)
                if step_id == LAST_QUESTION_STEP:
                    print_walk_away(screen)
            elif status == "failed":
                screen.finish(screen.color(RED, "✗"), labels[step_id])
        elif event.get("type") == "log":
            screen.log(step_id or "install", str(event.get("line", "")))

    def log_fn(step_id: str):
        return lambda line: screen.log(step_id, line)

    def progress(_step_id: str, update: dict) -> None:
        if update.get("activity"):
            screen.progress(str(update["activity"]))

    ctx: dict[str, Any] = {
        "root": ROOT,
        "log_fn": log_fn,
        "inputs": job["inputs"],
        "wait_input": lambda _step_id: wait_for_tailscale(screen, job, ctx),
        "stopped": stop.is_set,
        "emit": emit,
        "progress": progress,
        "account": get_account,
    }

    install.run_job(job, ctx)
    screen.pause()
    if job["status"] != "ready":
        return report_failure(screen, job, log_path)

    screen.say("\n" + screen.color(BOLD, "Finish setting up"))
    ok, detail = wait_for_core_apps(screen)
    needs_provider = detail == "needs_provider"
    if ok:
        screen.finish(screen.color(GREEN, "✓"), "Core apps are running")
    else:
        screen.finish(screen.color(RED, "!"), "Some core apps did not start yet")
        screen.say(
            f"    {detail or 'See the dashboard for details.'} The dashboard shows what went wrong and a Retry button."
        )

    host = tailnet_name()
    if account and fresh:
        screen.start("Saving your app logins to your vault")
        saved, message = save_logins(account, host)
        screen.finish(screen.color(GREEN, "✓") if saved else screen.color(RED, "!"), f"Vault: {message}")
    account.clear()

    dashboard = f"https://{host}:{install.DASHBOARD_SERVE_PORT}/" if host else ""
    verified, error = verify_dashboard(screen, host, stop.is_set)
    if not verified:
        screen.finish(screen.color(RED, "✗"), "Dashboard verification did not finish")
        screen.say(f"  {error}")
        screen.log("dashboard_readiness", error)
        screen.say(f"  Installer log: {log_path}")
        return 1
    return report_success(screen, dashboard, needs_provider)


def print_intro(screen: Screen, fresh: bool) -> None:
    screen.pause()
    if fresh:
        print("\n  Setup asks you a few things first: your first name, email and a password,")
        print("  then you connect this computer to Tailscale in your browser.")
        print("  After that it runs by itself, so you can walk away while it downloads.")
    else:
        print("\n  Anything setup needs from you comes first; after that it runs by itself.")


def print_walk_away(screen: Screen) -> None:
    screen.say("")
    screen.say(screen.color(BOLD, "  That's everything setup needs from you for now."))
    screen.say("  The rest runs by itself and can take a while: feel free to walk away.")
    screen.say("  When it is done, your dashboard opens in the browser; sign in there to finish.")


def bitwarden_line() -> str:
    """Name what was installed and where to find it, like the other finished steps."""
    browsers = browser_extension.status()["browsers"]
    if not browsers:
        return ""
    return f"Bitwarden password manager added to {' and '.join(browsers)} (the shield icon by the address bar)"


def report_failure(screen: Screen, job: dict, log_path: Path) -> int:
    failed = next((step for step in job["steps"] if step["status"] in ("failed", "waiting")), None)
    print()
    if failed and failed["status"] == "waiting":
        prompt = failed.get("prompt") or {}
        print(f"Setup needs one more thing: {prompt.get('title', failed['label'])}")
        if prompt.get("body"):
            print(f"  {prompt['body']}")
        if prompt.get("terminal_command"):
            print(f"  Run this in a terminal:\n    {prompt['terminal_command']}")
        print("  Then run ./install.sh again. Finished steps are skipped.")
        return 1
    print(f"Setup stopped at: {failed['label'] if failed else 'an unknown step'}")
    message = (failed or {}).get("error") or job.get("error") or ""
    if message:
        print(f"  Reason: {message}")
    print("  Nothing that was already installed has been undone.")
    print("  Fix the reason above if it names one, then run ./install.sh again;")
    print("  it continues where it stopped.")
    print(f"  Technical details were saved to: {log_path}")
    return 1


def report_success(screen: Screen, dashboard: str, needs_provider: bool) -> int:
    print()
    print(screen.color(BOLD, "Mu3Lab is installed."))
    print()
    if dashboard:
        print("  Your dashboard (works on any of your devices signed in to Tailscale):")
        print(f"    {screen.color(BLUE, dashboard)}")
        print()
        print("  Sign in with the email and password you chose.")
        browsers = browser_extension.status()["browsers"]
        if browsers:
            print()
            print(f"  Bitwarden (fills in your passwords) was added to {' and '.join(browsers)}.")
            print("  If the browser was already open, close it and open it again. Then click the")
            print("  Bitwarden shield next to the address bar and sign in with the same email and password.")
        if needs_provider:
            print("  First thing to do there: connect a free AI provider so the AI chat works.")
        print("  The dashboard's Home page lists every next step in order.")
    else:
        print("  Tailscale is not connected, so the dashboard address is unknown.")
        print("  Run ./install.sh again to finish connecting.")
    print()
    print("  To check or update Mu3Lab later, run ./install.sh again.")
    return 0


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("\n\nStopped. Nothing is broken; run ./install.sh to continue where you left off.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
