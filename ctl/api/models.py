"""Public HTTP contracts. Python models are the source of dashboard API types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

DisplayState = Literal["running", "stopped", "working", "checking", "not_installed", "needs_setup", "needs_attention"]


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


class ServiceLink(ApiModel):
    """Another private address of an app (``extra_routes``), shown beside its Open button."""

    label: str
    url: str


class ServiceExtraRoute(ApiModel):
    name: str
    label: str
    private_https_port: float


class PortEntry(ApiModel):
    # tailnet: opened over Tailscale; home: on the home network; loopback: this server only.
    kind: Literal["tailnet", "home", "loopback"]
    label: str
    address: str
    port: int
    protocol: Literal["tcp", "udp"]
    # The port inside the app's container, when its Compose file maps one.
    container_port: int | None = None
    note: str = ""


class AppPorts(ApiModel):
    id: str
    name: str
    entries: list[PortEntry]


class PortsResponse(ApiModel):
    ok: bool
    tailnet_dns_name: str
    home_ip: str
    apps: list[AppPorts]


class HomeWidget(ApiModel):
    id: str
    # Mu3Lab's own (weather, calendar…) or an app's, as "<app>:<widget>" (ctl/app_widgets.py).
    kind: str
    # Column and row of its top-left corner, and how many columns and rows it takes.
    x: int
    y: int
    w: int
    h: int
    # Left out of the one-column board a phone shows.
    hide_on_phone: bool = False
    # The person's own name for the widget ("" = its kind's name), and no title bar outside edit mode.
    title: str = ""
    hide_title: bool = False
    # Each kind's own settings (ctl/home_board.py checks them): weather {units, days, place}, stocks
    # {symbols, range}, calendar {view, week_start}, an app's {show}, a group's {title, collapsed}.
    settings: dict[str, JsonValue] = {}
    # A group's widgets, placed on its own grid (columns and rows from the group's top-left).
    children: list[HomeWidget] = []


class HomeWidgetKind(ApiModel):
    kind: str
    min_w: int
    min_h: int
    max_w: int
    max_h: int
    w: int
    h: int
    # Set for an app's widget (Mu3Lab's own are described by the dashboard).
    title: str | None = None
    description: str | None = None
    app_id: str | None = None
    app_name: str | None = None
    refresh_seconds: int | None = None
    # Shows each person's own figures.
    personal: bool | None = None
    # A key the person pastes in the widget's settings, when Mu3Lab cannot make one.
    key_label: str | None = None
    key_help: str | None = None
    # How the dashboard draws an app's widget: figures and a list, or a view of its own.
    view: str = "figures"


class HomeWidgetStat(ApiModel):
    label: str
    value: str
    tone: Literal["", "good", "warn", "bad"] = ""


class HomeWidgetItem(ApiModel):
    title: str
    subtitle: str = ""
    meta: str = ""
    progress: float | None = None
    tone: Literal["", "good", "warn", "bad"] = ""
    url: str = ""
    # A tick box: whether it is done, and what ticking it asks the app.
    check: bool | None = None
    action: str = ""


class HomeWidgetAction(ApiModel):
    action: str = Field(min_length=1, max_length=120)
    # A view's change (the note's text) and that widget's settings on the board (which document).
    data: dict[str, JsonValue] = {}
    settings: dict[str, JsonValue] = {}


class HomeWidgetActionResult(ApiModel):
    ok: bool = True
    # What the app answered, for a view (a new document's id).
    result: dict[str, JsonValue] = {}


class HomeWidgetKeyRequest(ApiModel):
    # Empty removes the saved key.
    key: str = Field(max_length=4000)


class HomeWidgetKeyStatus(ApiModel):
    ok: bool = True
    saved: bool


class HomeWidgetData(ApiModel):
    """An app widget's figures, or why there are none (ctl/app_widgets.py)."""

    ok: bool = True
    state: Literal["ok", "not_installed", "starting", "stopped", "setup", "error"]
    detail: str = ""
    app_id: str
    app_name: str
    # When the figures were read (seconds since 1970); 0 when there are none.
    updated_at: float = 0
    # The figures are the last ones read, kept while the app is stopped or cannot answer (``state``
    # and ``detail`` say why); ticking a box is not possible until it answers again.
    saved: bool = False
    stats: list[HomeWidgetStat] = []
    items: list[HomeWidgetItem] = []
    items_title: str = ""
    empty: str = ""
    note: str = ""
    # A view's own data (manifest ``view``): a note's text, a slideshow's photos.
    view: dict[str, JsonValue] = {}


class HomeBoardTab(ApiModel):
    # Empty in a request: a new board.
    id: str = Field(default="", max_length=40)
    name: str = Field(max_length=60)


