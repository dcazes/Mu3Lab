"""Durable provider reconciliation and live-route verification."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.core_setup import _stream_chat_ok
from ctl.core_wiring import configure
from ctl.jobs import JobStore, redact
from ctl.provider_catalog import get
from ctl.provider_secrets import delete as delete_secret
from ctl.provider_secrets import metadata, records, save
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

SUPPORTED_ACTIONS = frozenset({"save", "verify", "enable", "disable", "remove"})


def _request_json(url: str, key: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, ValueError, urllib.error.URLError):
        return 0, {}


def _samples(provider_id: str, key: str) -> list[str]:
    status, payload = _request_json("http://127.0.0.1:3001/v1/models", key)
    if status != 200:
        return []
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    result: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        # FreeLLMAPI's OpenAI-compatible model response identifies an
        # ungrouped provider with `owned_by`; older/projected responses may
        # expose `platform` or `platforms`. Accept all reviewed shapes while
        # refusing unavailable catalog rows as verified access.
        platforms = item.get("platforms") or [item.get("platform") or item.get("owned_by")]
        if provider_id not in platforms:
            continue
        if item.get("available") is False:
            continue
        model_id = str(item.get("id", ""))
        if model_id and model_id not in result:
            result.append(model_id)
        if len(result) == 3:
            break
    return result


def _reconcile(root: Path, log) -> tuple[bool, str, list[str]]:
    paths = RuntimePaths()
    try:
        wiring = configure(paths)
    except ValueError as exc:
        return False, str(exc), []
    for service_id in ("freellmapi", "litellm"):
        service = load().get(service_id)
        env_path = paths.projects / service_id / ".env"
        env = {"MU3LAB_DATA_ROOT": str(paths.data), "MU3LAB_ENV_FILE": str(env_path)}
        if service_id == "freellmapi":
            env["MU3LAB_FREELLMAPI_CONFIG"] = str(wiring["freellmapi_config"])
        else:
            env["MU3LAB_LITELLM_CONFIG"] = str(wiring["litellm_config"])
        rc, output = actions.compose_up(service.compose_path(root), log, env=env, recreate=True, wait_timeout=120)
        if rc:
            return False, f"{service.name} reconciliation failed: {redact(output)}", []
    service_key = read_runtime_env(paths.projects / "freellmapi" / ".env").get("FREELLMAPI_SERVICE_KEY", "")
    return bool(service_key), ("Provider gateway reconciled." if service_key else "FreeLLMAPI service key is unavailable."), []


def _verify(provider_id: str, root: Path, log) -> tuple[bool, str, list[str]]:
    ok, detail, _ = _reconcile(root, log)
    if not ok:
        return False, detail, []
    key = read_runtime_env(RuntimePaths().projects / "freellmapi" / ".env").get("FREELLMAPI_SERVICE_KEY", "")
    models = _samples(provider_id, key)
    if not models:
        return False, "FreeLLMAPI did not report a usable model for this provider key.", []
    payload = {"model": models[0], "messages": [{"role": "user", "content": "Reply with OK."}],
               "max_tokens": 4, "temperature": 0}
    if not _stream_chat_ok("http://127.0.0.1:3001/v1/chat/completions", payload,
                           {"Authorization": f"Bearer {key}"}):
        return False, "The provider did not complete a streamed test request through FreeLLMAPI.", models
    return True, "Provider model discovery and streamed routing passed.", models


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    job_id = str(job["id"])
    provider_id = str(job.get("service_id") or "").removeprefix("provider:")
    action = str(job.get("action") or "")
    actor = str(job.get("actor") or worker_id)
    state = ControlState.runtime()
    try:
        provider = get(provider_id)
    except ValueError:
        provider = None
    if state is None or (provider is None and action != "remove") or action not in SUPPORTED_ACTIONS:
        store.transition(job_id, "failed", actor=worker_id, detail="The worker rejected an unsupported provider operation.",
                         error_code="unsupported_provider_action", step_id="validate")
        return
    resolved_id = provider.id if provider else provider_id
    resolved_name = provider.name if provider else str((state.provider(provider_id) or {}).get("label") or provider_id)
    current = state.provider(resolved_id)
    label = str((current or {}).get("label") or resolved_name)
    log = lambda line: store.append_event(job_id, "log", line)
    if action == "remove":
        delete_secret(resolved_id)
        state.delete_provider(resolved_id)
        try:
            _reconcile(root, log)
        except (OSError, ValueError):
            pass
        store.transition(job_id, "succeeded", actor=actor, detail=f"{resolved_name} connection removed.", step_id="complete")
        return
    if action == "disable":
        state.set_provider(provider.id, label, enabled=False, state="disabled", job_id=job_id)
        ok, detail, _ = _reconcile(root, log)
        store.transition(job_id, "succeeded" if ok else "failed", actor=actor, detail=detail,
                         error_code="" if ok else "provider_reconcile_failed", step_id="complete" if ok else "reconcile")
        return
    if action == "enable":
        action = "verify"
    state.set_provider(provider.id, label, enabled=True, state="verifying", attempted=True, job_id=job_id)
    ok, detail, models = _verify(provider.id, root, log)
    if ok:
        state.set_provider(provider.id, label, enabled=True, state="verified", models=models,
                           attempted=True, verified=True, job_id="")
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
        waiting_core = next((item for item in store.jobs(limit=100)
                             if item.get("service_id") == "core-suite"
                             and item.get("state") == "waiting_for_confirmation"), None)
        if waiting_core:
            store.retry(str(waiting_core["id"]), actor=actor,
                        idempotency_key=f"provider-verified:{provider.id}:{job_id}")
    else:
        error = {"code": "provider_verification_failed", "message": redact(detail),
                 "recommended_action": "Check the key, replace it if needed, then verify again."}
        state.set_provider(provider.id, label, enabled=True, state="degraded", models=models,
                           error=error, attempted=True, job_id="")
        # Re-render without the degraded credential so it cannot remain routable.
        try:
            _reconcile(root, log)
        except (OSError, ValueError):
            pass
        store.transition(job_id, "failed", actor=actor, detail=detail,
                         error_code="provider_verification_failed", step_id="verify")


def migrate_legacy(state: ControlState) -> None:
    """Project old encrypted records without deleting or exposing their keys."""
    from ctl.provider_catalog import BY_ID, canonical_id
    for item in metadata():
        raw_id = str(item["id"])
        provider_id = canonical_id(raw_id)
        if state.provider(provider_id):
            continue
        if provider_id in BY_ID:
            if raw_id != provider_id:
                private = next((record for record in records() if record["id"] == raw_id), None)
                if private:
                    save(provider_id, str(private["label"]), str(private["api_key"]))
                    delete_secret(raw_id)
            state.set_provider(provider_id, str(item["label"]), enabled=False, state="saved")
        else:
            state.set_provider(raw_id, str(item["label"]), enabled=False, state="unsupported_legacy")
