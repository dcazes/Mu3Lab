"""Typed evidence behind every "usable" claim, one check per requirement.

A service is usable only while each check it requires has passed recently:
its process answers the declared health probe, its private HTTPS route
answers a real request, and an Authentik-protected app's sign-in hand-off
was verified within its window. Configuration (a published Serve port, a
saved identity row) is never evidence on its own.

The worker records process and route checks with each observation and keeps
their history across cycles; sign-in evidence lives in control state and is
judged when the API reads it, because its age changes between observations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from ctl.registry import Service

CheckState = Literal["pass", "checking", "fail", "pending", "stale", "not_required"]
CHECK_STATES = frozenset({"pass", "checking", "fail", "pending", "stale", "not_required"})

# A working route that stops answering stays usable ("checking") until it has
# failed twice in a row or has gone this long without answering.
ROUTE_GRACE_SECONDS = 120
ROUTE_FAILURES_TO_FAIL = 2
# The worker re-runs sign-in checks this often; evidence older than the
# maximum age no longer supports a usable claim.
SIGN_IN_RECHECK_SECONDS = 6 * 3600
SIGN_IN_RETRY_SECONDS = 15 * 60
SIGN_IN_MAX_AGE_SECONDS = 24 * 3600
# Observations older than this are not evidence of anything current. The
# observer runs every few seconds; this tolerates a slow cycle, not a stop.
OBSERVATION_MAX_AGE_SECONDS = 90
SIGN_IN_METHODS = frozenset({"oidc", "trusted_header", "gate"})


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def epoch(value: object) -> float | None:
    """Seconds since the epoch for a stored ISO timestamp, or None if absent/invalid."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()


@dataclass(frozen=True)
class Check:
    state: CheckState
    detail: str = ""
    checked_at: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    failures: int = 0

    @property
    def satisfied(self) -> bool:
        """Whether this check supports a usable claim right now."""
        return self.state in {"pass", "checking", "not_required"}

    def public(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, value: object) -> Check | None:
        """Read a stored check; anything malformed is treated as no history."""
        if not isinstance(value, dict) or value.get("state") not in CHECK_STATES:
            return None
        failures = value.get("failures", 0)
        return cls(
            state=value["state"],
            detail=str(value.get("detail") or ""),
            checked_at=str(value.get("checked_at") or ""),
            last_success_at=str(value.get("last_success_at") or ""),
            last_failure_at=str(value.get("last_failure_at") or ""),
            failures=failures if type(failures) is int and failures >= 0 else 0,
        )


def process_check(previous: Check | None, lifecycle_state: str, detail: str, now: float) -> Check:
    prior = previous or Check("pending")
    stamp = iso(now)
    if lifecycle_state == "ready":
        return Check("pass", "The app answers its health check.", stamp, stamp, prior.last_failure_at)
    if lifecycle_state in {"planned", "stopped"}:
        return Check("not_required", detail, stamp, prior.last_success_at, prior.last_failure_at)
    if lifecycle_state == "starting":
        return Check("pending", "Waiting for the app's health check.", stamp, prior.last_success_at)
    return Check("fail", detail, stamp, prior.last_success_at, stamp, prior.failures + 1)


def route_check(
    previous: Check | None,
    *,
    required: bool,
    configured: bool | None,
    healthy: bool,
    dns_known: bool,
    probe: tuple[bool, float] | None,
    now: float,
) -> Check:
    """Fold one cycle's route evidence into the route's history.

    ``configured`` is None when Tailscale Serve could not be read, so the
    probe alone decides. ``probe`` is (answered, probed_at); a cached probe
    repeats its ``probed_at`` and so is not counted as another failure.
    """
    if not required:
        return Check("not_required")
    prior = previous or Check("pending")
    if not healthy:
        return replace(prior, state="pending", detail="The route is checked once the app is healthy.")
    if configured is False:
        return replace(
            prior,
            state="fail",
            detail="The app's private HTTPS route is not published in Tailscale Serve.",
            checked_at=iso(now),
            last_failure_at=iso(now),
            failures=max(prior.failures + 1, ROUTE_FAILURES_TO_FAIL),
        )
    if not dns_known or probe is None:
        failed_at = iso(now)
        failures = prior.failures + 1
        detail = "This server's private Tailscale address is not known, so the route could not be checked."
    else:
        answered, probed_at = probe
        stamp = iso(probed_at)
        if answered:
            return Check("pass", "The private HTTPS route answered.", stamp, stamp, prior.last_failure_at, 0)
        repeated = stamp == prior.checked_at and prior.state in {"checking", "fail"}
        failed_at = stamp
        failures = prior.failures if repeated else prior.failures + 1
        detail = "The app's private HTTPS route did not answer its last check."
    last_success = epoch(prior.last_success_at)
    in_grace = last_success is not None and now - last_success <= ROUTE_GRACE_SECONDS
    state: CheckState = "checking" if in_grace and failures < ROUTE_FAILURES_TO_FAIL else "fail"
    if state == "checking":
        detail = "The private HTTPS route missed a check; checking again."
    return Check(state, detail, failed_at, prior.last_success_at, failed_at, failures)


def sign_in_required(service: Service) -> bool:
    sign_in = service.manifest.sign_in
    return (
        sign_in.method in SIGN_IN_METHODS
        and not sign_in.session_provider
        and service.private_https_port is not None
        and bool(service.ui.get("available", False))
    )


def sign_in_check(service: Service, saved: dict | None, now: float) -> Check:
    """Judge saved sign-in evidence by its outcome and age."""
    if not sign_in_required(service):
        return Check("not_required")
    saved = saved or {}
    verified_at = str(saved.get("last_verified_at") or "")
    verified = epoch(verified_at)
    state = str(saved.get("state") or "")
    detail = str(saved.get("detail") or "")
    if state in {"configuring", "migration_required"}:
        return Check("pending", "Sign-in is being set up.", last_success_at=verified_at)
    if state == "degraded":
        return Check(
            "fail",
            detail or "The last sign-in check failed.",
            str(saved.get("updated_at") or ""),
            verified_at,
            str(saved.get("updated_at") or ""),
            1,
        )
    if state != "ready" or verified is None:
        return Check("pending", "Sign-in has not been checked yet; Mu3Lab checks it shortly.")
    if now - verified > SIGN_IN_MAX_AGE_SECONDS:
        return Check(
            "stale",
            "Sign-in has not been re-verified in the last 24 hours.",
            verified_at,
            verified_at,
        )
    return Check("pass", "Sign-in through Authentik was verified.", verified_at, verified_at)


def sign_in_due(service: Service, saved: dict | None, now: float) -> bool:
    """Whether the worker should run this app's sign-in check now."""
    if not sign_in_required(service):
        return False
    saved = saved or {}
    if saved.get("state") in {"configuring", "migration_required"}:
        return False
    if saved.get("state") == "degraded":
        updated = epoch(saved.get("updated_at"))
        return updated is None or now - updated >= SIGN_IN_RETRY_SECONDS
    verified = epoch(saved.get("last_verified_at"))
    if verified is None:
        attempted = epoch(saved.get("updated_at")) if saved.get("state") == "ready" else None
        return attempted is None or now - attempted >= SIGN_IN_RETRY_SECONDS
    return now - verified >= SIGN_IN_RECHECK_SECONDS


def stale(check: Check, observed_at: str) -> Check:
    if check.state == "not_required":
        return check
    return replace(check, state="stale", detail=f"Status has not been refreshed since {observed_at or 'startup'}.")
