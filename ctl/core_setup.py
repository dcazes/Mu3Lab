"""Curated core-suite reconciliation and verification.

The executor accepts no user-supplied Compose path or command. It materializes
private configuration, starts reviewed services in dependency order, prepares
the local embedding model, and records exactly which user-input boundary is
still pending rather than claiming a half-configured platform is ready.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ctl import actions
from ctl.control_state import ControlState
from ctl.core_wiring import configure as configure_wiring
from ctl.engine.install import run_install
from ctl.jobs import JobStore, redact
from ctl.platform_apps import by_capability
from ctl.provisioning import ProvisioningStore
from ctl.registry import Registry, RegistryError, Service, load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.service_state import tailnet_dns_name, tailnet_serve_ports


def capacity() -> dict:
    """Return a conservative, non-mutating admission result for the core suite."""
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


def core_services(registry: Registry | None = None) -> list[Service]:
    """Core services in dependency order; foundation dependencies are already installed."""
    registry = registry or load()
    ordered: list[Service] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service: Service) -> None:
        if service.id in visited:
            return
        if service.id in visiting:
            raise RegistryError("Core service dependencies contain a cycle.")
        visiting.add(service.id)
        for dependency in service.manifest.depends_on:
            required = registry.get(dependency)
            if required.stage == "optional":
                raise RegistryError(f"{service.name} requires optional app {required.name}.")
            if required.stage == "core":
                visit(required)
        visiting.remove(service.id)
        visited.add(service.id)
        ordered.append(service)

    for service in registry.services:
        if service.stage == "core":
            visit(service)
    return ordered


def plan(root: Path) -> dict:
    """Return a safe, deterministic plan without mutating the host."""
    admission = capacity()
    try:
        services = core_services()
    except RegistryError as exc:
        return {"ready": False, "error": str(exc), "services": [], "missing": [], "capacity": admission}
    missing = [service.id for service in services if not (service.compose_path(root) / "docker-compose.yml").is_file()]
    ready = bool(services) and not missing and admission["ok"]
    error = (
        ""
        if ready
        else (
            "No core apps are declared."
            if not services
            else "Missing app manifests for " + ", ".join(missing)
            if missing
            else "; ".join(admission["reasons"])
        )
    )
    return {
        "ready": ready,
        "error": error,
        "services": [service.id for service in services],
        "missing": missing,
        "capacity": admission,
    }


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
    litellm_env = read_runtime_env(runtime.projects / by_capability("model_proxy").id / ".env")
    key = litellm_env.get("LITELLM_MASTER_KEY", "")
    if not key or not _http_ok("http://127.0.0.1:4000/v1/models", headers={"Authorization": f"Bearer {key}"}):
        return False, "LiteLLM did not expose its authenticated model registry"
    if not _http_ok("http://127.0.0.1:11434/api/tags"):
        return False, "Ollama model registry did not answer"
    if not _http_ok("http://127.0.0.1:3211/__mu3lab_lobehub_health"):
        return False, "LobeChat did not answer its private health endpoint"
    lobehub = read_runtime_env(runtime.projects / by_capability("chat").id / ".env")
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
    if 8457 not in tailnet_serve_ports():
        return False, "LobeChat's private Tailscale route is not published"
    return (
        True,
        "Private LobeChat route, streamed chat and embeddings passed live checks. Authentik sign-in is configured; "
        "it is confirmed the first time you sign in to LobeChat.",
    )


def _run(
    store: JobStore,
    job_id: str,
    actor: str,
    root: Path,
    logger: Callable[[str], None] | None = None,
    worker_id: str = "",
) -> None:
    """Install each declared core app under one durable parent job."""
    log = logger or (lambda line: store.append_event(job_id, "log", line))
    provisioning = ProvisioningStore.runtime()
    current = "prepare"
    try:
        if provisioning:
            provisioning.update("core", "running", detail="Installing the curated core suite.")
        store.transition(job_id, "running", actor=actor, detail="Core suite execution started.")
        checked = plan(root)
        if not checked["ready"]:
            raise ValueError("Core suite blocked: " + checked["error"])
        registry = load()
        control = ControlState.runtime()
        parent = {"id": job_id}
        for service in core_services(registry):
            current = service.id
            if worker_id and not store.heartbeat(job_id, worker_id, step_id=current):
                raise RuntimeError("job lease was lost")
            store.append_event(job_id, "step.started", f"Installing {service.name}.")
            if not run_install(store, control, parent, service, registry, actor, root, complete_job=False):
                if provisioning:
                    provisioning.update(
                        "core", "failed", error=f"{service.name} installation failed; see the core job for details."
                    )
                return
            log(f"{service.name}: installed and verified")
            store.append_event(job_id, "step.verified", service.id)
        runtime = RuntimePaths()
        wiring = configure_wiring(runtime, registry.catalog)
        verified, detail = _verify_platform(runtime, wiring)
        if not verified:
            state = "waiting_for_confirmation" if not wiring["chat_configured"] else "failed"
            store.transition(job_id, state, actor=actor, detail=detail)
            if provisioning:
                if state == "waiting_for_confirmation":
                    provisioning.update(
                        "core", "verified", detail="Core services and their private routes are healthy."
                    )
                    provisioning.update("configuration", "waiting_for_user", detail=detail)
                else:
                    provisioning.update("core", "failed", detail=detail, error=detail)
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services and private integration checks passed.")
            provisioning.update("verification", "verified", detail=detail)
        store.transition(
            job_id, "succeeded", actor=actor, detail="Core suite installed and passed application-level checks."
        )
    except (OSError, ValueError, RuntimeError) as exc:
        store.transition(
            job_id,
            "failed",
            actor=actor,
            detail=f"Core setup failed during {current}: {redact(str(exc))}",
            error_code="core_executor_failure",
            step_id=current,
        )
        if provisioning:
            provisioning.update("core", "failed", error="Core installation failed; see the core job for details.")


def start(
    store: JobStore,
    actor: str,
    root: Path,
    idempotency_key: str | None = None,
    *,
    actor_subject: str = "",
    prepare=None,
) -> dict[str, str]:
    """Queue core reconciliation; the persistent worker owns execution."""
    del root  # retained in the public call shape while release layout is migrated
    return store.create(
        kind="lifecycle",
        service_id="core-suite",
        action="install",
        actor=actor,
        actor_subject=actor_subject,
        namespace="core.install",
        prepare=prepare,
        detail="Reconcile the reviewed core platform",
        idempotency_key=idempotency_key,
    )


def start_verify(
    store: JobStore, actor: str, idempotency_key: str | None = None, *, actor_subject: str = ""
) -> dict[str, str]:
    """Queue contract verification without pulling or recreating services."""
    return store.create(
        kind="verification",
        service_id="core-suite",
        action="verify",
        actor=actor,
        actor_subject=actor_subject,
        namespace="core.verify",
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
    except (OSError, ValueError, RuntimeError) as exc:
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