class HomeBoard(ApiModel):
    ok: bool = True
    widgets: list[HomeWidget]
    # False until the person changes their board: they see the starter set.
    customized: bool
    # How many columns the person chose; the kinds' sizes are for base_columns.
    columns: int
    column_choices: list[int]
    base_columns: int
    # free: widgets stay where they are put (gaps allowed); gravity: they pack to the top.
    placement: Literal["free", "gravity"]
    kinds: list[HomeWidgetKind]
    # Which of the person's boards this is, and all of them (tabs), in their order.
    tab: str = "home"
    tabs: list[HomeBoardTab] = []


class HomeBoardRequest(ApiModel):
    widgets: list[dict[str, JsonValue]]
    columns: int = 12
    placement: Literal["free", "gravity"] = "free"
    # The board (tab) saved; empty = the first.
    tab: str = Field(default="", max_length=40)


class HomeBoardTabsRequest(ApiModel):
    """Every board the person keeps, in order: renamed, moved, new (no id) or left out (removed)."""

    tabs: list[HomeBoardTab] = Field(min_length=1, max_length=12)


class HomeBoardTabs(ApiModel):
    ok: bool = True
    tabs: list[HomeBoardTab]


class WeatherDay(ApiModel):
    date: str
    code: int
    high: float | None = None
    low: float | None = None
    rain_chance: float | None = None


class WeatherResponse(ApiModel):
    ok: bool = True
    units: Literal["celsius", "fahrenheit"]
    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = None
    wind: float | None = None
    wind_unit: str
    code: int
    is_day: bool
    days: list[WeatherDay]


class Place(ApiModel):
    name: str
    region: str
    latitude: float
    longitude: float


class PlacesResponse(ApiModel):
    ok: bool = True
    places: list[Place]


class StockQuote(ApiModel):
    symbol: str
    found: bool
    name: str = ""
    price: float | None = None
    change: float | None = None
    change_percent: float | None = None
    currency: str = ""
    points: list[float] = []


class StocksResponse(ApiModel):
    ok: bool = True
    quotes: list[StockQuote]


class Mover(ApiModel):
    symbol: str
    name: str = ""
    price: float | None = None
    change_percent: float | None = None
    currency: str = ""


class MoversResponse(ApiModel):
    ok: bool = True
    movers: list[Mover]


class RatesResponse(ApiModel):
    ok: bool = True
    base: str
    # The day the European Central Bank published them.
    date: str
    rates: dict[str, float]


class FeedItem(ApiModel):
    title: str
    url: str
    summary: str = ""
    # Seconds since 1970; 0 when the feed does not say.
    published: float = 0


class Feed(ApiModel):
    url: str
    title: str = ""
    items: list[FeedItem] = []
    # Why this feed shows nothing ("" when it was read).
    error: str = ""


class FeedsResponse(ApiModel):
    ok: bool = True
    feeds: list[Feed]


class ThermostatStatus(ApiModel):
    ok: bool = True
    paired: bool
    name: str = ""
    units: Literal["celsius", "fahrenheit"] = "celsius"
    # Temperatures in Celsius, as HomeKit reports them; the dashboard shows the thermostat's own unit.
    current: float | None = None
    humidity: float | None = None
    target: float | None = None
    heat_to: float | None = None
    cool_to: float | None = None
    mode: Literal["off", "heat", "cool", "auto"] = "off"
    state: Literal["idle", "heating", "cooling"] = "idle"
    modes: list[Literal["off", "heat", "cool", "auto"]] = []
    minimum: float = 10
    maximum: float = 33
    step: float = 0.5


class ThermostatChange(ApiModel):
    mode: Literal["off", "heat", "cool", "auto"] | None = None
    target: float | None = Field(default=None, ge=0, le=50)
    heat_to: float | None = Field(default=None, ge=0, le=50)
    cool_to: float | None = Field(default=None, ge=0, le=50)


class HomeKitDevice(ApiModel):
    id: str
    name: str
    model: str
    paired: bool


class HomeKitDevices(ApiModel):
    ok: bool = True
    devices: list[HomeKitDevice]


class ThermostatPairRequest(ApiModel):
    device_id: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=8, max_length=12)


class CameraSettings(ApiModel):
    ok: bool = True
    configured: bool
    name: str
    host: str
    port: int
    username: str
    # The password is never sent back; this says whether one is saved.
    has_password: bool
    sd_path: str
    hd_path: str


class CameraSettingsRequest(ApiModel):
    name: str = Field(default="Front door", max_length=40)
    host: str = Field(max_length=253)
    port: int = Field(default=554, ge=1, le=65535)
    username: str = Field(default="", max_length=80)
    # Blank keeps the saved password.
    password: str = Field(default="", max_length=200)
    sd_path: str = Field(default="", max_length=120)
    hd_path: str = Field(default="", max_length=120)


