"""Durable provider reconciliation and live-route verification."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
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


@dataclass(frozen=True)
class StreamProbe:
    success: bool
    http_status: int
    routed_via: str
    model: str
    error_code: str
    detail: str


class ModelCatalogUnavailable(RuntimeError):
    """The local FreeLLMAPI catalog could not be read."""


def _available_models(key: str) -> list[str]:
    status, payload = _request_json("http://127.0.0.1:3001/v1/models", key)
    if status != 200:
        raise ModelCatalogUnavailable(f"FreeLLMAPI model discovery returned HTTP {status or 'unavailable'}.")
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    result: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("available") is False:
            continue
        model_id = str(item.get("id", ""))
        if model_id and model_id not in result:
            result.append(model_id)
    return result


def _canonical_model_id(value: str) -> str:
    """Compare FreeLLMAPI's normalized IDs with curated provider aliases.

    The gateway intentionally reports every model as owned by `freellmapi`.
    Provider ownership therefore cannot establish whether a stored key works;
    the streamed route header is the authoritative check.  This helper only
    finds sensible probe candidates, preserving the curated order.
    """
    normalized = value.strip().lower()
    for prefix in ("openai/", "qwen/", "meta-llama/", "meta/", "nvidia/", "groq/", "google/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized


def _probe_candidates(provider_id: str, available: list[str]) -> list[str]:
    """Return actual gateway IDs in curated probe order, without duplicates."""
    exact = set(available)
    normalized: dict[str, list[str]] = {}
    for model in available:
        normalized.setdefault(_canonical_model_id(model), []).append(model)
    result: list[str] = []
    for probe in get(provider_id).probe_models:
        candidates = [probe] if probe in exact else normalized.get(_canonical_model_id(probe), [])
        for candidate in candidates:
            if candidate not in result:
                result.append(candidate)
    return result


def _probe_stream(model: str, key: str, *, gateway: str = "FreeLLMAPI",
                  url: str = "http://127.0.0.1:3001/v1/chat/completions") -> StreamProbe:
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
               "max_tokens": 4, "temperature": 0, "stream": True}
    request = urllib.request.Request(
        url, method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "text/event-stream", "Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
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
                return StreamProbe(False, response.status, routed, model, "stream_failed",
                                   f"{gateway} opened a stream but did not complete it.")
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
        return StreamProbe(False, 0, "", model, "gateway_unavailable",
                           f"{gateway} was unavailable during provider verification.")


def _groq_access_diagnostic() -> tuple[int, str]:
    """Use Groq's read-only models endpoint to explain opaque gateway 502s."""
    key = next((item["api_key"] for item in records() if item["id"] == "groq"), "")
    if not key:
        return 0, "No saved Groq credential was found."
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, "Groq accepted the key for model discovery."
    except urllib.error.HTTPError as exc:
        return exc.code, f"Groq's models endpoint returned HTTP {exc.code}."
    except (OSError, urllib.error.URLError):
        return 0, "Groq's models endpoint was unreachable."


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


