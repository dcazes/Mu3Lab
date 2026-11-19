"""Settings > Ports: every installed app's addresses and ports, for administrators."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from ctl import ports
from ctl.api import models
from ctl.api.contracts import ContractRoute
from ctl.api.security import Operator

ROOT = Path(__file__).resolve().parents[3]
router = APIRouter(prefix="/api/v1", tags=["ports"], route_class=ContractRoute)


@router.get("/ports", response_model=models.PortsResponse)
async def list_ports(_admin: Operator) -> models.PortsResponse:
    return models.PortsResponse.model_validate(await run_in_threadpool(ports.report, ROOT))
