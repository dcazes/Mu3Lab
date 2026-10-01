"""Keep each person's Mu3Lab logins in their vault, automatically.

Mu3Lab's service account owns the shared "Mu3Lab" organization in Vaultwarden.
Every active person in Mu3Lab whose Authentik email has a Vaultwarden account
is a member with one private collection. Logins Mu3Lab generates for them
(app accounts, and for administrators the AI gateway admin pages) are written
there with the app's private address, so their Bitwarden app fills them in.

The run is idempotent and safe to repeat: items are matched by their
``mu3lab_id`` field, passwords change only when Mu3Lab's copy changed, and a
person who has no vault account yet is invited and picked up as soon as they
create one. Nothing reads or touches anyone's personal vault.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ctl import onboarding_state, workflow_secrets
from ctl.identity import authentik_only
from ctl.registry import load as load_registry
from ctl.runtime import RuntimePaths
from ctl.secret_file import locked, write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text
from ctl.vault_org import STATUS_ACCEPTED, STATUS_CONFIRMED, OrgSession
from ctl.vault_setup import FREELLMAPI_ACCOUNT, VAULTWARDEN_LOCAL_URL
from ctl.vaultwarden_api import MATCH_HOST, VaultError, register

SERVICE_EMAIL = "mu3lab-service@vault.mu3lab.invalid"
SERVICE_NAME = "Mu3Lab"
# Each person sees only their own collection, so one plain name reads best in
# Bitwarden, next to the "Mu3Lab AI providers" folder the installer creates.
COLLECTION_NAME = "Mu3Lab app logins"


@dataclass
class Item:
    mu3lab_id: str
    name: str
    username: str
    password: str
    url: str
    notes: str
    # Called once the item is safely in the person's vault.
    saved: list[Any] = field(default_factory=list)


def _origin(host: str, port: int | None) -> str:
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


def _service_password(paths: RuntimePaths) -> tuple[str, bool]:
    """Mu3Lab's own vault password, created once; True when it was just created."""
    path = paths.runtime / "vault-service.env"
    values = read_runtime_env(path)
    if values.get("PASSWORD"):
        return values["PASSWORD"], False
    password = secrets.token_urlsafe(32)
    write_atomic(path, runtime_env_text({"EMAIL": SERVICE_EMAIL, "PASSWORD": password}).encode())
    return password, True


def _session(paths: RuntimePaths) -> OrgSession:
    password, new = _service_password(paths)
    if new:
        try:
            register(VAULTWARDEN_LOCAL_URL, SERVICE_EMAIL, password, SERVICE_NAME)
        except VaultError as exc:
            if exc.code != "exists":
                raise
    session = OrgSession(VAULTWARDEN_LOCAL_URL)
    session.login(SERVICE_EMAIL, password)
    return session


