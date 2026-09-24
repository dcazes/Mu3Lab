"""Curated core-suite reconciliation and verification.

The executor accepts no user-supplied Compose path or command. It materializes
private configuration, starts reviewed services in dependency order, prepares
the local embedding model, and records exactly which user-input boundary is
still pending rather than claiming a half-configured platform is ready.
"""

from __future__ import annotations

import time
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from ctl import actions
from ctl.jobs import JobStore, redact
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError, load
from ctl.runtime import RuntimePaths
from ctl.secrets import ensure_core_envs
from ctl.service_state import status as service_status, tailnet_dns_name
from ctl.core_wiring import EMBEDDING_MODEL, configure as configure_wiring
from ctl.secrets import read_runtime_env

CORE_ORDER = ("ollama", "freellmapi", "litellm", "lobehub")
HEALTH_TIMEOUT_SECONDS = 120
MODEL_TIMEOUT_SECONDS = 600


def capacity() -> dict:
    """Return a conservative, non-mutating admission result for the core suite."""
    import os
    import platform
    import shutil
    import subprocess

    root = RuntimePaths().root
    target = root if root.exists() else root.parent
    disk = shutil.disk_usage(target)
    memory_total = 0
    try:
        memory_total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        pass
    try:
        docker_ready = subprocess.run(["docker", "info"], capture_output=True,
                                      timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        docker_ready = False
    reasons: list[str] = []
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        reasons.append("the first supported release requires x86-64")
    if disk.free < 20 * 1024**3:
        reasons.append("at least 20 GiB free disk is required for the supported AI slice")
    if memory_total and memory_total < 8 * 1024**3:
        reasons.append("at least 8 GiB memory is required for the supported AI slice")
    if not docker_ready:
        reasons.append("Docker is not ready")
    return {"ok": not reasons, "reasons": reasons, "disk_free": disk.free,
            "memory_total": memory_total, "docker_ready": docker_ready}


def plan(root: Path) -> dict:
    """Return a safe, deterministic plan without mutating the host."""
    try:
        registry = load()
    except RegistryError as exc:
        return {"ready": False, "error": str(exc), "services": [], "missing": list(CORE_ORDER)}
    known = {service.id: service for service in registry.services}
    missing = [service_id for service_id in CORE_ORDER
               if service_id not in known or not (known[service_id].compose_path(root) / "docker-compose.yml").is_file()]
    admission = capacity()
    ready = not missing and admission["ok"]
    error = "" if ready else ("Core manifests are not ready for execution." if missing
                               else "; ".join(admission["reasons"]))
    return {"ready": ready, "error": error, "services": list(CORE_ORDER),
            "missing": missing, "capacity": admission}


def _http_ok(url: str, *, headers: dict[str, str] | None = None) -> bool:
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def _http_json(url: str, method: str = "GET", payload: dict | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """Call a fixed local integration endpoint without logging its payload."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, method=method, data=data,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}),
                 **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return exc.code, {}
    except (OSError, urllib.error.URLError, ValueError):
        return 0, {}


def _stream_chat_ok(url: str, payload: dict, headers: dict[str, str]) -> bool:
    """Require at least one OpenAI-compatible SSE delta and a clean terminator."""
    request = urllib.request.Request(
        url, method="POST", data=json.dumps({**payload, "stream": True}).encode("utf-8"),
        headers={"Accept": "text/event-stream", "Content-Type": "application/json", **headers},
    )
    saw_delta = False
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                return False
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    return saw_delta
                try:
                    event = json.loads(value)
                except ValueError:
                    continue
                if isinstance(event.get("choices"), list):
                    saw_delta = True
    except (OSError, urllib.error.URLError):
        return False
    return False


def _provision_freellmapi(runtime: RuntimePaths) -> tuple[bool, str]:
    """Create/reuse an internal scoped client credential through FreeLLMAPI.

    FreeLLMAPI intentionally has a separate single-user dashboard. Mu3Lab
    never exposes it: this local-only bootstrap account is used solely to mint
    the LiteLLM client-profile key, and provider keys remain declaratively
    applied from the root-only Mu3Lab configuration file.
    """
    from ctl.secrets import read_runtime_env, runtime_env_text

    env_path = runtime.projects / "freellmapi" / ".env"
    values = read_runtime_env(env_path)
    existing = values.get("FREELLMAPI_SERVICE_KEY", "")
    if existing.startswith("sk-cp-"):
        return True, "FreeLLMAPI internal client credential already exists."
    password = values.get("FREELLMAPI_ADMIN_PASSWORD", "")
    if not password:
        return False, "FreeLLMAPI internal credential was not initialized"
    email = "mu3lab-gateway@localhost.test"
    status, response = _http_json("http://127.0.0.1:3001/api/auth/setup", "POST",
                                  {"email": email, "password": password})
    token = str(response.get("token", "")) if status in {200, 201} else ""
    if not token and status == 403:
        rc, local = actions.freellmapi_local_setup(email, password, lambda _line: None)
        body = local.get("body", {}) if isinstance(local, dict) else {}
        token = str(body.get("token", "")) if rc == 0 and local.get("status") == 201 else ""
    if not token:
        status, response = _http_json("http://127.0.0.1:3001/api/auth/login", "POST",
                                      {"email": email, "password": password})
        token = str(response.get("token", "")) if status == 200 else ""
    if not token:
        return False, "FreeLLMAPI did not accept its internal bootstrap account"
    headers = {"Authorization": f"Bearer {token}"}
    status, profiles = _http_json("http://127.0.0.1:3001/api/client-profiles", headers=headers)
    if status != 200 or not isinstance(profiles, list):
        return False, "FreeLLMAPI client-profile API did not answer"
    # Profile keys are intentionally revealed only once by the upstream API.
    # If a previous partial run created one but lost its secret, fail safely
    # instead of silently creating unbounded credentials.
    if any(item.get("name") == "Mu3Lab LiteLLM" for item in profiles if isinstance(item, dict)):
        return False, "FreeLLMAPI has an incomplete Mu3Lab client credential; use the safe repair action"
    status, response = _http_json("http://127.0.0.1:3001/api/client-profiles", "POST",
                                  {"name": "Mu3Lab LiteLLM"}, headers=headers)
    service_key = str(response.get("key", "")) if status == 201 else ""
    if not service_key.startswith("sk-cp-"):
        return False, "FreeLLMAPI did not return a scoped internal client credential"
    values["FREELLMAPI_SERVICE_KEY"] = service_key
    temporary = env_path.with_suffix(".env.tmp")
    temporary.write_text(runtime_env_text(values), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(env_path)
    env_path.chmod(0o600)
    return True, "FreeLLMAPI internal client credential created."


def _runtime_envs(env_files: dict[str, Path], wiring: dict) -> dict[str, dict[str, str]]:
    """Return only service-owned runtime variables; values never enter jobs."""
    common = {"MU3LAB_DATA_ROOT": str(RuntimePaths().data)}
    envs = {service_id: {**common, "MU3LAB_ENV_FILE": str(path)}
            for service_id, path in env_files.items()}
    envs["litellm"]["MU3LAB_LITELLM_CONFIG"] = str(wiring["litellm_config"])
    envs["freellmapi"]["MU3LAB_FREELLMAPI_CONFIG"] = str(wiring["freellmapi_config"])
    return envs


def _configure_chat_routes(root: Path, log: Callable[[str], None]) -> None:
    """Publish LobeChat and the other core UI routes through private HTTPS."""
    from ctl.routes import reconcile_core
    registry = load()
    routed, detail = reconcile_core(registry, root, log)
    if not routed:
        raise ValueError("Core private UI routes could not be reconciled: " + redact(detail))


def _verify_platform(runtime: RuntimePaths, wiring: dict) -> tuple[bool, str]:
    """Check application-level contracts after container health has passed."""
    litellm_env = read_runtime_env(runtime.projects / "litellm" / ".env")
    key = litellm_env.get("LITELLM_MASTER_KEY", "")
    if not key or not _http_ok("http://127.0.0.1:4000/v1/models",
                               headers={"Authorization": f"Bearer {key}"}):
        return False, "LiteLLM did not expose its authenticated model registry"
    if not _http_ok("http://127.0.0.1:11434/api/tags"):
        return False, "Ollama model registry did not answer"
    if not _http_ok("http://127.0.0.1:3211/__mu3lab_lobehub_health"):
        return False, "LobeChat did not answer its private health endpoint"
    lobehub = read_runtime_env(runtime.projects / "lobehub" / ".env")
    if not all(lobehub.get(key) for key in ("AUTH_AUTHENTIK_ID", "AUTH_AUTHENTIK_SECRET", "APP_URL")):
        return False, "LobeChat's Authentik sign-in configuration is incomplete"
    if not wiring["chat_configured"]:
        return False, "Waiting for at least one external inference-provider key"
    if not _http_ok("http://127.0.0.1:3001/readyz"):
        return False, "FreeLLMAPI has no currently usable configured provider"
    headers = {"Authorization": f"Bearer {key}"}
    chat_payload = {
        "model": "mu3lab-chat", "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 4, "temperature": 0,
    }
    if not _stream_chat_ok("http://127.0.0.1:4000/v1/chat/completions", chat_payload, headers):
        return False, "A streamed FreeLLMAPI chat request did not complete through LiteLLM"
    embedding_status, embedding = _http_json("http://127.0.0.1:4000/v1/embeddings", "POST", {
        "model": "mu3lab-embed", "input": "Mu3Lab readiness check",
    }, headers=headers)
    vectors = embedding.get("data") if isinstance(embedding, dict) else None
    vector = vectors[0].get("embedding") if isinstance(vectors, list) and vectors and isinstance(vectors[0], dict) else None
    if (embedding_status != 200 or not isinstance(vector, list)
            or len(vector) != 768 or not all(isinstance(value, (int, float)) for value in vector)):
        return False, "The local embedding model did not return vectors through LiteLLM"
    host = tailnet_dns_name()
    if not host:
        return False, "The private hostname disappeared before LobeChat route verification"
    from ctl.service_state import tailnet_serve_ports
    if 8457 not in tailnet_serve_ports():
        return False, "LobeChat's private Tailscale route is not published"
    return True, "Private LobeChat route, Authentik sign-in configuration, streamed chat, and embeddings passed live checks."


def _run(store: JobStore, job_id: str, actor: str, root: Path,
         logger: Callable[[str], None] | None = None,
         worker_id: str = "") -> None:
    """Run the reviewed sequence after a durable worker has claimed the job."""
    log = logger or (lambda line: store.append_event(job_id, "log", line))
    provisioning = ProvisioningStore.runtime()
    try:
        if provisioning:
            provisioning.update("core", "running", detail="Reconciling the curated core suite.")
        store.transition(job_id, "running", actor=actor, detail="Core suite execution started.")
        checked = plan(root)
        if not checked["ready"]:
            store.transition(job_id, "failed", actor=actor,
                             detail="Core suite blocked: " + ", ".join(checked["missing"]))
            return
        runtime = RuntimePaths()
        env_files = ensure_core_envs(runtime.root)
        from ctl.service_ops import _materialize
        lobehub_project = _materialize(load().get("lobehub"), root)
        env_files["lobehub"] = lobehub_project / ".env"
        _configure_chat_routes(root, log)
        wiring = configure_wiring(runtime)
        envs = _runtime_envs(env_files, wiring)
        for service_id in CORE_ORDER:
            if worker_id and not store.heartbeat(job_id, worker_id, step_id=service_id):
                raise RuntimeError("job lease was lost")
            store.append_event(job_id, "step.started", service_id)
            service = load().get(service_id)
            project = lobehub_project if service_id == "lobehub" else service.compose_path(root)
            from ctl.compute import compose_overrides
            rc, output = actions.compose_up(
                project, log, env=envs[service_id],
                extra_files=compose_overrides(service_id, project),
                recreate=service_id == "freellmapi" and bool(wiring["provider_count"]),
                timeout=1800, on_output=lambda _line: None)
            safe_output = redact(output or f"exit {rc}")
            if rc != 0:
                store.transition(job_id, "failed", actor=actor,
                                 detail=f"Stopped at {service_id}: {safe_output}")
                return
            deadline = time.monotonic() + (900 if service_id == "lobehub" else HEALTH_TIMEOUT_SECONDS)
            while time.monotonic() < deadline:
                live = service_status(service, "", root)
                if live["health_state"] == "healthy":
                    break
                time.sleep(2)
            else:
                live = service_status(service, "", root)
                store.transition(job_id, "failed", actor=actor,
                                 detail=f"Stopped at {service_id}: health check did not pass ({redact(live['detail'])}).")
                return
            log(f"{service_id}: compose start completed")
            store.append_event(job_id, "step.verified", service_id)
            if service_id == "lobehub":
                from ctl.lobehub_ops import reconcile
                policy_ready, policy_detail = reconcile(log)
                if not policy_ready:
                    store.transition(job_id, "failed", actor=actor,
                                     detail="LobeChat model and agent policy failed: " + redact(policy_detail),
                                     error_code="lobehub_policy_failed", step_id="lobehub_policy")
                    return
            if service_id == "ollama":
                rc, output = actions.compose_exec(
                    project, "ollama", ["ollama", "pull", EMBEDDING_MODEL], log,
                    timeout=MODEL_TIMEOUT_SECONDS, env=envs[service_id])
                if rc != 0:
                    store.transition(job_id, "failed", actor=actor,
                                     detail="Ollama embedding model preparation failed: " + redact(output))
                    if provisioning:
                        provisioning.update("core", "failed", error="Embedding model preparation failed.")
                    return
            if service_id == "freellmapi":
                provisioned, detail = _provision_freellmapi(runtime)
                if not provisioned:
                    store.transition(job_id, "failed", actor=actor, detail=detail)
                    if provisioning:
                        provisioning.update("core", "failed", error=detail)
                    return
                log(detail)
                wiring = configure_wiring(runtime)
                envs = _runtime_envs(env_files, wiring)
        verified, detail = _verify_platform(runtime, wiring)
        if not verified:
            state = "waiting_for_confirmation" if not wiring["chat_configured"] else "failed"
            store.transition(job_id, state, actor=actor, detail=detail)
            if provisioning:
                if state == "waiting_for_confirmation":
                    provisioning.update("core", "verified",
                                        detail="Core containers and the private LobeChat route are healthy.")
                    provisioning.update("configuration", "waiting_for_user", detail=detail)
                else:
                    provisioning.update("core", "failed", detail=detail, error=detail)
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services and private integration checks passed.")
            provisioning.update("verification", "verified",
                                detail="Private LobeChat route and OIDC configuration, streamed chat, and embeddings passed.")
        store.transition(job_id, "succeeded", actor=actor,
                         detail="Core suite started, wired, and passed application-level checks.")
    except Exception as exc:  # noqa: BLE001 - job state must become terminal
        stage = service_id if "service_id" in locals() else "prepare"
        store.transition(job_id, "failed", actor=actor,
                         detail=f"Core setup failed during {stage}: {redact(str(exc))}",
                         error_code="core_executor_failure", step_id=stage)
        if provisioning:
            provisioning.update("core", "failed", error="Core executor failed safely.")


def start(store: JobStore, actor: str, root: Path,
          idempotency_key: str | None = None) -> dict[str, str]:
    """Queue core reconciliation; the persistent worker owns execution."""
    del root  # retained in the public call shape while release layout is migrated
    return store.create(kind="lifecycle", service_id="core-suite", action="install",
                        actor=actor, detail="Reconcile the reviewed core platform",
                        idempotency_key=idempotency_key)


def start_verify(store: JobStore, actor: str,
                 idempotency_key: str | None = None) -> dict[str, str]:
    """Queue contract verification without pulling or recreating services."""
    return store.create(kind="verification", service_id="core-suite", action="verify",
                        actor=actor, detail="Verify live core platform contracts",
                        idempotency_key=idempotency_key)


def _run_verify(store: JobStore, job_id: str, actor: str) -> None:
    provisioning = ProvisioningStore.runtime()
    store.transition(job_id, "running", actor=actor,
                     detail="Live platform verification started.", step_id="verify")
    try:
        runtime = RuntimePaths()
        wiring = configure_wiring(runtime)
        ok, detail = _verify_platform(runtime, wiring)
        if not ok:
            if provisioning:
                provisioning.update("verification", "failed", detail=detail, error=detail)
            store.transition(job_id, "failed", actor=actor, detail=detail,
                             error_code="platform_verification_failed", step_id="verify")
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services remain configured and reachable.")
            provisioning.update("verification", "verified", detail=detail)
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
    except Exception as exc:  # noqa: BLE001 - durable jobs must terminate safely
        detail = "Platform verification failed safely: " + redact(str(exc))
        if provisioning:
            provisioning.update("verification", "failed", error=detail)
        store.transition(job_id, "failed", actor=actor, detail=detail,
                         error_code="platform_verification_failed", step_id="verify")


def execute_claimed(store: JobStore, job: dict, worker_id: str,
                    root: Path) -> None:
    """Dispatch one already-claimed, allowlisted core job."""
    if job.get("service_id") != "core-suite" or job.get("action") not in {"install", "verify"}:
        store.transition(str(job["id"]), "failed", actor=worker_id,
                         detail="The worker rejected an unsupported job.",
                         error_code="unsupported_job")
        return
    if job.get("action") == "verify":
        _run_verify(store, str(job["id"]), str(job.get("actor") or "system"))
        return
    _run(store, str(job["id"]), str(job.get("actor") or "system"), root,
         worker_id=worker_id)
