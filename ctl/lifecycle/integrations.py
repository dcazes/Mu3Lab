"""App-specific setup that runs against live containers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.jobs import JobStore, redact
from ctl.registry import load as load_registry

Log = Callable[[str], None]
OLLAMA = "http://127.0.0.1:11434"
EMBEDDING_MODEL = "nomic-embed-text"


def surfsense_embedding_preflight(store: JobStore, job_id: str, root: Path) -> tuple[bool, str]:
    """Verify SurfSense's fixed internal Ollama embedding dependency."""
    store.append_event(job_id, "stage", "embedding_check: Verifying the curated Ollama embedding model.")
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, f"Ollama is not available for SurfSense embeddings: {exc}"
    names = {str(item.get("name", "")).split(":", 1)[0] for item in payload.get("models", []) if isinstance(item, dict)}
    if EMBEDDING_MODEL not in names:
        store.append_event(job_id, "stage", "embedding_model_pull: Pulling the required local embedding model.")
        project = load_registry().get("ollama").compose_path(root)
        rc, _ = actions.compose_exec(
            project,
            "ollama",
            ["ollama", "pull", EMBEDDING_MODEL],
            lambda line: store.append_event(job_id, "log", line),
            timeout=600,
        )
        if rc:
            return False, "The required Ollama embedding model could not be prepared."
    request = urllib.request.Request(
        f"{OLLAMA}/api/embeddings",
        method="POST",
        data=json.dumps({"model": EMBEDDING_MODEL, "prompt": "Mu3Lab readiness"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        vector = result.get("embedding")
        if not isinstance(vector, list) or not vector:
            return False, "Ollama returned no embedding vector."
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, f"The local embedding probe failed: {exc}"
    return True, "Ollama embedding model is ready."


def configure_adventurelog_oidc(project: Path, log: Log) -> tuple[bool, str]:
    """Upsert AdventureLog's supported django-allauth SocialApp idempotently."""
    code = (
        "import os; from allauth.socialaccount.models import SocialApp; "
        "from django.contrib.sites.models import Site; "
        "cid=os.environ['ADVENTURELOG_OIDC_CLIENT_ID']; "
        "app,_=SocialApp.objects.update_or_create(provider='openid_connect', provider_id=cid, "
        "defaults={'name':'Authentik','client_id':cid,'secret':os.environ['ADVENTURELOG_OIDC_CLIENT_SECRET'],"
        "'settings':{'server_url':os.environ['ADVENTURELOG_OIDC_DISCOVERY_URL']}}); "
        "app.sites.set(Site.objects.all()); print('MU3LAB_OIDC_APP_OK')"
    )
    rc, output = actions.compose_exec(project, "app", ["python", "manage.py", "shell", "-c", code], log, timeout=120)
    if rc or "MU3LAB_OIDC_APP_OK" not in output:
        return False, "AdventureLog could not confirm its Authentik SocialApp: " + redact(output)
    return True, "AdventureLog Authentik SocialApp is configured."


def adopt_mealie_admin(project: Path, owner: dict, log: Log) -> tuple[bool, str]:
    """Make Mealie's built-in administrator the owner's Authentik account.

    Mealie seeds ``changeme@example.com`` and shows a "first login" screen
    pre-filled with it while that account exists. Mealie's OIDC sign-in adopts
    an existing account whose email matches the verified Authentik email, so
    giving the seeded administrator the owner's email (and a password nobody
    knows) makes their first Authentik sign-in land on it, with no setup.
    """
    email = str(owner.get("email") or "").strip().lower()
    if not email:
        return False, "Mealie needs the owner's Authentik email to set up its administrator."
    request = json.dumps(
        {
            "email": email,
            "username": str(owner.get("username") or email.split("@", 1)[0]),
            "name": str(owner.get("display_name") or owner.get("username") or email),
        }
    )
    rc, output = actions.compose_exec(
        project, "mealie", ["/opt/mealie/bin/python", "-c", _MEALIE_ADOPT, request], log, timeout=60
    )
    if rc == 0 and "MU3LAB_MEALIE_OWNER_OK" in output:
        return True, "Mealie's administrator is your Authentik account."
    return False, "Mealie could not set up its administrator for Authentik sign-in: " + redact(output)


_MEALIE_ADOPT = """
import json, secrets, sys
from mealie.core.security.hasher import get_hasher
from mealie.db.db_setup import session_context
from mealie.db.models.users.users import AuthMethod, User

r = json.loads(sys.argv[1])
with session_context() as session:
    owner = session.query(User).filter(User.email == r["email"]).first()
    seeded = session.query(User).filter(User.email == "changeme@example.com").first()
    if seeded is not None:
        seeded.password = get_hasher().hash(secrets.token_urlsafe(48))
        if owner is None:
            seeded.email, seeded.username, seeded.full_name = r["email"], r["username"], r["name"]
            seeded.auth_method, seeded.admin = AuthMethod.OIDC, True
            owner = seeded
        else:
            seeded.admin = False
            seeded.email = "retired-" + secrets.token_hex(6) + "@mu3lab.invalid"
    session.commit()
    print("MU3LAB_MEALIE_OWNER_OK" if owner is not None and owner.admin else "MU3LAB_MEALIE_OWNER_MISSING")
"""