class AskRequest(ApiModel):
    question: str


class AskChatApp(ApiModel):
    id: str
    name: str


class AskChat(ApiModel):
    title: str
    url: str
    # When the chat was last used (seconds since 1970); 0 when the app does not say.
    updated_at: float = 0


class AskChats(ApiModel):
    """The Ask widget's chat switch: the installed chat apps (the household's first) and, for the one
    chosen, the person's own newest chats there (ctl/chat_recent.py)."""

    ok: bool = True
    apps: list[AskChatApp]
    app_id: str = ""
    app_name: str = ""
    # The household's chat app, which the Chat page shows.
    household_id: str = ""
    chats: list[AskChat] = []
    # Why there are no chats to show, when that is not simply that there are none yet.
    detail: str = ""
    # Opens the app with a question typed in ("{question}" once encoded, "{question2}" twice).
    prompt_url: str = ""


class SearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=500)
    # web: the web search app (SearXNG). fetch: the page reader (Firecrawl): a web address is read,
    # words are a deep search (ctl/home_search.py).
    mode: Literal["web", "fetch"] = "web"


class SearchResult(ApiModel):
    title: str
    url: str
    snippet: str = ""
    # Firecrawl read the page: the snippet is its own opening text, not a search engine's summary.
    read: bool = False


class SearchPage(ApiModel):
    title: str
    url: str
    text: str


class SearchResponse(ApiModel):
    ok: bool = True
    mode: Literal["web", "deep", "page"]
    # SearXNG's instant answer, when it has one.
    answer: str = ""
    results: list[SearchResult] = []
    page: SearchPage | None = None


class SearchModes(ApiModel):
    ok: bool = True
    # Which halves of the Search widget work: whether the web search app (SearXNG) and the page
    # reader (Firecrawl) are installed, and their names for the switch.
    web: bool
    fetch: bool
    web_name: str = ""
    fetch_name: str = ""


class AskResponse(ApiModel):
    ok: bool = True
    answer: str


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
    """A restore or update that was interrupted; nothing continues until a person chooses."""

    kind: Literal["restore", "update"]
    detail: str
    since: str
    # An interrupted update whose new release was already in place may be kept instead of undone.
    can_keep: bool = False


class ServiceUpdate(ApiModel):
    repository: str
    installed_version: str
    # The release an update moves to (Mu3Lab's, or a newer one from the app's makers: ``source``).
    approved_version: str
    update_available: bool
    supporting_only: bool
    # Same release and images; Mu3Lab changed the app's own files since they were installed.
    files_only: bool = False
    # "mu3lab": the release Mu3Lab approves. "upstream": newer, from the app's makers, passed the checks here.
    source: Literal["mu3lab", "upstream"] = "mu3lab"


class UpstreamRelease(ApiModel):
    """The newest release this server found from the app's makers, and what Mu3Lab's checks made of it."""

    version: str
    # ready: offered. held: waits for Mu3Lab's review (reasons). current: nothing newer. unavailable: unreadable.
    state: str
    reasons: list[str] = Field(default_factory=list)
    checked_at: str
    release_url: str


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


class ServiceCompute(ApiModel):
    """For apps with a GPU build: the build that runs and the one the compute mode asks for."""

    running: str
    wanted: str


class Service(ApiModel):
    mobile: ServiceMobile0 | None = None
    id: str
    name: str
    category: str
    # The stack of apps it works with (Home groups them), or "".
    stack: str = ""
    # The Home widget it runs for, when it is a helper kept out of Apps and Discover, or "".
    serves_widget: str = ""
    lifecycle: Literal["always_on"] | Literal["shared"] | Literal["optional"]
    stop_warning: str = ""
    # Named parts of the app (Compose service -> label), shown with their state on its page.
    parts: dict[str, str] = Field(default_factory=dict)
    compute: ServiceCompute | None = None
    https_port: float
    private_https_port: float | None = None
    auth: Literal["oidc"] | Literal["proxy"] | Literal["trusted_header"] | Literal["local"] | Literal["excluded"]
    dependencies: list[str]
    stage: Literal["foundation"] | Literal["core"] | Literal["optional"]
    group: Literal["apps"] | Literal["ai"] | Literal["infrastructure"]
    routable: bool
    # Reached on the home network (http://<home address>:<port>): Jellyfin there only, Seerr
    # on the tailnet too (``routable``).
    home_network: bool = False
    extra_routes: list[ServiceExtraRoute] = Field(default_factory=list)
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
    # Its home-network address (http://<home address>:<port>), also when ``url`` is the tailnet one.
    home_url: str = ""
    route_ready: bool
    links: list[ServiceLink] = Field(default_factory=list)
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
    # Kopia's backup service is backing the app up right now (it keeps running).
    backing_up: bool = False
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
        | Literal["off"]
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
    # Send back as ``expected_revision`` so a stale form cannot overwrite a newer change.
    revision: int | None = None
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
    engine: str = "kopia"
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
    # Folder of an older restic repository Mu3Lab no longer uses; kept untouched.
    legacy_repository: str | None = None


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


