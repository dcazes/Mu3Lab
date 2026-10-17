"""One browser refresh, using shared observations and the caller's own identity."""

from __future__ import annotations

from fastapi import APIRouter

from ctl import backup_service, bootstrap_state, dismissals, local_models
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.routes import chat, jobs, system
from ctl.api.security import Member, MemberMutation
from ctl.api.service_view import service_snapshot
from ctl.status import signals
from ctl.store import db

router = APIRouter(prefix="/api/v1", tags=["snapshot"], route_class=ContractRoute)


@router.get("/snapshot", response_model=models.SnapshotResponse, response_model_exclude_none=True)
def snapshot(person: Member, status: bool = False) -> models.SnapshotResponse:
    # Someone is using Mu3Lab: the worker's upkeep keeps its usual pace.
    signals.viewed()
    # The page shows app statuses: the worker checks them again if its last check is a minute old.
    if status:
        signals.status_wanted()

    def read() -> dict:
        return {
            "services": service_snapshot(person),
            "system": system.system(person),
            "jobs": jobs.list_jobs(person),
            "identity": person,
            "chat": chat.chat_status(person),
            "catalog": system.catalog(person),
            "core": jobs.core_setup(person),
            "provisioning": system.provisioning(person),
            "audit": jobs.audit(person) if person.get("is_admin") else {"ok": True, "available": False, "events": []},
            "backup_attention": backup_service.attention() if person.get("is_admin") else [],
            "model_runner": local_models.runner().id,
            "dismissed": dismissals.for_person(person),
        }

    path = db.database()
    if not path.is_file():
        return models.SnapshotResponse.model_validate(read())
    # All stores reuse this connection: a refresh sees one database snapshot.
    with db.connect(path) as connection:
        connection.execute("BEGIN")
        result = models.SnapshotResponse.model_validate(read())
    if person.get("is_admin"):
        bootstrap_state.confirm_dashboard_ready()
    return result


@router.post("/dismissed", response_model=models.DismissedResponse)
def dismiss(body: models.DismissRequest, person: MemberMutation) -> models.DismissedResponse:
    """Hide one Home warning for this person until it changes."""
    return models.DismissedResponse(dismissed=dismissals.dismiss(person, body.warning))
