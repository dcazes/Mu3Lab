"""Curated core-suite reconciliation and verification.

The executor accepts no user-supplied Compose path or command. It materializes
private configuration, starts reviewed services in dependency order, prepares
the local embedding model, and records exactly which user-input boundary is
still pending rather than claiming a half-configured platform is ready.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.core_wiring import EMBEDDING_MODEL
from ctl.core_wiring import configure as configure_wiring
from ctl.identity import sync_sign_in
from ctl.integrations.authentik import Authentik
from ctl.jobs import JobStore, redact
from ctl.provisioning import ProvisioningStore
from ctl.registry import RegistryError, load
from ctl.runtime import RuntimePaths
from ctl.secrets import ensure_core_envs, read_runtime_env
from ctl.service_state import status as service_status
from ctl.service_state import tailnet_dns_name

CORE_ORDER = ("ollama", "freellmapi", "litellm", "lobehub")
# Core apps that need the full app installer (private route, image pinning,
# chat connector). The core job queues them once the AI suite is running.
INSTALLER_CORE_APPS = ("firecrawl",)
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
        docker_ready = (
            subprocess.run(actions.docker_argv(["docker", "info"]), capture_output=True, timeout=5).returncode == 0
        )
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
    return {
        "ok": not reasons,
        "reasons": reasons,
        "disk_free": disk.free,
        "memory_total": memory_total,
        "docker_ready": docker_ready,
    }


def plan(root: Path) -> dict:
    """Return a safe, deterministic plan without mutating the host."""
    try:
        registry = load()
    except RegistryError as exc:
        return {"ready": False, "error": str(exc), "services": [], "missing": list(CORE_ORDER)}
    known = {service.id: service for service in registry.services}
    missing = [
        service_id
        for service_id in CORE_ORDER
        if service_id not in known or not (known[service_id].compose_path(root) / "docker-compose.yml").is_file()
    ]
    admission = capacity()
    ready = not missing and admission["ok"]
    error = (
        ""
        if ready
        else ("missing app manifests for " + ", ".join(missing) if missing else "; ".join(admission["reasons"]))
    )
    return {"ready": ready, "error": error, "services": list(CORE_ORDER), "missing": missing, "capacity": admission}


def _http_ok(url: str, *, headers: dict[str, str] | None = None) -> bool:
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> tuple[int, dict]:
    """Call a fixed local integration endpoint without logging its payload."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
        url,
        method="POST",
        data=json.dumps({**payload, "stream": True}).encode("utf-8"),
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

    FreeLLMAPI intentionally has a separate single-user dashboard, published
    only behind the Authentik gate. This bootstrap account mints the LiteLLM
    client-profile key and doubles as the dashboard login the owner can save
    to Vaultwarden; provider keys remain declaratively applied from the
    root-only Mu3Lab configuration file.
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
    status, response = _http_json(
        "http://127.0.0.1:3001/api/auth/setup", "POST", {"email": email, "password": password}
    )
    token = str(response.get("token", "")) if status in {200, 201} else ""
    if not token and status == 403:
        rc, local = actions.freellmapi_local_setup(email, password, lambda _line: None)
        body = local.get("body", {}) if isinstance(local, dict) else {}
        token = str(body.get("token", "")) if rc == 0 and local.get("status") == 201 else ""
    if not token:
        status, response = _http_json(
            "http://127.0.0.1:3001/api/auth/login", "POST", {"email": email, "password": password}
        )
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
    status, response = _http_json(
        "http://127.0.0.1:3001/api/client-profiles", "POST", {"name": "Mu3Lab LiteLLM"}, headers=headers
    )
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
    envs = {service_id: {**common, "MU3LAB_ENV_FILE": str(path)} for service_id, path in env_files.items()}
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


EMBEDDING_ATTEMPTS = 4
EMBEDDING_TIMEOUT = 120


def _embedding_ok(headers: dict[str, str], *, sleep=time.sleep) -> bool:
    """Ask for one embedding, allowing for the model's first load into memory.

    The first request after Ollama starts loads the model, which can take well
    over a minute on a CPU; a short timeout reported a healthy install as failed.
    """
    for attempt in range(EMBEDDING_ATTEMPTS):
        status, embedding = _http_json(
            "http://127.0.0.1:4000/v1/embeddings",
            "POST",
            {"model": "mu3lab-embed", "input": "Mu3Lab readiness check"},
            headers=headers,
            timeout=EMBEDDING_TIMEOUT,
        )
        vectors = embedding.get("data") if isinstance(embedding, dict) else None
        first = vectors[0] if isinstance(vectors, list) and vectors and isinstance(vectors[0], dict) else {}
        vector = first.get("embedding")
        if (
            status == 200
            and isinstance(vector, list)
            and len(vector) == 768
            and all(isinstance(value, (int, float)) for value in vector)
        ):
            return True
        if attempt + 1 < EMBEDDING_ATTEMPTS:
            sleep(10)
    return False