class DiskShare(ApiModel):
    data: float
    program: float


class DiskUsage(ApiModel):
    measured_at: str
    # By owner: an app's id, "core", "removed" (apps no longer offered) or "unused" (images nothing runs).
    owners: dict[str, DiskShare]
    # Data folders removed apps left behind; "Free up space" deletes them only when asked.
    removed_folders: list[str] = Field(default_factory=list)
    # "core" part by part: platform app ids, "tailnet-relay" and "mu3lab" (Mu3Lab's own services).
    core_parts: dict[str, DiskShare] = Field(default_factory=dict)
    # What owners keep on other drives (SystemResponse.drives), by mount point; "owners" is the main drive only.
    drives: dict[str, dict[str, DiskShare]] = Field(default_factory=dict)


class Drive(ApiModel):
    """A drive besides the main one that holds Mu3Lab's data, measured live."""

    mount: str
    name: str
    # What Mu3Lab keeps there: "backups".
    role: str
    total: float
    used: float
    percent: float


class CleanupRequest(ApiModel):
    delete_data: bool = False


class GpuMemory(ApiModel):
    """Graphics memory of the NVIDIA GPUs, all cards added up, in bytes."""

    name: str
    total: float
    used: float
    # As memory_by_owner; use that is not a Mu3Lab container is left out.
    by_owner: dict[str, float] = Field(default_factory=dict)
    by_core_part: dict[str, float] = Field(default_factory=dict)


class SystemResponse(ApiModel):
    observed_at: str = ""
    ok: bool
    cpu_percent: float
    uptime_seconds: float | None = None
    docker_ready: bool
    worker_state: str | None = None
    container_memory: dict[str, float] | None = None
    # By owner: an app's id, "core" or "removed" (apps no longer offered).
    memory_by_owner: dict[str, float] | None = None
    # "core" part by part, as in DiskUsage.core_parts.
    memory_by_core_part: dict[str, float] | None = None
    disk_usage: DiskUsage | None = None
    # None without an NVIDIA GPU.
    gpu: GpuMemory | None = None
    tailnet_dns_name: str
    tailscale: TailscaleStatus | None = None
    runtime_root: str
    memory: Metric
    # The drive Mu3Lab's own folder is on.
    disk: Metric
    drives: list[Drive] = Field(default_factory=list)
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


class CoreAppProgress(ApiModel):
    service_id: str
    name: str
    state: Literal["waiting", "downloading", "downloaded", "installing", "ready", "failed"]
    download: ImageDownload | None = None


class CoreSetupResponse(ApiModel):
    ok: bool
    ready_to_run: bool
    services: list[str]
    missing_manifests: list[str]
    current_job: Job | None = None
    # Each core app's progress while the core setup job runs, in install order.
    apps: list[CoreAppProgress] | None = None
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
    # Set when a local edit met a change made in Nextcloud; the person chooses which to keep.
    conflict: str | None = None


class CalendarEventsCalendar(ApiModel):
    id: str
    name: str


class CalendarEvents(ApiModel):
    ok: bool
    state: str
    calendar: CalendarEventsCalendar | None = None
    fetched_at: str | None = None
    nextcloud_ready: bool | None = None
    # Events kept on Mu3Lab are waiting to be sent; POST /calendar/sync sends them.
    pending_upload: bool | None = None
    events: list[CalendarEvent]
    error: str | None = None


class CalendarSync(ApiModel):
    """What one sync sent, and what it could not send without asking."""

    ok: bool
    moved: int
    conflicts: list[str]
    kept: list[str]


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
    # A queued job's turn among all waiting jobs (1 is next); none once it runs.
    queue_position: int | None = None


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


class JobEvent(ApiModel):
    """One line of a job's progress. Unlike an audit event it names no actor: the job does."""

    id: float
    job_id: str | None
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
    events: list[JobEvent]
    # Image download progress while an install job downloads.
    download: ImageDownload | None = None


class BackupSnapshot(ApiModel):
    id: str
    short_id: str
    time: str
    # "backup" for those Kopia's backup service makes; "pre-update" for an update's own.
    # Older backups may say "manual", "scheduled", "platform" or "pre-restore".
    reason: str
    version: str
    # The exact deployment the data belongs to; empty for backups made before release ids.
    release_id: str = ""
    paths: list[str]
    complete: bool = True


