"""Household accounts through Authentik's REST API; people choose their own passwords."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked
from gateway_authority import Authority

ADMIN_GROUP = "mu3lab-operators"
MEMBER_GROUP = "mu3lab-household"
SUPERUSER_GROUP = "authentik Admins"
ROLES = {"admin": ADMIN_GROUP, "member": MEMBER_GROUP}
INVITE_HOURS = 24
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class PeopleError(ValueError):
    """An actionable account-management refusal."""


def _group_names(user: dict[str, Any]) -> set[str]:
    return {str(group["name"]) for group in (user.get("groups_obj") or []) if isinstance(group, dict)}


def _role(user: dict[str, Any]) -> str:
    names = _group_names(user)
    if names & {ADMIN_GROUP, SUPERUSER_GROUP}:
        return "admin"
    return "member" if MEMBER_GROUP in names else ""


def _view(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": user["username"],
        "uid": user["uid"],
        "name": user["name"],
        "email": user["email"],
        "role": _role(user),
        "active": user["is_active"],
        "last_login": user.get("last_login") or "",
    }


def _people(client: Authentik) -> list[dict[str, Any]]:
    return sorted(
        (user for user in client.users() if _role(user) and not user["username"].startswith("ak-")),
        key=lambda user: (user["name"], user["username"]),
    )


def _set_role(client: Authentik, user: dict[str, Any], role: str) -> dict[str, Any]:
    # Add first so an API error cannot remove the person's last membership.
    client.add_to_group(ROLES[role], user["pk"])
    removed = {ADMIN_GROUP, MEMBER_GROUP} - {ROLES[role]}
    if role != "admin":
        removed.add(SUPERUSER_GROUP)
    for group in sorted(removed):
        client.remove_from_group(group, user["pk"])
    return client.user(user["username"]) or user


def _invite(client: Authentik, user: dict[str, Any], origin: str) -> dict[str, Any]:
    link = urlsplit(client.recovery_link(user["pk"], INVITE_HOURS))
    # The recovery API is called on loopback; invite recipients need the private HTTPS origin.
    return {
        "invite": {
            "url": origin.rstrip("/") + link.path + ("?" + link.query if link.query else ""),
            "valid_hours": INVITE_HOURS,
        }
    }


def list_people() -> list[dict[str, Any]]:
    try:
        return [_view(user) for user in _people(Authentik.runtime())]
    except AuthentikError as exc:
        raise PeopleError(str(exc)) from exc


def add_person(name: str, email: str, role: str, authentik_origin: str) -> dict[str, Any]:
    name, email = name.strip()[:150], email.strip().lower()
    if not _EMAIL.fullmatch(email) or len(email) > 254:
        raise PeopleError("Enter a valid email address.")
    if role not in ROLES:
        raise PeopleError("Choose member or admin.")
    try:
        with locked(RuntimePaths().runtime / "people.lock"):
            client = Authentik.runtime()
            users = client.users()
            if any(str(user.get("email", "")).lower() == email for user in users):
                raise PeopleError("Someone with that email already has an account.")
            base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0])[:140] or "member"
            username, suffix = base, 1
            usernames = {user["username"] for user in users}
            while username in usernames:
                suffix += 1
                username = f"{base}{suffix}"
            user = client.create_user(username, name or username, email)
            user = _set_role(client, user, role)
            return {"person": _view(user), **_invite(client, user, authentik_origin)}
    except AuthentikError as exc:
        raise PeopleError(str(exc)) from exc


def change(username: str, action: str, authentik_origin: str, role: str = "") -> dict[str, Any]:
    if action not in {"role", "invite", "deactivate", "reactivate"} or not username or len(username) > 150:
        raise PeopleError("Unsupported change.")
    if action == "role" and role not in ROLES:
        raise PeopleError("Choose member or admin.")
    try:
        with locked(RuntimePaths().runtime / "people.lock"):
            client = Authentik.runtime()
            users = _people(client)
            user = next((person for person in users if person["username"] == username), None)
            if user is None:
                raise PeopleError("That person is not part of this Mu3Lab.")
            removing_admin = action == "deactivate" or (action == "role" and role != "admin")
            if (
                removing_admin
                and user["is_active"]
                and _role(user) == "admin"
                and not any(
                    other["pk"] != user["pk"] and other["is_active"] and _role(other) == "admin" for other in users
                )
            ):
                raise PeopleError("Mu3Lab needs at least one administrator.")
            if removing_admin and username == "akadmin":
                # The automated-install API token belongs to this account. Disabling
                # it or dropping its privileges would also disable account management.
                raise PeopleError(
                    "The installation administrator must stay active to keep Mu3Lab's sign-in management working."
                )
            if removing_admin:
                directory = RuntimePaths().projects / "mcp-gateway" / "authority"
                if (directory / "authority.db").is_file():
                    Authority(directory, initialize=False).revoke_subject(str(user["uid"]))
            if action == "invite":
                return _invite(client, user, authentik_origin)
            if action == "role":
                user = _set_role(client, user, role)
            else:
                user = client.update_user(user["pk"], is_active=action == "reactivate")
            return {"person": _view(user)}
    except AuthentikError as exc:
        raise PeopleError(str(exc)) from exc


def operator_subjects() -> set[str]:
    """Only current active Authentik operators may receive shared connector credentials."""
    try:
        return {
            str(user["uid"]) for user in Authentik.runtime().users() if user.get("is_active") and _role(user) == "admin"
        }
    except AuthentikError:
        raise PeopleError("Current operator membership could not be verified.") from None
