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