class BackupProtection(ApiModel):
    """One app's backup evidence; never inferred from another app's backups."""

    state: Literal["missing", "stale", "unchecked", "checked"]
    snapshot_id: str = ""
    completed_at: str = ""
    verification: Literal["none", "structure", "data"] = "none"
    restore_tested_at: str = ""
    off_device: bool = False
    detail: str = ""


class CoreAppBackup(ApiModel):
    id: str
    name: str
    included: bool
    # Why an app is left out (its data can be fetched again).
    reason: str = ""
    protection: BackupProtection | None = None


class RecoverySheetStatus(ApiModel):
    """When an administrator last opened the recovery sheet; never its contents."""

    opened_at: str
    opened_by: str


class PlatformBackupStatus(ApiModel):
    """Mu3Lab's own state and each core app, each from its own backup evidence."""

    ok: bool
    platform: BackupProtection
    core_apps: list[CoreAppBackup]
    recovery_sheet: RecoverySheetStatus | None = None


BackupFrequency = Literal["off", "6h", "12h", "daily", "weekly"]


class BackupSchedule(ApiModel):
    """When Kopia's backup service runs backups, in this server's local time. Weekday: Monday is 0, Sunday 6."""

    ok: bool
    frequency: BackupFrequency
    weekday: int
    time: str
    # From Kopia itself; empty while scheduled backups are off or the service is not running.
    next_run_at: str
    service_running: bool
    # False until Kopia backups (the app) is installed; nothing runs on a schedule without it.
    service_installed: bool = True
    # Sources whose last backup did not finish, with why.
    problems: list[str]


class BackupDrive(ApiModel):
    """A place the backups can live: the main drive's own folder, or a folder on another mounted drive."""

    id: str
    name: str
    # The drive's model, when the system knows it.
    detail: str
    mount: str
    path: str
    size_bytes: int
    free_bytes: int
    current: bool
    # On the same physical drive as the apps' data: one drive failure loses both.
    same_drive_as_data: bool
    # Mounted only while someone is signed in to the desktop; night-time backups would miss it.
    desktop_only: bool
    # Why it can't be chosen; empty when it can.
    problem: str


class BackupLocation(ApiModel):
    """Where the backups are, the drives they could move to, and the latest move."""

    ok: bool
    path: str
    # Empty unless the chosen drive is missing (nothing is backed up until it is back).
    problem: str
    used_bytes: int
    drives: list[BackupDrive]
    move: Job | None = None


class BackupStarted(ApiModel):
    """Kopia's backup service was asked to back up now; progress shows on the app."""

    ok: bool
    detail: str


class BackupHookResponse(ApiModel):
    ok: bool
    detail: str


AiPrivacy = Literal["outside_allowed", "local_first", "local_only"]
AiSource = Literal["local", "outside"]


class LocalModel(ApiModel):
    """One reviewed local model. Sizes are Ollama's download sizes; memory is an estimate."""

    id: str
    name: str
    tag: str
    download_gb: float
    memory_gb: float
    note: str
    dimensions: int
    # How its weights are stored (Q4 for every chat model), which the memory estimate assumes.
    precision: str
    # Downloaded in the model runner that serves requests now.
    downloaded: bool
    # Used by a setting now (chat, which voice follows, or search), so it cannot be removed.
    in_use: bool = False
    # Billions of parameters, and the group the dashboard lists it in: xsmall (under 5), small (5 to 10),
    # medium (10 to 18) or large (18 and over).
    params_b: float = 0.0
    size_group: Literal["xsmall", "small", "medium", "large"] = "xsmall"
    # How it fits this computer beside the other apps (ctl/model_fit.py): good, slow or too_big; gpu or cpu.
    fit: str = ""
    fit_where: str = ""


class AiModelSettings(ApiModel):
    # Where chat may run, in order: the first answers by default, the second is a choice in chat.
    sources: list[AiSource]
    # The same order in three words: outside_allowed, local_first or local_only.
    privacy: AiPrivacy
    # Kept while local models are off, so turning them back on needs no new choice.
    chat_model: str
    embedding_model: str
    # The chosen model runner's app id; empty means the default (the one the installer sets up).
    runner: str
    changed_at: str
    changed_by: str


class ModelRunnerOption(ApiModel):
    id: str
    name: str
    installed: bool
    # Whether it has a web app of its own (Ollama does not).
    has_ui: bool