def items_for(person: dict[str, Any], host: str, paths: RuntimePaths) -> list[Item]:
    """The logins Mu3Lab owes this person's vault right now."""
    registry = load_registry()
    uid = str(person.get("uid") or "")
    items: list[Item] = []
    for record in onboarding_state.pending_logins(uid, paths):
        service_id = str(record["service_id"])
        if authentik_only(service_id):
            continue
        items.append(
            Item(
                mu3lab_id=f"service:{service_id}",
                name=registry.get(service_id).name,
                username=str(record["login_username"]),
                password=str(record["password"]),
                url=str(record.get("login_url") or ""),
                notes="Created by Mu3Lab. Bitwarden fills this in when the app asks for its login.",
                saved=[lambda service_id=service_id: onboarding_state.vault_saved(service_id, uid, paths)],
            )
        )
    for meta in workflow_secrets.metadata(uid, paths):
        credential = workflow_secrets.reveal(meta["id"], uid, paths)
        if not credential or any(item.mu3lab_id == f"service:{credential['service_id']}" for item in items):
            continue
        if authentik_only(credential["service_id"]):
            continue
        handoff_id = credential["id"]
        items.append(
            Item(
                mu3lab_id=f"service:{credential['service_id']}",
                name=registry.get(credential["service_id"]).name,
                username=credential["username"] or credential["email"],
                password=credential["password"],
                url=credential["login_url"],
                notes="Created by Mu3Lab when the app was installed.",
                saved=[lambda handoff_id=handoff_id: workflow_secrets.delete(handoff_id, uid, paths)],
            )
        )
    if person.get("role") == "admin" and host:
        freellmapi = read_runtime_env(paths.projects / "freellmapi" / ".env").get("FREELLMAPI_ADMIN_PASSWORD", "")
        if freellmapi and not _saved_personally(person, paths):
            items.append(
                Item(
                    "service:freellmapi",
                    "FreeLLMAPI dashboard",
                    FREELLMAPI_ACCOUNT,
                    freellmapi,
                    _origin(host, registry.get("freellmapi").private_https_port),
                    "Created by Mu3Lab. Authentik guards this page; this login unlocks it.",
                )
            )
        master_key = read_runtime_env(paths.projects / "litellm" / ".env").get("LITELLM_MASTER_KEY", "")
        if master_key:
            items.append(
                Item(
                    "service:litellm",
                    "LiteLLM admin",
                    "admin",
                    master_key,
                    _origin(host, registry.get("litellm").private_https_port),
                    "Created by Mu3Lab. Authentik guards this page; this login unlocks it.",
                )
            )
    return [item for item in items if item.password]


def _saved_personally(person: dict[str, Any], paths: RuntimePaths) -> bool:
    """Whether the older master-password flow already saved the FreeLLMAPI login to their personal vault."""
    from ctl.control_state import ControlState

    state = ControlState.runtime(paths)
    seeded = state.vault_seeded() if state else {}
    # Older installs recorded the person's email here, newer ones their username.
    who = {str(person.get("username") or ""), str(person.get("email") or "").lower()} - {""}
    return bool(seeded.get("seeded")) and str(seeded.get("seeded_by") or "").lower() in who


