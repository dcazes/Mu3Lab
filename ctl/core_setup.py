"""Curated core-suite reconciliation and verification.

The executor accepts no user-supplied Compose path or command. It materializes
private configuration, starts reviewed services in dependency order, prepares
the local embedding model, and records exactly which user-input boundary is
still pending rather than claiming a half-configured platform is ready.
"""

from __future__ import annotations

import threading
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
from ctl.service_state import status as service_status
from ctl.core_wiring import EMBEDDING_MODEL, configure as configure_wiring

CORE_ORDER = ("ollama", "freellmapi", "litellm", "open-webui", "firecrawl", "surfsense")
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
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        reasons.append("unsupported CPU architecture")
    if disk.free < 12 * 1024**3:
        reasons.append("at least 12 GiB free disk is required for the core suite")
    if memory_total and memory_total < 4 * 1024**3:
        reasons.append("at least 4 GiB memory is required for the core suite")
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


def _provision_freellmapi(runtime: RuntimePaths) -> tuple[bool, str]:
    """Create/reuse an internal scoped client credential through FreeLLMAPI.

    FreeLLMAPI intentionally has a separate single-user dashboard. Mu3Lab
    never exposes it: this local-only bootstrap account is used solely to mint
    the LiteLLM client-profile key, and provider keys remain declaratively
    applied from the root-only Mu3Lab configuration file.
    """
    from ctl.secrets import read_runtime_env

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
    temporary.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
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


def _verify_platform(runtime: RuntimePaths, wiring: dict) -> tuple[bool, str]:
    """Check application-level contracts after container health has passed."""
    litellm_env = __import__("ctl.secrets", fromlist=["read_runtime_env"]).read_runtime_env(
        runtime.projects / "litellm" / ".env")
    key = litellm_env.get("LITELLM_MASTER_KEY", "")
    if not key or not _http_ok("http://127.0.0.1:4000/v1/models",
                               headers={"Authorization": f"Bearer {key}"}):
        return False, "LiteLLM did not expose its authenticated model registry"
    if not _http_ok("http://127.0.0.1:11434/api/tags"):
        return False, "Ollama model registry did not answer"
    if not _http_ok("http://127.0.0.1:8084/health"):
        return False, "Open WebUI did not answer its health endpoint"
    if not _http_ok("http://127.0.0.1:3002/"):
        return False, "Firecrawl did not answer its health endpoint"
    if not _http_ok("http://127.0.0.1:3929/"):
        return False, "SurfSense did not answer its user interface"
    if not wiring["chat_configured"]:
        return False, "Waiting for at least one external inference-provider key"
    if not _http_ok("http://127.0.0.1:3001/readyz"):
        return False, "FreeLLMAPI has no currently usable configured provider"
    headers = {"Authorization": f"Bearer {key}"}
    chat_status, chat = _http_json("http://127.0.0.1:4000/v1/chat/completions", "POST", {
        "model": "mu3lab-chat", "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 4, "temperature": 0,
    }, headers=headers)
    if chat_status != 200 or not isinstance(chat.get("choices"), list):
        return False, "A real FreeLLMAPI chat request did not complete through LiteLLM"
    embedding_status, embedding = _http_json("http://127.0.0.1:4000/v1/embeddings", "POST", {
        "model": "mu3lab-embed", "input": "Mu3Lab readiness check",
    }, headers=headers)
    vectors = embedding.get("data") if isinstance(embedding, dict) else None
    if embedding_status != 200 or not isinstance(vectors, list) or not vectors:
        return False, "The local embedding model did not return vectors through LiteLLM"
    return True, "Private AI routing, chat, and embeddings passed live checks."


def _run(store: JobStore, job_id: str, actor: str, root: Path,
         logger: Callable[[str], None] | None = None) -> None:
    """Run the reviewed sequence in a detached worker."""
    log = logger or (lambda _line: None)
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
        wiring = configure_wiring(runtime)
        envs = _runtime_envs(env_files, wiring)
        for service_id in CORE_ORDER:
            service = load().get(service_id)
            project = service.compose_path(root)
            rc, output = actions.compose_up(
                project, log, env=envs[service_id],
                recreate=service_id == "freellmapi" and bool(wiring["provider_count"]))
            safe_output = redact(output or f"exit {rc}")
            if rc != 0:
                store.transition(job_id, "failed", actor=actor,
                                 detail=f"Stopped at {service_id}: {safe_output}")
                return
            deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
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
                provisioning.update("configuration" if state == "waiting_for_confirmation" else "core",
                                    "waiting_for_user" if state == "waiting_for_confirmation" else "failed",
                                    detail=detail, error="" if state == "waiting_for_confirmation" else detail)
            return
        if provisioning:
            provisioning.update("core", "verified", detail="Core services and private integration checks passed.")
            provisioning.update("verification", "verified", detail="User-facing platform checks passed.")
        store.transition(job_id, "succeeded", actor=actor,
                         detail="Core suite started, wired, and passed application-level checks.")
    except Exception as exc:  # noqa: BLE001 - job state must become terminal
        store.transition(job_id, "failed", actor=actor,
                         detail=f"Core executor failed safely: {redact(str(exc))}")
        if provisioning:
            provisioning.update("core", "failed", error="Core executor failed safely.")


def start(store: JobStore, actor: str, root: Path) -> dict[str, str]:
    """Create and asynchronously execute one core-suite job."""
    job = store.create(kind="lifecycle", service_id="core-suite", action="install",
                       actor=actor, detail="Install the reviewed imperative suite")
    thread = threading.Thread(target=_run, args=(store, job["id"], actor, root), daemon=True)
    thread.start()
    return job