class AiModels(ApiModel):
    """Where AI requests may go, the model runner, the local chat model and the search (embedding) model."""

    ok: bool
    settings: AiModelSettings
    # The reviewed chat model that fits best, or "" to suggest outside providers; and why.
    suggested: str = ""
    suggestion_reason: str = ""
    chat_models: list[LocalModel]
    embedding_models: list[LocalModel]
    # The runner that serves requests now, and every runner Mu3Lab knows.
    runner: str
    runners: list[ModelRunnerOption]
    runner_reachable: bool
    # Stopped from Home: trying, removing or switching a model starts it for that and stops it again.
    runner_stopped: bool
    # Installed apps whose search index blocks changing the embedding model.
    embedding_locked_by: list[str]
    active_job_id: str


class AiModelsRequest(ApiModel):
    sources: list[AiSource] = Field(min_length=1, max_length=2)
    chat_model: str = Field(default="", max_length=64)
    embedding_model: str = Field(max_length=64)
    runner: str = Field(default="", max_length=64)


class AiTryRequest(ApiModel):
    # A local chat model's id, or "outside" for the outside providers.
    target: str = Field(min_length=1, max_length=64)
    prompt: str = Field(default="", max_length=2000)


class AiTryResult(ApiModel):
    ok: bool
    where: str
    name: str
    answer: str
    seconds: float
    loading_seconds: float
    tokens_per_second: float
    # The model runner was stopped from Home, so it was started for this answer and stopped again.
    runner_started: bool = False


class VoiceApp(ApiModel):
    """An app assistant the voice assistant can hand people to, and whether it may."""

    id: str
    title: str
    description: str
    operators_only: bool
    on: bool
    # Whether the app's connector is live now; a stopped app's tools wait until it starts.
    ready: bool = True


class VoiceApps(ApiModel):
    ok: bool
    # Whether the voice app is installed; the switches matter only then.
    installed: bool
    apps: list[VoiceApp]


class VoiceAppRequest(ApiModel):
    on: bool


VoiceResponsiveness = Literal["fast", "balanced", "patient"]


class VoiceSettings(ApiModel):
    """The household's voice assistant choices (ctl/voice_settings.py)."""

    # How long the assistant waits when someone sounds unfinished.
    responsiveness: VoiceResponsiveness
    # Whether talking over the assistant stops it.
    interruptions: bool
    # The assistant's voice; "" uses Speaches' default voice.
    voice: str
    # Web search for current facts; it works once SearXNG is installed.
    search: bool
    # Said when an answer's first words take longer than wait_after seconds; "" is off.
    wait_phrase: str = ""
    wait_after: float = 1.5
    # After this many seconds with nobody speaking, check on the person, then end the call; 0 is off.
    silence_after: float = 30
    silence_reasks: int = 1
    silence_goodbye: str = ""
    # Each call's transcript and timing (and audio) kept for reports_days, for administrators.
    reports: bool = True
    reports_audio: bool = True
    reports_days: int = 14
    # How speech becomes text: Whisper (Speaches), or a streaming model in LiveKit's own recognizer.
    stt_engine: Literal["whisper", "kroko", "zipformer"] = "whisper"
    # Advanced turn timing, in seconds; 0 means the default (Responsiveness, or the detector's own).
    vad_silence: float = 0.25
    min_delay: float = 0.25
    max_delay: float = 0
    eot_threshold: float = 0
    interrupt_after: float = 0.5
    changed_at: str
    changed_by: str


class VoiceSettingsRequest(ApiModel):
    responsiveness: VoiceResponsiveness | None = None
    interruptions: bool | None = None
    voice: str | None = Field(default=None, max_length=40)
    search: bool | None = None
    wait_phrase: str | None = Field(default=None, max_length=80)
    wait_after: float | None = Field(default=None, ge=0.5, le=10)
    silence_after: float | None = Field(default=None, ge=0, le=600)
    silence_reasks: int | None = Field(default=None, ge=0, le=3)
    silence_goodbye: str | None = Field(default=None, max_length=160)
    reports: bool | None = None
    reports_audio: bool | None = None
    reports_days: int | None = Field(default=None, ge=1, le=90)
    stt_engine: Literal["whisper", "kroko", "zipformer"] | None = None
    vad_silence: float | None = Field(default=None, ge=0.1, le=1.5)
    min_delay: float | None = Field(default=None, ge=0.25, le=2)
    max_delay: float | None = Field(default=None, ge=0, le=6)
    eot_threshold: float | None = Field(default=None, ge=0, le=0.99)
    interrupt_after: float | None = Field(default=None, ge=0.2, le=2)


class VoiceReportSummary(ApiModel):
    id: str
    # The caller's name, or their sign-in when Mu3Lab has no name for them.
    who: str
    started_at: float
    seconds: float
    audio: bool