def _verify(provider_id: str, root: Path, log) -> StreamProbe:
    ok, detail, _ = _reconcile(root, log)
    if not ok:
        return StreamProbe(False, 0, "", "", "gateway_unavailable", detail)
    key = read_runtime_env(RuntimePaths().projects / "freellmapi" / ".env").get("FREELLMAPI_SERVICE_KEY", "")
    try:
        available = set(_available_models(key))
    except ModelCatalogUnavailable as exc:
        return StreamProbe(False, 0, "", "", "gateway_unavailable", str(exc))
    candidates = _probe_candidates(provider_id, list(available))
    if not candidates:
        return StreamProbe(False, 200, "", "", "catalog_mismatch",
                           "None of Mu3Lab's curated probe models are present in this FreeLLMAPI catalog.")
    last = StreamProbe(False, 0, "", "", "stream_failed", "No provider probe completed.")
    for model in candidates:
        log(f"FreeLLMAPI probe: {model}")
        probe = _probe_stream(model, key)
        log(f"FreeLLMAPI probe result: HTTP {probe.http_status or 'unavailable'}, "
            f"route {probe.routed_via or 'unreported'}, {probe.error_code or 'complete'}")
        last = probe
        if not probe.success:
            if probe.error_code in {"credential_rejected", "rate_limited_or_quota", "gateway_unavailable"}:
                return probe
            continue
        routed_provider = probe.routed_via.split("/", 1)[0].strip().lower()
        if routed_provider == provider_id:
            lite_key = read_runtime_env(RuntimePaths().projects / "litellm" / ".env").get("LITELLM_MASTER_KEY", "")
            if not lite_key:
                return StreamProbe(False, 0, probe.routed_via, model, "litellm_unavailable",
                                   "LiteLLM master key is missing after reconciliation.")
            log("LiteLLM probe: mu3lab-chat")
            end_to_end = _probe_stream("mu3lab-chat", lite_key, gateway="LiteLLM",
                                        url="http://127.0.0.1:4000/v1/chat/completions")
            log(f"LiteLLM probe result: HTTP {end_to_end.http_status or 'unavailable'}, "
                f"{end_to_end.error_code or 'complete'}")
            if not end_to_end.success:
                return StreamProbe(False, end_to_end.http_status, probe.routed_via, model,
                                   "litellm_route_failed", end_to_end.detail)
            if end_to_end.routed_via and end_to_end.routed_via.split("/", 1)[0].strip().lower() != provider_id:
                return StreamProbe(False, end_to_end.http_status, end_to_end.routed_via, model,
                                   "provider_route_mismatch",
                                   f"LiteLLM completed through {end_to_end.routed_via}, not {provider_id}.")
            return StreamProbe(True, end_to_end.http_status, probe.routed_via, model, "",
                               f"{provider_id} routed via {probe.routed_via}; LiteLLM streamed mu3lab-chat successfully."
                               + (" LiteLLM did not expose upstream provider attribution." if not end_to_end.routed_via else ""))
        last = StreamProbe(False, probe.http_status, probe.routed_via, model,
                           "provider_route_mismatch",
                           f"The test completed through {probe.routed_via or 'an unidentified provider'}, not {provider_id}.")
    if provider_id == "groq" and not last.routed_via and last.error_code == "stream_failed":
        status, diagnostic = _groq_access_diagnostic()
        log(diagnostic)
        if status in {401, 403}:
            return StreamProbe(False, status, "", last.model, "upstream_access_denied",
                               diagnostic + " Check the Groq key and account access.")
        if status == 429:
            return StreamProbe(False, status, "", last.model, "rate_limited_or_quota",
                               diagnostic + " Check Groq quota or rate limits.")
    return last


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
    probe = _verify(provider.id, root, log)
    if probe.success:
        state.set_provider(provider.id, label, enabled=True, state="verified", models=[probe.model],
                           attempted=True, verified=True, job_id="")
        _reconcile(root, log)
        provisioning = __import__("ctl.provisioning", fromlist=["ProvisioningStore"]).ProvisioningStore.runtime()
        if provisioning:
            provisioning.update("configuration", "verified", detail=f"{provider.name} streamed routing passed.")
        store.transition(job_id, "succeeded", actor=actor, detail=probe.detail, step_id="complete")
        core_jobs = [item for item in store.jobs(limit=100)
                     if item.get("service_id") == "core-suite"]
        waiting_core = next((item for item in core_jobs
                             if item.get("state") == "waiting_for_confirmation"), None)
        if waiting_core:
            store.transition(str(waiting_core["id"]), "cancelled", actor=actor,
                             detail="Superseded by verification-only checks after provider setup.")
        if not any(item.get("state") in {"queued", "running"} for item in core_jobs):
            store.create(kind="verification", service_id="core-suite", action="verify", actor=actor,
                         detail="Verify live platform contracts after provider setup.",
                         idempotency_key=f"provider-verified:{provider.id}:{job_id}")
    else:
        recommendations = {
            "credential_rejected": "Replace the rejected key, then verify again.",
            "upstream_access_denied": "Check the Groq credential and account access, then verify again.",
            "rate_limited_or_quota": "Check provider quota or wait for its rate limit to reset, then verify again.",
            "gateway_unavailable": "Confirm FreeLLMAPI is healthy, then verify again.",
            "litellm_unavailable": "Confirm LiteLLM is healthy and its runtime credential is configured.",
            "litellm_route_failed": "Check the LiteLLM job logs and its FreeLLMAPI route, then verify again.",
            "catalog_mismatch": "Review the provider's suggested models or update the curated catalog.",
            "provider_route_mismatch": "Disable competing routes for this model, then verify this provider again.",
            "stream_failed": "Open the verification job details, then retry the streamed check.",
        }
        recommendation = recommendations.get(probe.error_code, "Review the verification details and try again.")
        error = {"code": probe.error_code, "message": redact(probe.detail),
                 "recommended_action": recommendation, "routed_via": probe.routed_via,
                 "model": probe.model, "http_status": probe.http_status}
        state.set_provider(provider.id, label, enabled=True, state="degraded", models=[],
                           error=error, attempted=True, job_id=job_id, replace_models=True)
        # Re-render without the degraded credential so it cannot remain routable.
        try:
            _reconcile(root, log)
        except (OSError, ValueError):
            pass
        store.transition(job_id, "failed", actor=actor, detail=probe.detail,
                         error_code=probe.error_code, step_id="verify")


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
