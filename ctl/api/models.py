"""Public HTTP contracts. Python models are the source of dashboard API types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

DisplayState = Literal["running", "stopped", "working", "checking", "not_installed", "needs_attention"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ApiErrorResponse(ApiModel):
    ok: Literal[False] = False
    code: str
    message: str
    recommended_action: str
    error: str | dict[str, JsonValue]
    blocking_job_id: str | None = None


TailscaleConnectionState = Literal["connected"] | Literal["disconnected"] | Literal["unavailable"]


class Health(ApiModel):
    ok: bool
    version: str


class MobileClient(ApiModel):
    id: str
    name: str
    kind: Literal["native"] | Literal["pwa"] | Literal["web"]
    support: Literal["official"] | Literal["community"] | Literal["experimental"]
    platforms: list[Literal["ios"] | Literal["android"] | Literal["web"]]
    install: dict[Literal["ios"] | Literal["android"] | Literal["web"], str]
    setup: (
        Literal["server_url"]
        | Literal["pwa"]
        | Literal["web"]
        | Literal["api_token"]
        | Literal["device_qr"]
        | Literal["developer_mode"]
    )
    summary: str
    steps: list[str]
    homepage: str | None
    source: str | None
    caveat: str
    fallback: bool


class ServiceMobile0(ApiModel):
    primary: str
    clients: list[MobileClient]


class ServiceUi(ApiModel):
    state: Literal["ready"] | Literal["route_pending"] | Literal["unavailable"]
    url: str | None
    label: str
    authentication: str
    reason: str | None
    launch_label: str | None = None


class ServiceCheck(ApiModel):
    state: Literal["pass", "checking", "fail", "pending", "stale", "not_required"]
    detail: str
    checked_at: str
    last_success_at: str
    last_failure_at: str
    failures: int


class ServiceChecks(ApiModel):
    process: ServiceCheck
    route: ServiceCheck
    sign_in: ServiceCheck


class ServiceRecovery(ApiModel):
    """An update or restore that could not be undone; the app waits for a person."""

    operation_id: str
    kind: Literal["backup", "restore", "update"]
    detail: str
    snapshot_id: str
    since: str


class ServiceUpdate(ApiModel):
    repository: str
    installed_version: str
    approved_version: str
    update_available: bool
    supporting_only: bool


class ServiceAccount(ApiModel):
    mode: str
    user_action: str


class ServiceInitialization(ApiModel):
    mode: str
    state: str
    job_id: str | None = None
    verified_at: str | None = None
    last_error: dict[str, JsonValue] | None = None


class ServiceContainersItem(ApiModel):
    service: str
    name: str
    state: str
    status: str
    health: str
    image: str


class Service(ApiModel):
    mobile: ServiceMobile0 | None = None
    id: str
    name: str
    category: str
    lifecycle: Literal["always_on"] | Literal["shared"] | Literal["optional"]
    https_port: float
    private_https_port: float | None = None
    auth: Literal["oidc"] | Literal["proxy"] | Literal["trusted_header"] | Literal["local"] | Literal["excluded"]
    dependencies: list[str]
    stage: Literal["foundation"] | Literal["core"] | Literal["optional"]
    group: Literal["apps"] | Literal["ai"] | Literal["infrastructure"]
    routable: bool
    required: bool
    identity_note: str
    resource_guidance: str
    setup_action: str
    health_state: str
    recommended_action: str | None = None
    last_job_id: str
    last_error: str | dict[str, JsonValue]
    detail: str
    url: str
    route_ready: bool
    compose_present: bool
    ui: ServiceUi | None = None
    allowed_actions: (
        list[
            Literal["install"]
            | Literal["retry_setup"]
            | Literal["start"]
            | Literal["stop"]
            | Literal["restart"]
            | Literal["uninstall"]
            | Literal["uninstall_delete_data"]
        ]
        | None
    ) = None
    last_job: Job | None = None
    update: ServiceUpdate | None = None
    configuration: list[ServiceConfigField] | None = None
    account: ServiceAccount | None = None
    initialization: ServiceInitialization | None = None
    identity: ServiceIdentity | None = None
    containers: list[ServiceContainersItem] | None = None
    checks: ServiceChecks | None = None
    recovery: ServiceRecovery | None = None
    blocking_check: Literal["process", "route", "sign_in"] | None = None

    display_state: DisplayState
    reason: str
    installed: bool
    observed_at: str


class ServiceIdentity(ApiModel):
    mode: (
        Literal["native_oidc"] | Literal["trusted_header"] | Literal["proxy_gate"] | Literal["local"] | Literal["none"]
    )
    state: (
        Literal["unconfigured"]
        | Literal["configuring"]
        | Literal["migration_required"]
        | Literal["ready"]
        | Literal["degraded"]
        | Literal["unsupported"]
    )
    launch_url: str
    detail: str
    last_verified_at: str
    job_id: str
    error: dict[str, JsonValue] | None = None


class ServiceConfigField(ApiModel):
    key: str
    type: Literal["string"] | Literal["boolean"] | Literal["integer"] | Literal["enum"] | Literal["secret"]
    label: str | None = None
    required: bool | None = None
    default: str | bool | float | None = None
    options: list[str] | None = None
    value: str | bool | float | None = None
    secret_present: bool | None = None


class ServiceConfigResponse(ApiModel):
    ok: bool
    service_id: str
    fields: list[ServiceConfigField]
    restart_required: bool | None = None


class ServicesResponse(ApiModel):
    observed_at: str = ""
    ok: bool
    tailnet_dns_name: str
    services: list[Service]


class CatalogProfile(ApiModel):
    id: str
    name: str
    description: str
    services: list[str]


class CatalogService(ApiModel):
    summary: str
    tagline: str | None = None
    category: str | None = None
    stage_label: str | None = None
    resource_guidance: str | None = None
    integrations: list[str] | None = None


class CatalogResponse(ApiModel):
    ok: bool
    profiles: list[CatalogProfile]
    services: dict[str, CatalogService]


class Metric(ApiModel):
    total: float
    used: float
    percent: float


class BackupReadiness(ApiModel):
    ok: bool = True
    engine: str = "restic"
    available: bool = False
    repository_path: str = ""
    retention: dict[str, int] = Field(default_factory=dict)
    state: str | None = None
    detail: str | None = None
    repository_present: bool | None = None
    integrity_verified: bool | None = None
    snapshot_present: bool | None = None
    off_device: bool | None = None
    last_verified_at: str | None = None


class TailscaleServeStatus(ApiModel):
    state: Literal["available"] | Literal["unavailable"]
    ports: list[float]


class TailscaleStatus(ApiModel):
    state: TailscaleConnectionState
    backend_state: str
    online: bool
    dns_name: str
    detail: str
    serve: TailscaleServeStatus


class SystemResponse(ApiModel):
    observed_at: str = ""
    ok: bool
    cpu_percent: float
    uptime_seconds: float | None = None
    docker_ready: bool
    worker_state: str | None = None
    container_memory: dict[str, float] | None = None
    tailnet_dns_name: str
    tailscale: TailscaleStatus | None = None
    runtime_root: str
    memory: Metric
    disk: Metric
    backup: BackupReadiness


class IntegrationsResponseIntegrationsItem(ApiModel):
    source: str
    destination: str
    kind: str


class IntegrationsResponse(ApiModel):
    ok: bool
    policy: str
    integrations: list[IntegrationsResponseIntegrationsItem]


class IdentityResponse(ApiModel):
    ok: bool
    control_plane_auth: str
    username: str | None = None
    subject_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    groups: list[str] | None = None
    detail: str
    role: Literal["admin"] | Literal["member"] | Literal[""] | None = None
    is_admin: bool | None = None
    writes_enabled: bool


class CoreSetupResponseCapacity(ApiModel):
    ok: bool
    reasons: list[str] | None = None
    disk_free: float | None = None
    memory_total: float | None = None
    docker_ready: bool | None = None


class CoreSetupResponse(ApiModel):
    ok: bool
    ready_to_run: bool
    services: list[str]
    missing_manifests: list[str]
    current_job: Job | None = None
    next_action: str
    capacity: CoreSetupResponseCapacity | None = None
    provisioning: ProvisioningResponse | None = None


class ProvisioningPhase(ApiModel):
    phase_id: str
    label: str
    actual_state: str
    detail: str
    error: str
    updated_at: str
    attempts: float


class ProvisioningAction(ApiModel):
    kind: Literal["none"] | Literal["link"] | Literal["job"] | Literal["bootstrap"]
    label: str
    href: str | None = None
    endpoint: str | None = None


class ProvisioningResponseProgress(ApiModel):
    completed: float
    total: float


class ProvisioningResponse(ApiModel):
    ok: bool
    available: bool
    complete: bool
    phases: list[ProvisioningPhase]
    waiting: ProvisioningPhase | None = None
    blocked: ProvisioningPhase | None = None
    progress: ProvisioningResponseProgress | None = None
    next_action: ProvisioningAction | None = None


class ProviderCatalogItem(ApiModel):
    id: str
    name: str
    key_hint: str
    prefixes: list[str]
    instructions: str
    signup_url: str | None = None
    keys_url: str | None = None
    account: Literal["email"] | Literal["google"] | None = None
    recommended: bool | None = None
    free_tier: str | None = None
    payment_required: bool | None = None
    key_pattern: str | None = None


class VaultStatusBrowser_extension(ApiModel):
    browsers: list[str]
    server_url: str
    signed_in: bool | None = None


class VaultStatus(ApiModel):
    ok: bool = True
    seeded: bool
    pending_logins: float | None = None
    seeded_at: str | None = None
    browser_extension: VaultStatusBrowser_extension | None = None
    automatic: VaultAutomatic | None = None


class VaultAutomaticPeople0Item(ApiModel):
    uid: str
    name: str
    state: (
        Literal["up_to_date"]
        | Literal["partly_saved"]
        | Literal["waiting_for_account"]
        | Literal["skipped"]
        | Literal[""]
    )
    saved: float
    waiting: float


class VaultAutomatic(ApiModel):
    last_run: str | None
    ok: bool | None
    error: str | None
    people: list[VaultAutomaticPeople0Item] | None


class ProviderSetupProgress(ApiModel):
    verified: float
    complete: bool
    recommended: list[str]
    recommended_verified: list[str]
    recommended_minimum: float
    recommendation_met: bool


class VaultSetupResult(ApiModel):
    ok: bool
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    skipped: list[str]


class ProviderMetadata(ApiModel):
    id: str
    name: str
    label: str
    enabled: bool
    state: (
        Literal["saved"]
        | Literal["verifying"]
        | Literal["verified"]
        | Literal["degraded"]
        | Literal["disabled"]
        | Literal["removing"]
        | Literal["unsupported_legacy"]
    )
    key_hint: str
    credential_indicator: str
    model_samples: list[str]
    models_are_examples: bool
    last_attempt_at: str
    last_verified_at: str
    updated_at: str
    active_job_id: str
    error: str
    error_code: str | None = None
    recommended_action: str | None = None
    routed_via: str | None = None
    supported: bool


class CalendarConnectionCalendarsItem(ApiModel):
    id: str
    name: str


class CalendarConnection(ApiModel):
    ok: bool
    state: (
        Literal["not_installed"]
        | Literal["service_stopped"]
        | Literal["sso_not_ready"]
        | Literal["not_connected"]
        | Literal["awaiting_approval"]
        | Literal["connected"]
        | Literal["authentication_expired"]
        | Literal["unavailable"]
    )
    username_hint: str
    selected_calendar_id: str
    calendars: list[CalendarConnectionCalendarsItem]
    last_success_at: str
    error: str


class CalendarAuthorization(ApiModel):
    ok: bool
    state: Literal["awaiting_user"] | Literal["pending"] | Literal["connected"] | Literal["expired"] | Literal["failed"]
    authorization_id: str | None = None
    login_url: str | None = None
    expires_at: str | None = None
    poll_after_ms: float | None = None
    connection: CalendarConnection | None = None
    error: str | None = None


class CalendarEvent(ApiModel):
    id: str
    title: str
    start: str
    end: str
    all_day: bool
    editable: bool | None = None
    revision: str | None = None
    local: bool | None = None


class CalendarEventsCalendar(ApiModel):
    id: str
    name: str


class CalendarEvents(ApiModel):
    ok: bool
    state: str
    calendar: CalendarEventsCalendar | None = None
    fetched_at: str | None = None
    nextcloud_ready: bool | None = None
    events: list[CalendarEvent]
    error: str | None = None


class ProviderMetadataResponse(ApiModel):
    ok: bool
    providers: list[ProviderMetadata]
    setup: ProviderSetupProgress | None = None


class Job(ApiModel):
    id: str
    kind: str
    service_id: str
    action: str
    state: str
    actor: str
    created_at: str
    updated_at: str
    detail: str
    step_id: str | None = None
    error_code: str | None = None


class JobsResponse(ApiModel):
    ok: bool
    available: bool
    jobs: list[Job]


class AuditEvent(ApiModel):
    id: float
    job_id: str | None
    actor: str
    event: str
    created_at: str
    detail: str


class AuditResponse(ApiModel):
    ok: bool
    available: bool
    events: list[AuditEvent]


class ServiceLogsResponse(ApiModel):
    ok: bool
    service_id: str
    container: str
    lines: list[str]


class JobDetailResponse(ApiModel):
    ok: bool
    job: Job
    events: list[AuditEvent]


class BackupSnapshot(ApiModel):
    id: str
    short_id: str
    time: str
    reason: Literal["manual"] | Literal["pre-update"] | Literal["pre-restore"]
    version: str
    paths: list[str]


class BackupsResponse(ApiModel):
    ok: bool
    service_id: str
    backups: list[BackupSnapshot]
    readiness: BackupReadiness


class Mu3LabUpdate(ApiModel):
    ok: bool
    version: str
    commit: str
    date: str
    available: bool
    behind: float
    changes: list[str]
    blocked_reason: str
    checked_at: float


class UpdateResponse(ApiModel):
    ok: bool
    repository: str
    installed_version: str
    approved_version: str
    release_url: str
    update_available: bool
    supporting_only: bool
    update_enabled: bool
    blocked_reason: str


class McpServerAuth(ApiModel):
    type: str
    configured: bool
    auto_provision: bool
    auto_provision_note: str | None = None


class McpServerReview(ApiModel):
    status: str
    repository: str
    revision: str
    preferred: bool
    note: str | None = None


class McpServerToolsItem(ApiModel):
    id: str
    title: str
    risk: str
    enabled: bool
    permission: str | None = None
    parameters: dict[str, JsonValue] | None = None
    category: str | None = None
    core: bool | None = None
    offered: bool | None = None


class McpServerBlockedItem(ApiModel):
    id: str
    reason: str


class McpServer(ApiModel):
    id: str
    name: str
    service_id: str
    kind: str
    transport: str
    app_state: str
    enabled: bool
    prepared: bool | None = None
    state: (
        Literal["live"]
        | Literal["degraded"]
        | Literal["authentication_required"]
        | Literal["disabled"]
        | Literal["prepared"]
        | Literal["unavailable"]
        | Literal["starting"]
        | Literal["incompatible"]
        | Literal["failed"]
        | Literal["stopped"]
    )
    error: str | None = None
    last_verified_at: str | None = None
    auth: McpServerAuth
    review: McpServerReview | None = None
    configuration: list[ServiceConfigField] | None = None
    tools: list[McpServerToolsItem]
    gateway: bool | None = None
    categories: list[McpCategory] | None = None
    blocked: list[McpServerBlockedItem] | None = None
    unreviewed: list[str] | None = None


class McpCategory(ApiModel):
    id: str
    title: str
    summary: str
    enabled: bool
    default_on: bool


class McpRegistryResponse(ApiModel):
    ok: bool
    servers: list[McpServer]
    summary: dict[str, float]
    policy: str


class ChatProvider(ApiModel):
    id: str
    name: str
    ready: bool
    url: str
    authentication: str
    detail: str


class ChatAssistant(ApiModel):
    id: str
    name: str
    status: Literal["ready", "pending", "failed", "not_connected", "connector_down", "needs_review", "operator_only"]
    detail: str


class ChatStatus(ApiModel):
    assistants: list[ChatAssistant] | None = None
    connected: bool | None = None
    ok: bool
    ready: bool
    url: str
    authentication: str
    mcp_enabled_count: float
    detail: str
    providers: list[ChatProvider] | None = None


class SystemConfig(ApiModel):
    ok: bool
    compute_mode: Literal["auto"] | Literal["cpu"] | Literal["nvidia"] | Literal["amd"]
    resolved_compute_mode: Literal["cpu"] | Literal["nvidia"] | Literal["amd"]
    available_modes: list[str]
    updated_at: str
    updated_by: str


class InstallBatchItem(ApiModel):
    batch_id: str
    service_id: str
    ordinal: float
    explicitly_selected: float
    state: str
    job_id: str
    error_json: str | None = None
    started_at: str
    completed_at: str
    priority: float
    download_state: Literal[""] | Literal["downloading"] | Literal["paused"] | Literal["ready"]
    download_error: str | None = None
    download: ImageDownload | None = None


class ImageDownload(ApiModel):
    state: Literal["downloading"] | Literal["loading"] | Literal["done"] | Literal["docker"]
    total_bytes: float
    done_bytes: float
    rate_bps: float
    images_total: float
    images_done: float
    connections: float
    updated_at: str


class InstallBatchError(ApiModel):
    code: str | None = None
    message: str | None = None


class InstallBatch(ApiModel):
    id: str
    actor: str
    state: (
        Literal["queued"]
        | Literal["running"]
        | Literal["paused"]
        | Literal["succeeded"]
        | Literal["cancelled"]
        | Literal["completed_with_failures"]
        | Literal["resetting"]
        | Literal["reset_failed"]
        | Literal["reset"]
    )
    current_ordinal: float
    parallel_downloads: float
    created_at: str
    updated_at: str
    error: InstallBatchError | None = None
    items: list[InstallBatchItem]


class InstallBatchEvent(ApiModel):
    id: float
    job_id: str
    event: str
    created_at: str
    detail: str


class InstallBatchJob(ApiModel):
    id: str
    state: str
    step_id: str
    detail: str
    events: list[InstallBatchEvent]


class InstallBatchResponse(ApiModel):
    ok: bool
    batch: InstallBatch | None
    current_job: InstallBatchJob | None = None
    reset: bool | None = None


class AppSize(ApiModel):
    download_bytes: float
    needed_bytes: float
    disk_bytes: float
    needed_disk_bytes: float


class AppSizesResponseAppsValue(AppSize):
    updated_at: str
    complete: bool


class AppSizesResponse(ApiModel):
    ok: bool = True
    apps: dict[str, AppSizesResponseAppsValue]
    selection: AppSize
    measured: bool
    free_bytes: float


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ServiceActionRequest(RequestModel):
    action: Literal[
        "install",
        "retry_setup",
        "start",
        "stop",
        "restart",
        "uninstall",
        "uninstall_delete_data",
        "backup",
        "restore",
        "update",
        "recover",
    ]
    confirm: str = ""
    snapshot_id: str = ""


class ConfigurationRequest(RequestModel):
    values: dict[str, str | bool | int | None] = Field(default_factory=dict)


class ComputeRequest(RequestModel):
    compute_mode: Literal["auto", "cpu", "nvidia", "amd"]


class ProviderRequest(RequestModel):
    provider_id: str = ""
    label: str = ""
    api_key: str = Field(min_length=1, max_length=4096, repr=False)


class VaultSetupRequest(RequestModel):
    email: str = ""
    master_password: str = Field(min_length=1, max_length=4096, repr=False)
    totp: str = Field(default="", repr=False)


class PersonRequest(RequestModel):
    name: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=1, max_length=256)
    role: Literal["member", "admin"] = "member"


class PersonChangeRequest(RequestModel):
    role: Literal["member", "admin", ""] = ""


class CalendarConnectRequest(RequestModel):
    username: str = Field(min_length=1, max_length=256)
    app_password: str = Field(min_length=1, max_length=4096, repr=False)


class CalendarSelectRequest(RequestModel):
    calendar_id: str


class CalendarEventRequest(RequestModel):
    title: str = Field(min_length=1, max_length=256)
    start: str
    end: str
    all_day: bool = False
    revision: str = ""


class CalendarDeleteRequest(RequestModel):
    revision: str = ""


class InstallBatchRequest(RequestModel):
    service_ids: list[str] = Field(min_length=1, max_length=100)
    parallel_downloads: int = Field(default=3, ge=1, le=8)


class BatchOrderRequest(RequestModel):
    service_ids: list[str]


class ToolSwitchRequest(RequestModel):
    enabled: bool


class ToolPermissionRequest(RequestModel):
    permission: Literal["auto", "needs_approval", "disabled"]


class ToolCallRequest(RequestModel):
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    confirmation_token: str = ""


class QueuedJob(ApiModel):
    id: str
    state: str
    created_at: str | None = None


class JobResponse(ApiModel):
    ok: bool
    job: QueuedJob
    duplicate: bool | None = None


class SessionResponse(ApiModel):
    ok: bool
    csrf_token: str


class ChecklistRequest(RequestModel):
    items: dict[Literal["devices", "extension", "chat", "hidden"], bool] = Field(default_factory=dict)


class ChecklistResponse(ApiModel):
    ok: bool
    items: dict[str, bool]


class SnapshotResponse(ApiModel):
    services: ServicesResponse
    system: SystemResponse
    jobs: JobsResponse
    identity: IdentityResponse
    chat: ChatStatus
    catalog: CatalogResponse
    core: CoreSetupResponse
    provisioning: ProvisioningResponse
    audit: AuditResponse


class ParallelDownloadsRequest(RequestModel):
    parallel_downloads: int = Field(ge=1, le=8)


class OkResponse(ApiModel):
    ok: bool
    warning: str | None = None
    message: str | None = None
    id: str | None = None


class PersonAccess(ApiModel):
    kind: Literal["deactivated", "demoted"]
    state: Literal["pending", "complete"]
    pending: int
    pending_targets: list[str]
    detail: str
    since: str


class Person(ApiModel):
    username: str
    uid: str
    name: str
    email: str
    role: Literal["admin", "member", ""]
    active: bool
    last_login: str
    access: PersonAccess | None = None


class PeopleResponse(ApiModel):
    ok: bool
    people: list[Person]


class PersonResponse(ApiModel):
    ok: bool
    person: Person
    invite: PersonInvite | None = None


class ProviderCatalogResponse(ApiModel):
    ok: bool
    providers: list[ProviderCatalogItem]
    recommended_minimum: int


class ProviderModelsResponse(ApiModel):
    ok: bool
    provider_id: str
    models: list[str]
    examples: bool


class JobEventsResponse(ApiModel):
    ok: bool
    events: list[AuditEvent]


class ServiceInitializationResponse(ApiModel):
    ok: bool
    initialization: ServiceInitialization


class McpServerResponse(ApiModel):
    ok: bool
    server: McpServer


class McpToolsResponse(ApiModel):
    ok: bool
    server_id: str
    state: str
    tools: list[McpServerToolsItem]


class McpCall(ApiModel):
    id: int
    server_id: str
    tool_name: str
    source: str
    actor: str
    outcome: str
    duration_ms: int
    created_at: str
    detail: str
    source_ref: str | None = None


class McpActivityResponse(ApiModel):
    ok: bool
    server_id: str
    calls: list[McpCall]


class McpJobsResponse(ApiModel):
    ok: bool
    server_id: str
    jobs: list[Job]


class McpUpdatesResponse(ApiModel):
    ok: bool
    server_id: str
    current_version: str
    reviewed_update: dict[str, str] | None
    detail: str


class McpLogsResponse(ApiModel):
    ok: bool
    server_id: str
    lines: list[str]


class ToolPermissionResponse(ApiModel):
    ok: bool
    server_id: str
    tool_name: str
    permission: str


class ToolPrepareResponse(ToolPermissionResponse):
    risk: str
    confirmation_required: bool
    confirmation_token: str
    expires_in_seconds: int


class ToolExecuteResponse(ApiModel):
    ok: bool
    result: dict[str, JsonValue]
    outcome: str


class VoiceKeyStatus(ApiModel):
    ok: bool
    available: bool  # a speech app is installed
    exists: bool
    created_at: str = ""
    base_url: str = ""
    models: list[str]


class VoiceKeyCreated(ApiModel):
    ok: bool
    key: str
    base_url: str
    models: list[str]


class ChatConnectResponse(ApiModel):
    ok: bool
    verification_uri_complete: str
    user_code: str
    interval: int


class ChatPollResponse(ApiModel):
    ok: bool
    state: str
    interval: int | None = None
    ready: bool | None = None
    detail: str | None = None


class VaultSyncResponse(ApiModel):
    ok: bool
    automatic: VaultAutomatic


class PersonInvite(ApiModel):
    url: str
    valid_hours: int


PersonResponse.model_rebuild()


class ApprovalOperation(ApiModel):
    id: str
    subject: str
    provider: str
    credential_version: int
    app: str
    server: str
    tool: str
    connector_revision: str
    policy_revision: int
    state: Literal[
        "pending", "approved", "dispatching", "succeeded", "failed", "outcome_unknown", "rejected", "revoked", "expired"
    ]
    created_at: float
    expires_at: float
    dispatch_at: float
    completed_at: float
    arguments: dict[str, JsonValue] | None = None


class ApprovalResponse(ApiModel):
    ok: bool
    operation: ApprovalOperation
    execution: dict[str, JsonValue] | None = None


class ApprovalsResponse(ApiModel):
    ok: bool
    operations: list[ApprovalOperation]


class ApprovalDecisionRequest(RequestModel):
    decision: Literal["approve", "reject"]