def _verify_platform(runtime: RuntimePaths, wiring: dict) -> tuple[bool, str]:
    """Check application-level contracts after container health has passed."""
    litellm_env = read_runtime_env(runtime.projects / "litellm" / ".env")
    key = litellm_env.get("LITELLM_MASTER_KEY", "")
    if not key or not _http_ok("http://127.0.0.1:4000/v1/models", headers={"Authorization": f"Bearer {key}"}):
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
        "model": "mu3lab-chat",
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 4,
        "temperature": 0,
    }
    if not _stream_chat_ok("http://127.0.0.1:4000/v1/chat/completions", chat_payload, headers):
        return False, "A streamed FreeLLMAPI chat request did not complete through LiteLLM"
    if not _embedding_ok(headers):
        return False, "The local embedding model did not return vectors through LiteLLM"
    host = tailnet_dns_name()
    if not host:
        return False, "The private hostname disappeared before LobeChat route verification"
    from ctl.service_state import tailnet_serve_ports

    if 8457 not in tailnet_serve_ports():
        return False, "LobeChat's private Tailscale route is not published"
    return (
        True,
        "Private LobeChat route, streamed chat and embeddings passed live checks. Authentik sign-in is configured; "
        "it is confirmed the first time you sign in to LobeChat.",
    )


def _queue_installer_core_apps(store: JobStore, actor: str, root: Path, log: Callable[[str], None]) -> None:
    """Queue each installer-managed core app that is not installed yet, chat connector on."""
    from ctl.control_state import ControlState
    from ctl.mcp_ops import preenable

    control = ControlState.runtime()
    for service_id in INSTALLER_CORE_APPS:
        installation = control.installation(service_id) if control else None
        if installation and installation.get("state") in {"running", "stopped"}:
            continue
        preenable(service_id, root)
        job = store.create(
            kind="lifecycle",
            service_id=service_id,
            action="install",
            actor=actor,
            detail="Queued by core setup: this app is part of the core suite.",
        )
        log(f"{service_id}: installation queued (job {job['id']})")


def _run(
    store: JobStore,
    job_id: str,
    actor: str,
    root: Path,
    logger: Callable[[str], None] | None = None,
    worker_id: str = "",
) -> None:
    """Run the reviewed sequence after a durable worker has claimed the job."""
    log = logger or (lambda line: store.append_event(job_id, "log", line))
    provisioning = ProvisioningStore.runtime()
    try:
        if provisioning:
            provisioning.update("core", "running", detail="Reconciling the curated core suite.")
        store.transition(job_id, "running", actor=actor, detail="Core suite execution started.")
        checked = plan(root)
        if not checked["ready"]:
            store.transition(job_id, "failed", actor=actor, detail="Core suite blocked: " + checked["error"])
            return
        runtime = RuntimePaths()
        from ctl import onboarding_state, workflow_secrets

        owner = workflow_secrets.job_identity(job_id)
        if owner:
            owner = onboarding_state.remember_owner("lobehub", owner, runtime)
        env_files = ensure_core_envs(runtime.root)
        from ctl.lifecycle.materialize import materialize

        lobehub_project = materialize(load().get("lobehub"), root)
        env_files["lobehub"] = lobehub_project / ".env"
        sync_sign_in(load().catalog, tailnet_dns_name(), Authentik.runtime())
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
                project,
                log,
                env=envs[service_id],
                extra_files=compose_overrides(service_id, project),
                recreate=service_id == "lobehub" or (service_id == "freellmapi" and bool(wiring["provider_count"])),
                timeout=1800,
                on_output=lambda _line: None,
            )
            safe_output = redact(output or f"exit {rc}")
            if rc != 0:
                store.transition(job_id, "failed", actor=actor, detail=f"Stopped at {service_id}: {safe_output}")
                return
            deadline = time.monotonic() + (900 if service_id == "lobehub" else HEALTH_TIMEOUT_SECONDS)
            while time.monotonic() < deadline:
                live = service_status(service, "", root)
                if live["health_state"] == "healthy":
                    break
                time.sleep(2)
            else:
                live = service_status(service, "", root)
                store.transition(
                    job_id,
                    "failed",
                    actor=actor,
                    detail=f"Stopped at {service_id}: health check did not pass ({redact(live['detail'])}).",
                )
                return
            log(f"{service_id}: compose start completed")
            store.append_event(job_id, "step.verified", service_id)
            if service_id == "lobehub":
                from ctl.lobehub_ops import reconcile

                policy_ready, policy_detail = reconcile(log)
                if not policy_ready:
                    store.transition(
                        job_id,
                        "failed",
                        actor=actor,
                        detail="LobeChat model and agent policy failed: " + redact(policy_detail),
                        error_code="lobehub_policy_failed",
                        step_id="lobehub_policy",
                    )
                    return
                from ctl.control_state import ControlState

                control = ControlState.runtime()
                if control:
                    control.set_installation("lobehub", "running", job_id=job_id, route_state="ready")
                    saved = control.service_identity("lobehub") or {}
                    if saved.get("state") != "ready":
                        control.set_service_identity(
                            "lobehub",
                            "native_oidc",
                            "migration_required",
                            owner_uid=owner["owner_uid"] if owner else "",
                            job_id=job_id,
                            detail="Open LobeChat; its Authentik account will be verified automatically.",
                        )
                onboarding_state.mark_configured("lobehub", runtime)
            if service_id == "ollama":
                rc, output = actions.compose_exec(
                    project,
                    "ollama",
                    ["ollama", "pull", EMBEDDING_MODEL],
                    log,
                    timeout=MODEL_TIMEOUT_SECONDS,
                    env=envs[service_id],
                )
                if rc != 0:
                    store.transition(
                        job_id,
                        "failed",
                        actor=actor,
                        detail="Ollama embedding model preparation failed: " + redact(output),
                    )
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
        try:
            _queue_installer_core_apps(store, actor, root, log)
        except (OSError, ValueError) as exc:
            # The AI suite itself is up; the app shows its own Install/Retry button.
            log("Could not queue the remaining core apps: " + redact(str(exc)))
        verified, detail = _verify_platform(runtime, wiring)
        if not verified:
            state = "waiting_for_confirmation" if not wiring["chat_configured"] else "failed"
            store.transition(job_id, state, actor=actor, detail=detail)
            if provisioning:
                if state == "waiting_for_confirmation":
                    provisioning.update(
                        "core", "verified", detail="Core containers and the private LobeChat route are healthy."
                    )
                    provisioning.update("configuration", "waiting_for_user", detail=detail)
                else:
                    provisioning.update("core", "failed", detail=detail, error=detail)
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services and private integration checks passed.")
            provisioning.update(
                "verification",
                "verified",
                detail="Private LobeChat route and OIDC configuration, streamed chat, and embeddings passed.",
            )
        store.transition(
            job_id, "succeeded", actor=actor, detail="Core suite started, wired, and passed application-level checks."
        )
    except Exception as exc:
        stage = service_id if "service_id" in locals() else "prepare"
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail=f"Core setup failed during {stage}: {redact(str(exc))}",
            error_code="core_executor_failure",
            step_id=stage,
        )
        if provisioning:
            provisioning.update("core", "failed", error="Core executor failed safely.")


