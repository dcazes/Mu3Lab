"""Durable provider reconciliation and verification through FreeLLMAPI.

FreeLLMAPI is the source of truth for free models: it validates each key the
way that provider needs, ranks the provider's models, and tests them. Mu3Lab
keeps no model lists of its own, so nothing here goes stale when a provider
retires or adds a model.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.core_wiring import configure
from ctl.freellmapi_admin import GatewayAdmin, GatewayAdminError
from ctl.jobs import JobStore, redact
from ctl.provider_catalog import BY_ID, get, key_problem
from ctl.provider_secrets import delete as delete_secret
from ctl.provider_secrets import metadata, records, save
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env

SUPPORTED_ACTIONS = frozenset({"save", "verify", "enable", "disable", "remove"})
# How far down FreeLLMAPI's ranking to go: its catalog can briefly list a model
# the provider has just retired, and real chats fall through the same way.
MAX_MODEL_TESTS = 5
# FreeLLMAPI's key verdicts that mean the provider accepted the key.
KEY_ACCEPTED = frozenset({"healthy", "rate_limited"})
# Enough room for reasoning models, which think before they answer.
PROBE_MAX_TOKENS = 256


@dataclass(frozen=True)
class StreamProbe:
    success: bool
    http_status: int
    routed_via: str
    model: str
    error_code: str
    detail: str


def _probe_stream(
    model: str, key: str, *, gateway: str = "FreeLLMAPI", url: str = "http://127.0.0.1:3001/v1/chat/completions"
) -> StreamProbe:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
        "stream": True,
    }
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            routed = response.headers.get("X-Routed-Via", "")
            completed = False
            saw_delta = False
            received = 0
            while received < 1024 * 1024:
                line = response.readline()
                if not line:
                    break
                received += len(line)
                value = line.strip()
                if value == b"data: [DONE]":
                    completed = saw_delta
                    break
                if value.startswith(b"data:"):
                    try:
                        event = json.loads(value[5:].strip())
                        saw_delta = isinstance(event.get("choices"), list)
                    except (ValueError, AttributeError):
                        continue
            if not completed:
                return StreamProbe(
                    False,
                    response.status,
                    routed,
                    model,
                    "stream_failed",
                    f"{gateway} opened a stream but did not complete it.",
                )
            return StreamProbe(True, response.status, routed, model, "", "Stream completed.")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            code = "credential_rejected"
            detail = "The provider rejected authorization for this request."
        elif exc.code == 429:
            code = "rate_limited_or_quota"
            detail = "The provider is currently rate limited or out of quota."
        else:
            code = "stream_failed"
            detail = f"{gateway} returned HTTP {exc.code} during the streamed probe."
        return StreamProbe(False, exc.code, exc.headers.get("X-Routed-Via", ""), model, code, detail)
    except (OSError, urllib.error.URLError):
        return StreamProbe(
            False, 0, "", model, "gateway_unavailable", f"{gateway} was unavailable during provider verification."
        )


RENDERED_FILES = {
    "freellmapi": (".env", "freellmapi.config.json"),
    "litellm": (".env", "config.yaml"),
}


def _rendered(projects: Path, service_id: str) -> tuple[bytes | None, ...]:
    def read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    return tuple(read(projects / service_id / name) for name in RENDERED_FILES[service_id])


def _reconcile(root: Path, log) -> tuple[bool, str, list[str]]:
    paths = RuntimePaths()
    before = {service_id: _rendered(paths.projects, service_id) for service_id in RENDERED_FILES}
    try:
        wiring = configure(paths)
    except ValueError as exc:
        return False, str(exc), []
    for service_id in RENDERED_FILES:
        service = load().get(service_id)
        env_path = paths.projects / service_id / ".env"
        env = {"MU3LAB_DATA_ROOT": str(paths.data), "MU3LAB_ENV_FILE": str(env_path)}
        if service_id == "freellmapi":
            env["MU3LAB_FREELLMAPI_CONFIG"] = str(wiring["freellmapi_config"])
        else:
            env["MU3LAB_LITELLM_CONFIG"] = str(wiring["litellm_config"])
        # Both apps read their config only at startup, but Compose cannot see
        # changes inside a bind-mounted file; recreate only when it changed.
        changed = _rendered(paths.projects, service_id) != before[service_id]
        rc, output = actions.compose_up(service.compose_path(root), log, env=env, recreate=changed, wait_timeout=120)
        if rc:
            return False, f"{service.name} reconciliation failed: {redact(output)}", []
    service_key = read_runtime_env(paths.projects / "freellmapi" / ".env").get("FREELLMAPI_SERVICE_KEY", "")
    _forget_removed_keys(log)
    return (
        bool(service_key),
        ("Provider gateway reconciled." if service_key else "FreeLLMAPI service key is unavailable."),
        [],
    )


def _forget_removed_keys(log) -> None:
    """Delete gateway keys for providers removed in Mu3Lab.

    FreeLLMAPI's config import only adds and updates keys, so a provider
    missing from the file would otherwise keep routing on its old key.
    """
    saved = {str(item["id"]) for item in records()}
    try:
        admin = GatewayAdmin.sign_in()
        for key in admin.api_keys():
            platform = str(key.get("platform", ""))
            if platform in BY_ID and platform not in saved:
                admin.delete_key(int(key["id"]))
                log(f"Removed the {BY_ID[platform].name} key from FreeLLMAPI.")
    except (GatewayAdminError, KeyError, TypeError, ValueError) as exc:
        log(f"Could not tidy FreeLLMAPI keys: {exc}")


def _chat_route_check(log) -> StreamProbe:
    """Confirm chat's own path (LiteLLM's mu3lab-chat through FreeLLMAPI) streams."""
    lite_key = read_runtime_env(RuntimePaths().projects / "litellm" / ".env").get("LITELLM_MASTER_KEY", "")
    if not lite_key:
        return StreamProbe(
            False, 0, "", "", "litellm_unavailable", "LiteLLM master key is missing after reconciliation."
        )
    log("LiteLLM probe: mu3lab-chat")
    probe = _probe_stream("mu3lab-chat", lite_key, gateway="LiteLLM", url="http://127.0.0.1:4000/v1/chat/completions")
    log(f"LiteLLM probe result: HTTP {probe.http_status or 'unavailable'}, {probe.error_code or 'complete'}")
    return probe


def _verify(provider_id: str, root: Path, log) -> StreamProbe:
    provider = get(provider_id)
    saved = next((str(item["api_key"]) for item in records() if item["id"] == provider_id), "")
    if key_problem(saved):  # e.g. an empty or oversized record
        return StreamProbe(False, 0, "", "", "credential_rejected", key_problem(saved))
    ok, detail, _ = _reconcile(root, log)
    if not ok:
        return StreamProbe(False, 0, "", "", "gateway_unavailable", detail)
    try:
        admin = GatewayAdmin.sign_in()
        key = next((item for item in admin.api_keys() if item.get("platform") == provider_id), None)
        if key is None:
            return StreamProbe(
                False, 0, "", "", "gateway_unavailable", f"FreeLLMAPI did not load the {provider.name} key."
            )
        log(f"FreeLLMAPI key check: {provider.name}")
        verdict, reason = admin.check_key(int(key["id"]))
        log(f"FreeLLMAPI key check result: {verdict}")
        if verdict == "invalid":
            where = f" Copy the key again from {provider.keys_url} and paste it." if provider.keys_url else ""
            return StreamProbe(
                False,
                0,
                "",
                "",
                "credential_rejected",
                f"{provider.name} rejected this key: {reason or 'no reason given'}.{where}",
            )
        tested = ""
        last_error = f"FreeLLMAPI has no usable {provider.name} model right now."
        for model in admin.ranked_models(provider_id)[:MAX_MODEL_TESTS]:
            name = str(model.get("modelId", ""))
            log(f"FreeLLMAPI model test: {name}")
            passed, error = admin.test_model(int(model["id"]))
            log(f"FreeLLMAPI model test result: {'passed' if passed else redact(error) or 'failed'}")
            if passed:
                tested = name
                break
            last_error = error or last_error
    except (GatewayAdminError, KeyError, TypeError, ValueError) as exc:
        return StreamProbe(False, 0, "", "", "gateway_unavailable", str(exc))
    if not tested and verdict not in KEY_ACCEPTED:
        return StreamProbe(False, 0, "", "", "stream_failed", redact(last_error))
    chat = _chat_route_check(log)
    if not chat.success:
        return StreamProbe(False, chat.http_status, "", tested, "litellm_route_failed", chat.detail)
    if tested:
        return StreamProbe(
            True, 200, f"{provider_id}/{tested}", tested, "", f"{provider.name} answered with {tested}; chat is ready."
        )
    # FreeLLMAPI vouched for the key; its models were busy or just retired,
    # and real chats move on to the next model the same way.
    log(f"No {provider.name} model answered the test: {redact(last_error)}")
    return StreamProbe(
        True,
        200,
        "",
        "",
        "",
        f"{provider.name} accepted the key. Its models were busy during the test, "
        "so chat will start using them as soon as they are free.",
    )


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
        store.transition(
            job_id,
            "failed",
            actor=worker_id,
            detail="The worker rejected an unsupported provider operation.",
            error_code="unsupported_provider_action",
            step_id="validate",
        )
        return
    resolved_id = provider.id if provider else provider_id
    resolved_name = provider.name if provider else str((state.provider(provider_id) or {}).get("label") or provider_id)
    current = state.provider(resolved_id)
    label = str((current or {}).get("label") or resolved_name)

    def log(line: str) -> None:
        store.append_event(job_id, "log", line)

    if action == "remove":
        delete_secret(resolved_id)
        state.delete_provider(resolved_id)
        try:
            _reconcile(root, log)
        except (OSError, ValueError):
            pass
        store.transition(
            job_id, "succeeded", actor=actor, detail=f"{resolved_name} connection removed.", step_id="complete"
        )
        return
    assert provider is not None  # only removal accepts unknown legacy providers
    if action == "disable":
        state.set_provider(provider.id, label, enabled=False, state="disabled", job_id=job_id)
        ok, detail, _ = _reconcile(root, log)
        store.transition(
            job_id,
            "succeeded" if ok else "failed",
            actor=actor,
            detail=detail,
            error_code="" if ok else "provider_reconcile_failed",
            step_id="complete" if ok else "reconcile",
        )
        return
    if action == "enable":
        action = "verify"
    state.set_provider(provider.id, label, enabled=True, state="verifying", attempted=True, job_id=job_id)
    probe = _verify(provider.id, root, log)
    if probe.success:
        state.set_provider(
            provider.id,
            label,
            enabled=True,
            state="verified",
            models=[probe.model] if probe.model else [],
            attempted=True,
            verified=True,
            job_id="",
        )
        _reconcile(root, log)
        provisioning = __import__("ctl.provisioning", fromlist=["ProvisioningStore"]).ProvisioningStore.runtime()
        if provisioning:
            provisioning.update("configuration", "verified", detail=f"{provider.name} streamed routing passed.")
        store.transition(job_id, "succeeded", actor=actor, detail=probe.detail, step_id="complete")
        core_jobs = [item for item in store.jobs(limit=100) if item.get("service_id") == "core-suite"]
        waiting_core = next((item for item in core_jobs if item.get("state") == "waiting_for_confirmation"), None)
        if waiting_core:
            store.transition(
                str(waiting_core["id"]),
                "cancelled",
                actor=actor,
                detail="Superseded by verification-only checks after provider setup.",
            )
        if not any(item.get("state") in {"queued", "running"} for item in core_jobs):
            store.create(
                kind="verification",
                service_id="core-suite",
                action="verify",
                actor=actor,
                detail="Verify live platform contracts after provider setup.",
                idempotency_key=f"provider-verified:{provider.id}:{job_id}",
            )
    else:
        recommendations = {
            "credential_rejected": "Copy a fresh key from the provider and paste it again.",
            "rate_limited_or_quota": "Check provider quota or wait for its rate limit to reset, then verify again.",
            "gateway_unavailable": "Confirm FreeLLMAPI is healthy, then verify again.",
            "litellm_unavailable": "Confirm LiteLLM is healthy and its runtime credential is configured.",
            "litellm_route_failed": "Check the LiteLLM job logs and its FreeLLMAPI route, then verify again.",
            "catalog_mismatch": "Review the provider's suggested models or update the curated catalog.",
            "provider_route_mismatch": "Disable competing routes for this model, then verify this provider again.",
            "stream_failed": "Open the verification job details, then retry the streamed check.",
        }
        recommendation = recommendations.get(probe.error_code, "Review the verification details and try again.")
        error = {
            "code": probe.error_code,
            "message": redact(probe.detail),
            "recommended_action": recommendation,
            "routed_via": probe.routed_via,
            "model": probe.model,
            "http_status": probe.http_status,
        }
        state.set_provider(
            provider.id,
            label,
            enabled=True,
            state="degraded",
            models=[],
            error=error,
            attempted=True,
            job_id=job_id,
            replace_models=True,
        )
        # Re-render without the degraded credential so it cannot remain routable.
        try:
            _reconcile(root, log)
        except (OSError, ValueError):
            pass
        store.transition(
            job_id, "failed", actor=actor, detail=probe.detail, error_code=probe.error_code, step_id="verify"
        )


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
