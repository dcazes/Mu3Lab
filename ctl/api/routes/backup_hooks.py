"""Kopia's before- and after-actions: stop an app just before its backup, start it just after.

Only the backup service calls these, from ``apps/kopia-ui/backup-hook.sh``,
with the hook token from its own settings file. The token can do nothing else:
it never identifies a person and no other route accepts it. The work itself
runs as worker jobs (only the worker runs Docker); each call waits for its job.
A refused before-action makes Kopia skip that backup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header
from starlette.concurrency import run_in_threadpool

from ctl import backup_service
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError

ROOT = Path(__file__).resolve().parents[3]
SOURCE = re.compile(r"^/data/([a-z0-9][a-z0-9-]{0,62})$")

router = APIRouter(prefix="/api/v1/backup-hooks", tags=["backups"], route_class=ContractRoute)


@router.post("/{action}", response_model=models.BackupHookResponse, include_in_schema=False)
async def backup_hook(
    action: Literal["before-snapshot-root", "after-snapshot-root"],
    payload_model: models.BackupHookRequest,
    authorization: str = Header(default=""),
) -> models.BackupHookResponse:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not backup_service.token_valid(token):
        raise ApiError(401, "Only Mu3Lab's backup service may call this.", code="unauthorized")
    match = SOURCE.fullmatch(payload_model.source)
    if not match:
        raise ApiError(400, "Unknown backup source.")
    source, store = match.group(1), runtime.job_store()
    handler = backup_service.before if action == "before-snapshot-root" else backup_service.after
    try:
        detail = await run_in_threadpool(handler, source, store, ROOT)
    except backup_service.HookRefused as refused:
        raise ApiError(409, str(refused), code="backup_skipped") from refused
    return models.BackupHookResponse(ok=True, detail=detail)
