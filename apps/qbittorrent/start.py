"""Mu3Lab's start for qBittorrent: set the settings Mu3Lab owns, then run the image's own start.

Runs as root inside the container at every start, before qBittorrent does:

- Sign-in. Authentik admits administrators at the private address; qBittorrent then skips its
  own login for its own Docker network (only this app's containers and this computer are on
  it) and for itself (Gluetun's port-forwarding command). Nobody types a qBittorrent password:
  the one set here is random and never kept.
- Where files go. New torrents land in the Downloads library; a category saves straight into
  the library it is named after (Music, Audiobooks, Podcasts, Roms), so those apps pick the
  files up. Unfinished pieces wait in Downloads/.incomplete, which backups skip.
- The VPN. While it is on, qBittorrent may use only the VPN's network interface.
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import ipaddress
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

CONFIG = Path("/config/qBittorrent/config")
SETTINGS = CONFIG / "qBittorrent.conf"
CATEGORIES = CONFIG / "categories.json"
DOWNLOADS = Path("/media/downloads")
INCOMPLETE = DOWNLOADS / ".incomplete"
# Category -> the library folder it saves into. Music is Navidrome's, outside the media root.
LIBRARIES = {
    "music": Path("/music"),
    "audiobooks": Path("/media/audiobooks"),
    "podcasts": Path("/media/podcasts"),
    "roms": Path("/media/roms"),
}
VPN_INTERFACE = "tun0"
OWNER = (int(os.environ.get("PUID", "33")), int(os.environ.get("PGID", "33")))


def password_hash() -> str:
    """A random password in qBittorrent's own format (PBKDF2-SHA512, 100,000 rounds)."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha512", secrets.token_urlsafe(32).encode(), salt, 100_000)
    return f'"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"'


def own_networks() -> str:
    """This container's own Docker network(s), from its eth0 addresses."""
    output = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", "eth0"], capture_output=True, text=True).stdout
    networks = [str(ipaddress.ip_interface(part).network) for line in output.splitlines() for part in line.split()[3:4]]
    if not networks:
        raise SystemExit("Mu3Lab: qBittorrent has no network address on eth0")
    return ",".join(networks)  # a list, in qBittorrent's settings file


def libraries() -> dict[str, Path]:
    """The libraries on this computer: Music only while Navidrome shares it, others once they exist."""
    found = {name: path for name, path in LIBRARIES.items() if name != "music" and path.is_dir()}
    if os.environ.get("MU3LAB_MUSIC_SHARED") == "1" and LIBRARIES["music"].is_dir():
        found["music"] = LIBRARIES["music"]
    return found


def writable(path: Path) -> None:
    """qBittorrent writes here as www-data, as Nextcloud does; files already inside are left alone."""
    path.mkdir(parents=True, exist_ok=True)
    if (path.stat().st_uid, path.stat().st_gid) != OWNER:
        os.chown(path, *OWNER)


def settings(vpn: bool) -> None:
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str  # qBittorrent's keys are case-sensitive
    if SETTINGS.is_file():
        parser.read(SETTINGS, encoding="utf-8")
    owned = {
        "Preferences": {
            "WebUI\\Port": os.environ.get("QBT_WEBUI_PORT", "8479"),
            # A Host header must name this server (or localhost): no DNS-rebinding page gets in.
            "WebUI\\HostHeaderValidation": "true",
            # Quoted: in qBittorrent's settings file an unquoted ";" starts a comment.
            "WebUI\\ServerDomains": '"' + os.environ.get("MU3LAB_SERVER_DOMAINS", "localhost") + '"',
            "WebUI\\Username": "mu3lab",
            "WebUI\\LocalHostAuth": "false",
            "WebUI\\AuthSubnetWhitelistEnabled": "true",
            "WebUI\\AuthSubnetWhitelist": own_networks(),
            # Behind a proxy qBittorrent would see each visitor's own address and ask for a login.
            "WebUI\\ReverseProxySupportEnabled": "false",
            "WebUI\\UseUPnP": "false",
        },
        "BitTorrent": {
            "Session\\DefaultSavePath": str(DOWNLOADS),
            "Session\\TempPath": str(INCOMPLETE),
            "Session\\TempPathEnabled": "true",
            # New torrents follow their category's folder.
            "Session\\DisableAutoTMMByDefault": "false",
            "Session\\Interface": VPN_INTERFACE if vpn else "",
            "Session\\InterfaceName": VPN_INTERFACE if vpn else "",
        },
        "Network": {"PortForwardingEnabled": "false"},
    }
    for section, values in owned.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in values.items():
            parser.set(section, key, value)
    if not parser.has_option("Preferences", "WebUI\\Password_PBKDF2"):
        parser.set("Preferences", "WebUI\\Password_PBKDF2", password_hash())
    CONFIG.mkdir(parents=True, exist_ok=True)
    with SETTINGS.open("w", encoding="utf-8") as handle:
        parser.write(handle, space_around_delimiters=False)


def categories(found: dict[str, Path]) -> None:
    """Add a category for each library and drop Mu3Lab's for one that is gone; keep anyone else's."""
    try:
        current = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    for name, path in LIBRARIES.items():
        entry = current.get(name)
        if name in found:
            current[name] = {**(entry if isinstance(entry, dict) else {}), "save_path": str(path)}
        elif isinstance(entry, dict) and entry.get("save_path") == str(path):
            del current[name]
    CATEGORIES.write_text(json.dumps(current, indent=4) + "\n", encoding="utf-8")


def main() -> None:
    found = libraries()
    for path in (DOWNLOADS, INCOMPLETE, *found.values()):
        writable(path)
    # Kopia skips what .kopiaignore names: half-downloaded pieces are not worth a backup.
    ignore = DOWNLOADS / ".kopiaignore"
    if not ignore.exists():
        ignore.write_text(".incomplete/\n", encoding="utf-8")
    settings(os.environ.get("MU3LAB_VPN", "").lower() in {"true", "1", "yes", "on"})
    categories(found)
    os.execv("/entrypoint.sh", ["/entrypoint.sh", *sys.argv[1:]])


if __name__ == "__main__":
    main()
