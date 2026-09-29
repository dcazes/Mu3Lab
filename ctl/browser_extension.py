"""Add the Bitwarden extension to the owner's browsers, already pointed at their vault.

WHAT: Writes one machine policy file per installed Chromium-family browser
      (Google Chrome, Chromium, Brave). The file does exactly two things:
      installs Bitwarden and pre-sets its self-hosted server URL, so the owner
      only has to sign in with their Mu3Lab email and password.
WHY:  Choosing "Self-hosted" and pasting a server URL is the step beginners
      get stuck on; the browser's own policy mechanism removes it.
SCOPE: No other policy is set, so every other browser feature keeps its normal
      default. Visible side effects: the browser says "Managed by your
      organization", and Bitwarden can be turned off but not removed while
      Mu3Lab is installed. ./uninstall.sh deletes these files, and the browser
      then removes the extension on its next start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BITWARDEN_ID = "nngceckbapebfimnlniiiahkandclblb"
CHROME_WEB_STORE_UPDATES = "https://clients2.google.com/service/update2/crx"
POLICY_FILE = "mu3lab-bitwarden.json"


@dataclass(frozen=True)
class Browser:
    name: str
    policy_dir: str
    # Any of these existing means the browser is installed.
    markers: tuple[str, ...]

    @property
    def policy_path(self) -> str:
        return f"{self.policy_dir}/{POLICY_FILE}"


BROWSERS = (
    Browser("Google Chrome", "/etc/opt/chrome/policies/managed", ("/opt/google/chrome/chrome",)),
    # Debian's package and Ubuntu's snap read different policy folders.
    Browser("Chromium", "/etc/chromium/policies/managed", ("/usr/lib/chromium/chromium",)),
    Browser(
        "Chromium",
        "/etc/chromium-browser/policies/managed",
        ("/snap/bin/chromium", "/usr/lib/chromium-browser/chromium-browser"),
    ),
    Browser("Brave", "/etc/brave/policies/managed", ("/opt/brave.com/brave/brave",)),
)


def _on(root: Path, path: str) -> Path:
    return root / path.lstrip("/")


def installed(root: Path = Path("/")) -> list[Browser]:
    return [browser for browser in BROWSERS if any(_on(root, marker).exists() for marker in browser.markers)]


def names(browsers: list[Browser]) -> str:
    return ", ".join(dict.fromkeys(browser.name for browser in browsers))


def policy(server_url: str) -> dict[str, Any]:
    return {
        "ExtensionSettings": {
            BITWARDEN_ID: {
                # Installed and pinned for the owner; unlike "force_installed"
                # they can still turn it off or unpin it.
                "installation_mode": "normal_installed",
                "update_url": CHROME_WEB_STORE_UPDATES,
                "toolbar_pin": "default_pinned",
            }
        },
        # Bitwarden reads its server from managed storage (environment.base).
        "3rdparty": {"extensions": {BITWARDEN_ID: {"environment": {"base": server_url}}}},
    }


def render(server_url: str) -> str:
    return json.dumps(policy(server_url), indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def conflicting_file(browser: Browser, root: Path = Path("/")) -> str:
    """Another policy file already setting ExtensionSettings would silently win or lose
    against ours (the browser does not merge them), so Mu3Lab stays out of the way."""
    directory = _on(root, browser.policy_dir)
    if not directory.is_dir():
        return ""
    for path in sorted(directory.glob("*.json")):
        if path.name != POLICY_FILE and "ExtensionSettings" in _read_json(path):
            return str(path)
    return ""


def configured_server(browser: Browser, root: Path = Path("/")) -> str:
    current = _read_json(_on(root, browser.policy_path))
    try:
        return str(current["3rdparty"]["extensions"][BITWARDEN_ID]["environment"]["base"])
    except (KeyError, TypeError):
        return ""


def plan(server_url: str, root: Path = Path("/")) -> dict[str, list[Browser]]:
    """Sort installed browsers into ready / to_write / conflict for this server URL."""
    result: dict[str, list[Browser]] = {"ready": [], "to_write": [], "conflict": []}
    wanted = policy(server_url)
    for browser in installed(root):
        if conflicting_file(browser, root):
            result["conflict"].append(browser)
        elif _read_json(_on(root, browser.policy_path)) == wanted:
            result["ready"].append(browser)
        else:
            result["to_write"].append(browser)
    return result


def status(root: Path = Path("/")) -> dict[str, Any]:
    """What the dashboard needs to tailor its Bitwarden guide."""
    browsers = [browser for browser in BROWSERS if configured_server(browser, root)]
    return {
        "browsers": list(dict.fromkeys(browser.name for browser in browsers)),
        "server_url": configured_server(browsers[0], root) if browsers else "",
    }