class VoiceReports(ApiModel):
    """Kept call reports, newest first (ctl/voice_reports.py); administrators only."""

    ok: bool = True
    reports: list[VoiceReportSummary]


class VoiceReportLine(ApiModel):
    # said: a message; tool, tool_result, tool_error: what the assistant asked an app and got back.
    kind: Literal["said", "tool", "tool_result", "tool_error"]
    who: Literal["person", "assistant"]
    text: str
    interrupted: bool = False
    at: float = 0
    # Seconds from the end of the person's speech to the reply's first sound.
    reply_after: float | None = None


class VoiceReport(VoiceReportSummary):
    ok: bool = True
    lines: list[VoiceReportLine]


class VoiceReportAudio(ApiModel):
    ok: bool = True
    # A data: address the dashboard plays and saves.
    audio: str


class VoiceOption(ApiModel):
    id: str
    name: str
    accent: str
    gender: str


class SpeechModelOption(ApiModel):
    id: str
    name: str
    note: str
    download_gb: float
    # An estimate of the memory it takes while loaded, at the precision its weights are stored.
    memory_gb: float
    precision: str
    # In Speaches' model folder now, and whether Speaches is set up to use it.
    downloaded: bool
    in_use: bool


class SpeechModelChoice(ApiModel):
    value: str
    options: list[SpeechModelOption]


class VoiceSpeech(ApiModel):
    """Speaches' models, which every app using speech shares."""

    app_id: str
    installed: bool
    # Speech recognition (Whisper) and the voice model (Kokoro).
    stt: SpeechModelChoice
    tts: SpeechModelChoice
    default_voice: str


class VoicePage(ApiModel):
    ok: bool
    # Whether LiveKit, the voice app, is installed.
    installed: bool
    settings: VoiceSettings
    speech: VoiceSpeech
    # The English voices Speaches can speak with now; empty while it is not answering.
    voices: list[VoiceOption]
    # Whether the household's SearXNG app is installed; web search needs it.
    search_installed: bool
    search_app_id: str
    # A running change of the speech models (Speaches' setup), or "".
    active_job_id: str


class VoiceSpeechRequest(ApiModel):
    stt_model: str = Field(min_length=1, max_length=200)
    tts_model: str = Field(min_length=1, max_length=200)


class SpeechModelRemoveRequest(ApiModel):
    model: str = Field(min_length=1, max_length=200)


class VoiceSampleRequest(ApiModel):
    voice: str = Field(min_length=1, max_length=40)


class VoiceSample(ApiModel):
    ok: bool
    # One spoken sentence as base64 MP3.
    audio: str


class VoiceToolCheck(ApiModel):
    id: str
    ok: bool
    tools: int
    detail: str


class VoiceToolChecks(ApiModel):
    ok: bool
    # Checked with the viewer's own keys, as a call of theirs would; empty when they have none.
    apps: list[VoiceToolCheck]


class RecoverySheet(ApiModel):
    """What a new computer needs to open the backups. Sent once, never cached."""

    ok: bool
    server: str
    generated_at: str
    version: str
    password: str
    password_check: str
    repository: str
    kopia_image: str
    copies: list[str]


class BackupsResponse(ApiModel):
    ok: bool
    service_id: str
    backups: list[BackupSnapshot]
    protection: BackupProtection | None = None
    readiness: BackupReadiness


class Mu3LabUpdateResult(ApiModel):
    """How the last update ended: verified, rolled_back or needs_attention."""

    state: str
    at: str
    detail: str
    # Release names, deliberately not called from/to (a Python keyword).
    from_: str = Field("", alias="from")
    to: str = ""


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
    # The dashboard's code changed since its page was built: Restart Mu3Lab rebuilds it first.
    dashboard_outdated: bool = False
    last_update: Mu3LabUpdateResult | None = None


class Mu3LabDevelopment(ApiModel):
    """One merged branch or pull request (or a run of direct commits) inside a release."""

    title: str
    branch: str = ""
    date: str
    changes: list[str]


class Mu3LabRelease(ApiModel):
    """One release's changes; a development copy has no version."""

    version: str
    date: str
    commit: str = ""
    changes: list[str]
    # The same changes grouped by development, newest first.
    developments: list[Mu3LabDevelopment] = Field(default_factory=list)


class Mu3LabChangelog(ApiModel):
    ok: bool
    releases: list[Mu3LabRelease]


