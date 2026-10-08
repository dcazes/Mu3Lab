"""Facts about this computer that generated app settings follow."""

from __future__ import annotations

import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ctl import process

DEFAULT_TIMEZONE = "UTC"


def _valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def timezone(etc: Path = Path("/etc"), run=process.completed) -> str:
    """The computer's IANA timezone (for example ``Europe/Paris``), else UTC."""
    candidates: list[str] = []
    try:
        candidates.append((etc / "timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    try:
        target = (etc / "localtime").resolve()
        if "zoneinfo" in target.parts:
            candidates.append("/".join(target.parts[target.parts.index("zoneinfo") + 1 :]))
    except OSError:
        pass
    try:
        proc = run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True, text=True, timeout=5)
        candidates.append(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return next((name for name in candidates if name and _valid(name)), DEFAULT_TIMEZONE)


def country_code(zone: str | None = None, table: Path = Path("/usr/share/zoneinfo/zone.tab")) -> str:
    """The two-letter country of the computer's timezone (``ca`` for America/Toronto), else ""."""
    zone = zone or timezone()
    try:
        lines = table.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        fields = line.split("\t")
        if len(fields) >= 3 and not line.startswith("#") and fields[2] == zone:
            return fields[0].lower()
    return ""


# Currencies of the countries whose time zones Mu3Lab sees most; anything else uses USD.
_EURO = ("at", "be", "cy", "de", "ee", "es", "fi", "fr", "gr", "hr", "ie", "it", "lt", "lu", "lv", "mt", "nl", "pt")
CURRENCIES = {
    **dict.fromkeys(_EURO, "EUR"),
    "ca": "CAD",
    "us": "USD",
    "mx": "MXN",
    "br": "BRL",
    "ar": "ARS",
    "gb": "GBP",
    "ch": "CHF",
    "se": "SEK",
    "no": "NOK",
    "dk": "DKK",
    "pl": "PLN",
    "cz": "CZK",
    "au": "AUD",
    "nz": "NZD",
    "jp": "JPY",
    "in": "INR",
    "za": "ZAR",
}


def currency_code(country: str | None = None) -> str:
    """The usual currency of the computer's country (``CAD`` for Canada), else ``USD``."""
    return CURRENCIES.get(country if country is not None else country_code(), "USD")