def _state(paths: RuntimePaths) -> dict[str, Any]:
    try:
        return json.loads((paths.runtime / "vault-org.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status(paths: RuntimePaths = RuntimePaths()) -> dict[str, Any]:
    """Last automatic save, for the dashboard; contains no secrets."""
    state = _state(paths)
    return {key: state.get(key) for key in ("last_run", "ok", "error", "people")}


def sync(people: list[dict[str, Any]], host: str, log, paths: RuntimePaths = RuntimePaths()) -> dict[str, Any]:
    """Bring every person's Mu3Lab collection up to date. Never raises for one person's problem."""
    with locked(paths.runtime / "vault-org.lock"):
        state = _state(paths)
        collections: dict[str, str] = dict(state.get("collections") or {})
        report: list[dict[str, Any]] = []
        error = ""
        try:
            with _session(paths) as session:
                org_id, org_key = session.ensure_organization(SERVICE_EMAIL)
                members = {member.email: member for member in session.members(org_id)}
                names = session.collections(org_id, org_key)
                existing = {
                    login.fields.get("mu3lab_id", ""): login
                    for login in session.logins(org_id, org_key)
                    if login.fields.get("mu3lab_id")
                }
                for person in people:
                    report.append(
                        _sync_person(
                            session, org_id, org_key, person, host, members, names, collections, existing, paths, log
                        )
                    )
        except VaultError as exc:
            error = str(exc)
            log(f"Saving logins to Vaultwarden is paused: {error}")
        result = {
            "last_run": datetime.now(UTC).isoformat(timespec="seconds"),
            "ok": not error,
            "error": error,
            "people": report,
            "collections": collections,
        }
        write_atomic(paths.runtime / "vault-org.json", json.dumps(result).encode())
        return result


def _sync_person(session, org_id, org_key, person, host, members, names, collections, existing, paths, log):
    email = str(person.get("email") or "").lower()
    uid = str(person.get("uid") or "")
    name = str(person.get("name") or person.get("username") or email)
    view: dict[str, Any] = {"uid": uid, "name": name, "state": "", "saved": 0, "waiting": 0}
    if not email or not uid or not person.get("active", True):
        view["state"] = "skipped"
        return view
    member = members.get(email)
    try:
        if member is None:
            session.invite(org_id, email)
            member = next(m for m in session.members(org_id) if m.email == email)
            members[email] = member
        if member.status == STATUS_ACCEPTED:
            session.confirm(org_id, member, org_key)
            member.status = STATUS_CONFIRMED
    except VaultError as exc:
        log(f"Vault sharing for {name} is waiting: {exc}")
    items = items_for(person, host, paths)
    view["waiting"] = len(items)
    if member is None or member.status != STATUS_CONFIRMED:
        # They have not created their Vaultwarden account yet; their logins wait.
        view["state"] = "waiting_for_account"
        return view
    collection = collections.get(uid)
    if collection not in names:
        collection = session.create_collection(org_id, org_key, COLLECTION_NAME, [member.id])
        collections[uid] = collection
        names[collection] = COLLECTION_NAME
    elif names[collection] != COLLECTION_NAME:
        session.rename_collection(org_id, org_key, collection, COLLECTION_NAME, [member.id])
        names[collection] = COLLECTION_NAME
    _retire_authentik_only(session, uid, existing, paths, log)
    for item in items:
        key = f"{uid}:{item.mu3lab_id}"
        fields = {"mu3lab_id": key}
        payload = {
            "name": item.name,
            "username": item.username,
            "password": item.password,
            "uris": [(item.url, MATCH_HOST)] if item.url else [],
            "notes": item.notes,
            "fields": fields,
        }
        current = existing.get(key)
        try:
            if current is None:
                session.create_login(org_id, org_key, collection, **payload)
            elif session_password(current, org_key) != item.password or current.username != item.username:
                session.update_login(current, org_id, org_key, **payload)
        except VaultError as exc:
            log(f"Could not save {item.name} for {name}: {exc}")
            continue
        for done in item.saved:
            done()
        view["saved"] += 1
    view["waiting"] -= view["saved"]
    view["state"] = "up_to_date" if not view["waiting"] else "partly_saved"
    return view


def _retire_authentik_only(session, uid: str, existing: dict[str, Any], paths: RuntimePaths, log) -> None:
    """Remove admin logins saved before these apps became Authentik-only, and forget them."""
    prefix = f"{uid}:service:"
    for key, login in list(existing.items()):
        if key.startswith(prefix) and authentik_only(key[len(prefix) :]):
            try:
                session.delete_login(login.id)
            except VaultError as exc:
                log(f"Could not remove the old {login.name} login: {exc}")
                continue
            existing.pop(key)
    for record in onboarding_state.pending_logins(uid, paths):
        if authentik_only(str(record["service_id"])):
            onboarding_state.discard_password(str(record["service_id"]), paths)
    for meta in workflow_secrets.metadata(uid, paths):
        if authentik_only(str(meta.get("service_id") or "")):
            workflow_secrets.delete(meta["id"], uid, paths)


def run(log) -> dict[str, Any] | None:
    """Sync everyone, as the worker and the dashboard's "save now" do."""
    from ctl import people
    from ctl.service_state import tailnet_dns_name

    paths = RuntimePaths()
    if not paths.runtime.is_dir():
        return None
    try:
        everyone = people.list_people()
    except people.PeopleError as exc:
        log(f"Saving logins to Vaultwarden waits for Authentik: {exc}")
        return None
    return sync(everyone, tailnet_dns_name(), log, paths)


def session_password(login, org_key) -> str:
    from ctl.vaultwarden_api import SymmetricKey, decrypt, decrypt_bytes

    key = SymmetricKey.from_bytes(decrypt_bytes(login.raw["key"], org_key)) if login.raw.get("key") else org_key
    return decrypt((login.raw.get("login") or {}).get("password"), key)