class UpdateResponse(ApiModel):
    ok: bool
    repository: str
    installed_version: str
    approved_version: str
    release_url: str
    update_available: bool
    supporting_only: bool
    files_only: bool = False
    update_enabled: bool
    blocked_reason: str
    added_services: list[str] = Field(default_factory=list)
    removed_services: list[str] = Field(default_factory=list)
    source: Literal["mu3lab", "upstream"] = "mu3lab"
    upstream: UpstreamRelease | None = None


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
    parameters: dict[str, JsonValue] | None = None


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
    # Where operators choose this connector's tools: its virtual server in ContextForge.
    manage_url: str | None = None


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
    status: Literal[
        "ready", "paused", "pending", "failed", "not_connected", "connector_down", "needs_review", "operator_only"
    ]
    detail: str


class ChatStatus(ApiModel):
    assistants: list[ChatAssistant] | None = None
    connected: bool | None = None
    # The household chat app the Chat page shows.
    app_id: str = ""
    app_name: str = ""
    ok: bool
    ready: bool
    url: str
    authentication: str
    mcp_enabled_count: float
    detail: str
    providers: list[ChatProvider] | None = None
    # Opens the chat app with a question typed in: replace "{question}" with the question,
    # URL-encoded twice (it travels inside another address). Empty when the app cannot.
    prompt_url: str = ""


class ChatAppChoice(ApiModel):
    id: str
    name: str
    installed: bool
    selected: bool


class ChatApps(ApiModel):
    ok: bool
    apps: list[ChatAppChoice]


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


class BackupScheduleRequest(RequestModel):
    frequency: BackupFrequency
    weekday: int = Field(ge=0, le=6)
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class BackupLocationRequest(RequestModel):
    # A drive's id from GET /system/backups/location: "default" or its mount point.
    target: str = Field(min_length=1, max_length=4096)


class BackupHookRequest(RequestModel):
    source: str = Field(max_length=80)


class CalendarResolveRequest(RequestModel):
    keep: Literal["mine", "server"]


class ServiceActionRequest(RequestModel):
    action: Literal[
        "install",
        "retry_setup",
        "start",
        "stop",
        "restart",
        "uninstall",
        "uninstall_delete_data",
        "restore",
        "update",
        "recover",
        "keep_update",
        "switch_compute",
    ]
    confirm: str = ""
    snapshot_id: str = ""
    # Start and restart: which of the stopped apps this one needs start with it. Left out, all of them;
    # the dashboard asks first and the person may leave some stopped.
    dependencies: list[str] | None = Field(default=None, max_length=50)


class ConfigurationRequest(RequestModel):
    values: dict[str, str | bool | int | None] = Field(default_factory=dict)
    expected_revision: int | None = None


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


class QueuedJob(ApiModel):
    id: str
    state: str
    created_at: str | None = None


class JobResponse(ApiModel):
    ok: bool
    job: QueuedJob
    duplicate: bool | None = None


class JobCancelResponse(ApiModel):
    ok: bool
    # "cancelled", or "cancelling" while the worker finishes the command it is in.
    state: Literal["cancelled", "cancelling"]


class UpdatesCancelResponse(ApiModel):
    ok: bool
    # Apps whose waiting or downloading update stopped; nothing about them changed.
    cancelled: list[str]
    # Apps already switching versions: each finishes, or puts its old version back by itself.
    finishing: list[str]


class SessionResponse(ApiModel):
    ok: bool
    csrf_token: str


class ChatAppRequest(RequestModel):
    app_id: str = Field(min_length=1, max_length=64)


class ChecklistRequest(RequestModel):
    items: dict[Literal["devices", "extension", "chat", "hidden"], bool] = Field(default_factory=dict)


class ChecklistResponse(ApiModel):
    ok: bool
    items: dict[str, bool]


class BackupAttention(ApiModel):
    """One backup problem Home shows administrators; empty while backups keep up with the schedule."""

    key: str
    message: str


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
    backup_attention: list[BackupAttention] = []
    # The app that runs local models now (Settings, AI): Ollama.
    model_runner: str = ""
    # Home warnings this person dismissed (ctl/dismissals.py), on every device.
    dismissed: list[str] = []


class DismissRequest(RequestModel):
    warning: str = Field(min_length=1, max_length=500)


class DismissedResponse(ApiModel):
    ok: bool = True
    dismissed: list[str]


class ParallelDownloadsRequest(RequestModel):
    parallel_downloads: int = Field(ge=1, le=8)


class OkResponse(ApiModel):
    ok: bool
    warning: str | None = None
    message: str | None = None
    id: str | None = None


class PowerStatus(ApiModel):
    """Whether Mu3Lab as a whole is on, and the progress of turning it off."""

    state: Literal["on", "turning_off", "off", "turning_on"]
    step: str | None = None
    done: int | None = None
    total: int | None = None
    since: str | None = None
    error: str | None = None
    # Why it cannot turn off now (a task is running); only administrators are told.
    blockers: list[str] | None = None


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
    events: list[JobEvent]


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
