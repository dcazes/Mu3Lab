"""One browser refresh, using shared observations and the caller's own identity."""

from __future__ import annotations

from fastapi import APIRouter

from ctl import bootstrap_state
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.routes import chat, jobs, system
from ctl.api.security import Member
from ctl.api.service_view import service_snapshot
from ctl.store import db

router = APIRouter(prefix="/api/v1", tags=["snapshot"], route_class=ContractRoute)


@router.get("/snapshot", response_model=models.SnapshotResponse, response_model_exclude_none=True)
def snapshot(person: Member) -> models.SnapshotResponse:
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
