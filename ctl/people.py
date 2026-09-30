"""Household people: Authentik accounts in Mu3Lab's member or admin group.

Mu3Lab never handles these people's passwords. Adding someone creates their
Authentik account without a password and returns a one-time sign-in link valid
for a day; opening it signs them in once, and they set their own password in
Authentik. Removing someone deactivates the account rather than deleting it,
so what they stored in apps is kept.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ctl import actions

ADMIN_GROUP = "mu3lab-operators"
MEMBER_GROUP = "mu3lab-household"
ROLES = {"admin": ADMIN_GROUP, "member": MEMBER_GROUP}
INVITE_HOURS = 24
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MARK = "MU3LAB_PEOPLE="

# Runs inside Authentik's own shell. Input arrives as one JSON line on stdin.
_SCRIPT = r"""
import json, re, sys
from datetime import timedelta
from django.db import transaction
from django.utils.timezone import now
from authentik.core.models import Group, User
from authentik.recovery.lib import create_recovery_token

request = json.loads(sys.stdin.readline())
admins = Group.objects.get_or_create(name="mu3lab-operators")[0]
members = Group.objects.get_or_create(name="mu3lab-household")[0]
superusers = Group.objects.filter(name="authentik Admins").first()


def role(user):
    names = set(user.groups.values_list("name", flat=True))
    if names & {"mu3lab-operators", "authentik Admins"}:
        return "admin"
    return "member" if "mu3lab-household" in names else ""


def view(user):
    return {
        "username": user.username,
        "name": user.name,
        "email": user.email,
        "role": role(user),
        "active": user.is_active,
        "last_login": user.last_login.isoformat() if user.last_login else "",
        "has_password": user.has_usable_password(),
    }


def people():
    users = User.objects.filter(groups__name__in=["mu3lab-operators", "mu3lab-household", "authentik Admins"])
    return [view(user) for user in users.distinct().order_by("name", "username") if not user.username.startswith("ak-")]


def set_role(user, value):
    user.groups.remove(admins, members)
    (admins if value == "admin" else members).users.add(user)


def admin_count(exclude=None):
    users = User.objects.filter(is_active=True, groups__name__in=["mu3lab-operators", "authentik Admins"])
    return users.exclude(pk=getattr(exclude, "pk", None)).distinct().count()


result = {}
action = request["action"]
with transaction.atomic():
    if action == "list":
        result = {"people": people()}
    elif action == "add":
        email = request["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise SystemExit("MU3LAB_PEOPLE_ERROR=Someone with that email already has an account.")
        base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0]) or "member"
        username, suffix = base, 1
        while User.objects.filter(username=username).exists():
            suffix += 1
            username = f"{base}{suffix}"
        user = User.objects.create(username=username, name=request["name"] or username, email=email)
        user.set_unusable_password()
        user.save()
        set_role(user, request["role"])
        _token, path = create_recovery_token(user, now() + timedelta(hours=request["hours"]), "Mu3Lab")
        result = {"person": view(user), "invite_path": path}
    else:
        user = User.objects.filter(username=request["username"]).first()
        if user is None or role(user) == "":
            raise SystemExit("MU3LAB_PEOPLE_ERROR=That person is not part of this Mu3Lab.")
        if action == "role":
            if request["role"] != "admin" and role(user) == "admin" and admin_count(exclude=user) == 0:
                raise SystemExit("MU3LAB_PEOPLE_ERROR=Mu3Lab needs at least one administrator.")
            set_role(user, request["role"])
        elif action == "invite":
            _token, path = create_recovery_token(user, now() + timedelta(hours=request["hours"]), "Mu3Lab")
            result = {"invite_path": path}
        elif action == "deactivate":
            if role(user) == "admin" and admin_count(exclude=user) == 0:
                raise SystemExit("MU3LAB_PEOPLE_ERROR=Mu3Lab needs at least one administrator.")
            user.is_active = False
            user.save()
        elif action == "reactivate":
            user.is_active = True
            user.save()
        result = result or {"person": view(user)}
print("MU3LAB_PEOPLE=" + json.dumps(result))
"""


class PeopleError(ValueError):
    """A request Authentik refused, with a message fit to show the admin."""


def _run(request: dict[str, Any]) -> dict[str, Any]:
    rc, output = actions.docker_cmd_with_stdin(
        ["docker", "exec", "-i", "authentik-server-1", "ak", "shell", "-c", _SCRIPT],
        json.dumps(request) + "\n",
        lambda _line: None,
        timeout=90,
    )
    refusal = next((line.split("=", 1)[1] for line in output.splitlines() if "MU3LAB_PEOPLE_ERROR=" in line), "")
    if refusal:
        raise PeopleError(refusal)
    line = next((line for line in reversed(output.splitlines()) if line.startswith(_MARK)), "")
    if rc or not line:
        raise PeopleError("Authentik did not answer. Check that it is running, then try again.")
    return json.loads(line.removeprefix(_MARK))


def _invite(result: dict[str, Any], authentik_origin: str) -> dict[str, Any]:
    path = str(result.pop("invite_path", ""))
    if path:
        result["invite"] = {"url": authentik_origin.rstrip("/") + path, "valid_hours": INVITE_HOURS}
    return result


def list_people() -> list[dict[str, Any]]:
    return list(_run({"action": "list"})["people"])


def add_person(name: str, email: str, role: str, authentik_origin: str) -> dict[str, Any]:
    name, email = name.strip()[:150], email.strip()
    if not _EMAIL.fullmatch(email) or len(email) > 254:
        raise PeopleError("Enter a valid email address.")
    if role not in ROLES:
        raise PeopleError("Choose member or admin.")
    return _invite(
        _run({"action": "add", "name": name, "email": email, "role": role, "hours": INVITE_HOURS}), authentik_origin
    )


def change(username: str, action: str, authentik_origin: str, role: str = "") -> dict[str, Any]:
    if action not in {"role", "invite", "deactivate", "reactivate"} or not username or len(username) > 150:
        raise PeopleError("Unsupported change.")
    if action == "role" and role not in ROLES:
        raise PeopleError("Choose member or admin.")
    request = {"action": action, "username": username, "role": role, "hours": INVITE_HOURS}
    return _invite(_run(request), authentik_origin)
