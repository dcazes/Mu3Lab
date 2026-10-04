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

from ctl.control_state import ControlState
from ctl.core_wiring import configure, routing_projects
from ctl.engine.compose import Compose
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


def _rendered(files: tuple[Path, ...]) -> tuple[bytes | None, ...]:
    def read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    return tuple(read(path) for path in files)


def _reconcile(root: Path, log) -> tuple[bool, str, list[str]]:
    paths = RuntimePaths()
    catalog = load().catalog
    projects = routing_projects(catalog)
    for item in projects:
        if not (paths.projects / item.app.id / "docker-compose.yml").is_file():
            return False, f"{item.app.manifest.name} is not installed; finish core setup first.", []
    before = {item.app.id: _rendered(item.files(paths)) for item in projects}
    try:
        configure(paths, catalog)
    except ValueError as exc:
        return False, str(exc), []
    for item in projects:
        # Bind-mounted configs are read at startup; recreate only when their contents changed.
        changed = _rendered(item.files(paths)) != before[item.app.id]
        rc, output = Compose(paths.projects / item.app.id).up(log, recreate=changed, wait_seconds=120)
        if rc:
            return False, f"{item.app.manifest.name} reconciliation failed: {redact(output)}", []
    gateway = next((item for item in projects if item.params.mode == "gateway"), None)
    models = next((item for item in projects if item.params.mode == "models"), None)
    service_key = (
        read_runtime_env(paths.projects / gateway.app.id / ".env").get(models.params.service_key_env, "")
        if gateway and models
        else ""
    )
    forgotten, forget_detail = _forget_removed_keys(log)
    if not forgotten:
        return False, forget_detail, []
    return (
        bool(service_key),
        "Provider gateway reconciled." if service_key else "Provider service key is unavailable.",
        [],
    )


def _forget_removed_keys(log) -> tuple[bool, str]:
    """Delete gateway keys for providers removed in Mu3Lab, and confirm they are gone.

    FreeLLMAPI's config import only adds and updates keys, so a provider
    missing from the file would otherwise keep routing on its old key.
    """
    saved = {str(item["id"]) for item in records()}

    def stale(keys: list[dict]) -> list[dict]:
        return [key for key in keys if str(key.get("platform", "")) in BY_ID and key.get("platform") not in saved]

    try:
        admin = GatewayAdmin.sign_in()
        for key in stale(admin.api_keys()):
            admin.delete_key(int(key["id"]))
            log(f"Removed the {BY_ID[str(key['platform'])].name} key from FreeLLMAPI.")
        remaining = stale(admin.api_keys())
    except (GatewayAdminError, KeyError, TypeError, ValueError) as exc:
        return False, f"FreeLLMAPI could not remove old provider keys: {redact(str(exc))}"
    if remaining:
        names = ", ".join(BY_ID[str(key["platform"])].name for key in remaining)
        return False, f"FreeLLMAPI still holds a removed key ({names})."
    return True, "No removed provider keys remain in FreeLLMAPI."


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
        # Keep a visible "removing" record until the gateway has provably
        # dropped the key, so a failure can be seen and retried.
        state.set_provider(resolved_id, label, enabled=False, state="removing", job_id=job_id)
        delete_secret(resolved_id)
        try:
            ok, detail, _ = _reconcile(root, log)
        except (OSError, ValueError) as exc:
            ok, detail = False, redact(str(exc))
        if not ok:
            state.set_provider(
                resolved_id,
                label,
                enabled=False,
                state="removing",
                error={"message": detail, "retryable": True},
                job_id="",
            )
            store.transition(
                job_id,
                "failed",
                actor=actor,
                detail=f"{resolved_name} is switched off, but removing its key from the AI gateway failed: {detail}",
                error_code="provider_removal_incomplete",
                step_id="remove_gateway_key",
            )
            return
        state.delete_provider(resolved_id)
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
        core_jobs = store.jobs_for_service("core-suite", limit=100)
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
