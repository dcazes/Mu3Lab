"""Household accounts through Authentik's REST API; people choose their own passwords."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ctl import access
from ctl.integrations.authentik import Authentik, AuthentikError
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked

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


def _view(user: dict[str, Any], held: dict[str, Any] | None = None) -> dict[str, Any]:
    """One person as the dashboard shows them; Mu3Lab's pending changes take precedence."""
    role = _role(user)
    if held and held["kind"] == "demoted" and role == "admin":
        role = "member"
    view = {
        "username": user["username"],
        "uid": user["uid"],
        "name": user["name"],
        "email": user["email"],
        "role": role,
        "active": bool(user["is_active"]) and not (held and held["kind"] == "deactivated"),
        "last_login": user.get("last_login") or "",
    }
    progress = access.summary(held)
    if progress:
        view["access"] = progress
    return view


def _effective_admin(user: dict[str, Any], held: dict[str, dict[str, Any]]) -> bool:
    return bool(user["is_active"]) and _role(user) == "admin" and str(user["uid"]) not in held


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
        held = access.holds()
        return [_view(user, held.get(str(user["uid"]))) for user in _people(Authentik.runtime())]
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


def change(username: str, action: str, authentik_origin: str, role: str = "", actor: str = "") -> dict[str, Any]:
    """Apply one account change.

    Deactivation and demotion are recorded as an access hold first, so Mu3Lab
    denies the old access at once; each revocation (Authentik included) then
    runs as a retried task. A failed Authentik call leaves its task pending
    instead of failing the change.
    """
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
            held = access.holds()
            uid = str(user["uid"])
            removing_admin = action == "deactivate" or (action == "role" and role != "admin")
            if (
                removing_admin
                and _effective_admin(user, held)
                and not any(other["pk"] != user["pk"] and _effective_admin(other, held) for other in users)
            ):
                raise PeopleError("Mu3Lab needs at least one administrator.")
            if removing_admin and username == "akadmin":
                # The automated-install API token belongs to this account. Disabling
                # it or dropping its privileges would also disable account management.
                raise PeopleError(
                    "The installation administrator must stay active to keep Mu3Lab's sign-in management working."
                )
            if action == "invite":
                return _invite(client, user, authentik_origin)
            if action == "deactivate":
                access.record(uid, username, "deactivated", actor)
                return {"person": _view(user, access.holds().get(uid))}
            if action == "reactivate":
                previous = held.get(uid) or {}
                unfinished_demotion = any(
                    task["target"] == "authentik_role" and task["state"] == "pending"
                    for task in previous.get("tasks", [])
                )
                access.release(uid, ("deactivated",))
                if unfinished_demotion:
                    # Reactivation must not hand back an administrator role whose removal never finished.
                    access.record(uid, username, "demoted", actor)
                user = client.update_user(user["pk"], is_active=True)
                return {"person": _view(user, access.holds().get(uid))}
            if role == "admin":
                access.release(uid, ("demoted",))
                user = _set_role(client, user, role)
                return {"person": _view(user, access.holds().get(uid))}
            if _role(user) == "admin":
                access.record(uid, username, "demoted", actor)
            try:
                user = _set_role(client, user, role)
            except AuthentikError:
                if uid not in access.holds():
                    raise
                # The hold denies administration now; the retried task finishes the group change.
            return {"person": _view(user, access.holds().get(uid))}
    except AuthentikError as exc:
        raise PeopleError(str(exc)) from exc


def operator_subjects() -> set[str]:
    """Only current active Authentik operators may receive shared connector credentials."""
    try:
        held = access.holds()
        return {
            str(user["uid"])
            for user in Authentik.runtime().users()
            if user.get("is_active") and _role(user) == "admin" and str(user["uid"]) not in held
        }
    except AuthentikError:
        raise PeopleError("Current operator membership could not be verified.") from None
