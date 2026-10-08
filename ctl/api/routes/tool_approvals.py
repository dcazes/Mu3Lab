"""Same-origin human decisions over immutable, encrypted tool requests."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from ctl import mcp_gateway
from ctl.api import models, runtime
from ctl.api.contracts import ContractRoute
from ctl.api.errors import ApiError
from ctl.api.security import Operator, OperatorMutation

router = APIRouter(prefix="/api/v1/tool-approvals", tags=["tool approvals"], route_class=ContractRoute)


@router.get("", response_model=models.ApprovalsResponse)
def approvals(operator: Operator) -> models.ApprovalsResponse:
    try:
        operations = mcp_gateway.authority().list_operations(runtime.mutation_subject(operator))
        return models.ApprovalsResponse.model_validate({"ok": True, "operations": operations})
    except (ValueError, OSError):
        raise ApiError(503, "Tool approval storage is unavailable.", code="approval_store_unavailable") from None


@router.get("/{operation_id}", response_model=models.ApprovalResponse, response_model_exclude_none=True)
def approval(operation_id: str, operator: Operator) -> models.ApprovalResponse:
    try:
        operation = mcp_gateway.authority().view(operation_id, runtime.mutation_subject(operator), arguments=True)
        return models.ApprovalResponse.model_validate({"ok": True, "operation": operation})
    except ValueError:
        raise ApiError(404, "This approval request is unavailable.", code="approval_not_found") from None


def _decide(operation_id: str, operator, decision: str) -> dict:
    subject = runtime.mutation_subject(operator)
    mcp_gateway.verify_operator(subject)
    operation = mcp_gateway.authority().decide(operation_id, subject, approve=decision == "approve")
    result = {"ok": True, "operation": operation}
    if decision == "approve":
        result["execution"] = mcp_gateway.dispatch_operation(operation_id, subject)
        result["operation"] = mcp_gateway.authority().view(operation_id, subject)
    return result


@router.post("/{operation_id}/decision", response_model=models.ApprovalResponse, response_model_exclude_none=True)
async def decide(
    operation_id: str, payload: models.ApprovalDecisionRequest, operator: OperatorMutation
) -> models.ApprovalResponse:
    try:
        return models.ApprovalResponse.model_validate(
            await run_in_threadpool(_decide, operation_id, operator, payload.decision)
        )
    except (ValueError, OSError):
        raise ApiError(
            409,
            "Approval is unavailable, expired, changed or already decided. Refresh its status.",
            code="approval_conflict",
        ) from None


@router.post("/{operation_id}/execute", response_model=models.ApprovalResponse, response_model_exclude_none=True)
async def execute(operation_id: str, operator: OperatorMutation) -> models.ApprovalResponse:
    try:
        subject = runtime.mutation_subject(operator)
        result = await run_in_threadpool(mcp_gateway.dispatch_operation, operation_id, subject)
        operation = mcp_gateway.authority().view(operation_id, subject)
        return models.ApprovalResponse.model_validate({"ok": True, "operation": operation, "execution": result})
    except (ValueError, OSError):
        raise ApiError(
            409, "This approved operation could not be executed. Refresh its status.", code="approval_conflict"
        ) from None