def start(store: JobStore, actor: str, root: Path, idempotency_key: str | None = None) -> dict[str, str]:
    """Queue core reconciliation; the persistent worker owns execution."""
    del root  # retained in the public call shape while release layout is migrated
    return store.create(
        kind="lifecycle",
        service_id="core-suite",
        action="install",
        actor=actor,
        detail="Reconcile the reviewed core platform",
        idempotency_key=idempotency_key,
    )


def start_verify(store: JobStore, actor: str, idempotency_key: str | None = None) -> dict[str, str]:
    """Queue contract verification without pulling or recreating services."""
    return store.create(
        kind="verification",
        service_id="core-suite",
        action="verify",
        actor=actor,
        detail="Verify live core platform contracts",
        idempotency_key=idempotency_key,
    )


def _run_verify(store: JobStore, job_id: str, actor: str) -> None:
    provisioning = ProvisioningStore.runtime()
    store.transition(job_id, "running", actor=actor, detail="Live platform verification started.", step_id="verify")
    try:
        runtime = RuntimePaths()
        wiring = configure_wiring(runtime)
        ok, detail = _verify_platform(runtime, wiring)
        if not ok:
            if provisioning:
                provisioning.update("verification", "failed", detail=detail, error=detail)
            store.transition(
                job_id,
                "failed",
                actor=actor,
                detail=detail,
                error_code="platform_verification_failed",
                step_id="verify",
            )
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services remain configured and reachable.")
            provisioning.update("verification", "verified", detail=detail)
        store.transition(job_id, "succeeded", actor=actor, detail=detail, step_id="complete")
    except Exception as exc:
        detail = "Platform verification failed safely: " + redact(str(exc))
        if provisioning:
            provisioning.update("verification", "failed", error=detail)
        store.transition(
            job_id, "failed", actor=actor, detail=detail, error_code="platform_verification_failed", step_id="verify"
        )


def execute_claimed(store: JobStore, job: dict, worker_id: str, root: Path) -> None:
    """Dispatch one already-claimed, allowlisted core job."""
    if job.get("service_id") != "core-suite" or job.get("action") not in {"install", "verify"}:
        store.transition(
            str(job["id"]),
            "failed",
            actor=worker_id,
            detail="The worker rejected an unsupported job.",
            error_code="unsupported_job",
        )
        return
    if job.get("action") == "verify":
        _run_verify(store, str(job["id"]), str(job.get("actor") or "system"))
        return
    _run(store, str(job["id"]), str(job.get("actor") or "system"), root, worker_id=worker_id)
