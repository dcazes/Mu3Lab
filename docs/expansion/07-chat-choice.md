# 07. Chat app choice (LobeChat or Open WebUI) and Hermes Agent (Phase 3)

## 1. Goal

- The household chat app is **LobeChat or Open WebUI**, chosen at install and
  switchable later. Each app's assistant and its tools behave the same in both.
- **Hermes Agent** is an optional **owner-only** add-on agent beside either.
- The safety rule "changes ask first" holds in every case. Where a chat app or
  agent cannot ask by itself, the tool gateway asks through Mu3Lab (§4).

## 2. What exists today

LobeChat is wired in as the only chat app:

| File | Role |
|---|---|
| `apps/lobehub/` | Manifest (`tier: core`, `capabilities: [chat]`), compose, Caddy template, `hooks.py` (syncs assistants after healthy) |
| `ctl/integrations/lobehub.py` | `LobeHub` client: device-code login, per-person API key, agents and MCP servers |
| `ctl/lobehub_ops.py` | `origin()`, `desired_assistants()`, `assistant_report(uid)`, `sync_agents(log)`, `sync_gateway(...)`, `set_tool_permission(...)` |
| `ctl/chat_connections.py` | Encrypted per-person chat approvals and keys |
| `ctl/api/routes/chat.py` | `/status`, `/connect`, `/connect/poll` |
| `ctl/mcp_ops.py`, `ctl/service_ops.py`, `ctl/engine/install.py`, `ctl/worker.py` | Call `sync_agents` / `sync_gateway` |
| `ctl/core_setup.py`, `ctl/install.py` | Health and route checks (`LOBEHUB_*` constants, port 8457 reservation) |
| `dashboard/src/features/chat/ChatPage.tsx` and others | Chat page, Get started, AI settings |
| `platform/tool-gateway/gateway.py` | Per-app MCP endpoint; `find_tools`, `use_tool`, `change_with_tool` |

Read [../chat-connectors.md](../chat-connectors.md) and the memory note
summarised there: an assistant appears only when its connector is reviewed,
live, and the person connected chat once.

**VERIFY before designing 3-2:** how "LobeChat asks before each change" is
configured today. Find where `_upsert_assistant` or the MCP-server
registration sets LobeChat's human-approval option. Record it. That mechanism
stays for LobeChat.

## 3. Task 3-1: Chat provider interface (refactor, no behaviour change)

Goal: shared code talks to "the chat provider", never to LobeChat by name.
After this task LobeChat works exactly as before. All existing tests pass.

1. Create `ctl/integrations/chat/` with:
   - `base.py`: a `ChatProvider` `Protocol` (typed):

     ```python
     class ChatProvider(Protocol):
         interface: ClassVar[str]  # e.g. "lobehub-v1", "open-webui-v1"
         connection: ClassVar[Literal["per_person_approval", "managed"]]
         asks_before_changes: ClassVar[bool]  # can the app itself ask before a tool call?

         def health(self) -> ChatHealth: ...
         def begin_connection(self, uid: str) -> ConnectionStart: ...  # managed: raise NotSupported
         def poll_connection(self, uid: str) -> ConnectionState: ...
         def sync_assistants(self, desired: list[DesiredAssistant], people: list[Person]) -> SyncReport: ...
         def assistant_report(self, uid: str) -> list[AssistantStatus]: ...
         def register_tools(self, app_id: str, endpoint: str, token: str, tools: list[ToolInfo], enabled: bool) -> None: ...
         def launch_url(self) -> str: ...
         def export_help(self) -> ExportHelp: ...  # how people export their history before a switch
     ```

     Use Pydantic models or frozen dataclasses for every argument and result
     (no bare dicts across this boundary; rebuild-plan E2).
   - `registry.py`: maps `interface` strings to implementation classes.
     Interface names describe the **API**, not the app ID. VERIFY that
     `tools/check_no_app_ids.py` accepts `"lobehub-v1"` (it may match the
     substring `lobehub`). If it does not, use `"lobe-api-v1"` and
     `"owui-api-v1"`.
   - `lobe.py`: wraps the existing `LobeHub` client and
     `ctl/chat_connections.py` (move code; do not rewrite logic).
2. Manifest: add `chat_provider: {interface: str}` to `AppManifest` (optional).
   `apps/lobehub/app.yaml` sets it.
3. `ctl/chat_ops.py` replaces `ctl/lobehub_ops.py`. `current_provider()`
   resolves the **selected** chat app (§6.1) and builds its provider. Every
   caller in the table in §2 switches to `chat_ops`. Rename user-facing text
   "LobeChat" to "chat" where it refers to the role rather than the product.
   Product names come from the manifest's `name`.
4. API: `ChatStatus` gains `provider_name`, `connection` and
   `asks_before_changes`. Run `make api-schema`.
5. Dashboard: `ChatPage.tsx` and others read these fields; nothing in the UI
   decides behaviour from an app ID (rebuild-plan E2).
6. `ctl/install.py`/`ctl/core_setup.py` constants named `LOBEHUB_*` become
   capability-driven: read the selected chat app's route ports from its
   manifest.

Acceptance: `make verify`; all chat tests pass with only import or path
changes; `grep -rn -i "lobehub\|lobechat" ctl/ | grep -v integrations/chat/lobe.py`
returns only comments explaining history (or nothing); VM: chat connect,
assistant sync and a Mealie read still work.

## 4. Task 3-2: Gateway approvals (for apps and agents that cannot ask)

**VERIFY first:** does Open WebUI (pinned version) let the user approve or
deny a tool call before it runs? Check its docs and settings (search
"confirm", "approval", "elicitation"). Also check Hermes Agent. Record the
answer.

- If both can ask natively, skip this task, set `asks_before_changes` for
  each, and record why.
- Otherwise, build it as follows (and ask the owner open question Q3 from
  [01-decisions.md](01-decisions.md) §3 **before** starting, with this
  design as your recommendation).

Design:

1. The gateway learns **who** is asking. For each chat provider, VERIFY how
   it can pass the person's identity to tool servers (Open WebUI: forwarded
   user-info headers such as `X-OpenWebUI-User-Email`, behind a setting like
   `ENABLE_FORWARD_USER_INFO_HEADERS`). The gateway trusts these headers
   **only** on the internal network from that provider's container, and only
   alongside the app token. Hermes calls are always the owner.
2. When a provider has `asks_before_changes = false`, `change_with_tool`
   does **not** run immediately. The gateway:
   - records a pending change `{id, app, tool, arguments, summary, person,
     created_at, expires_at (+10 min)}` through the control plane (the
     gateway has no database; VERIFY how it gets policy today
     (`policy.json`) and add a small authenticated internal endpoint in
     `ctl/api` for pending changes);
   - waits (long-poll) up to **120 seconds** for a decision;
   - approved → runs the tool and returns its result; denied → returns
     "<person> declined; nothing was changed"; timeout → returns
     "No approval yet; nothing was changed. Ask the person to approve it in
     Mu3Lab, then try again."
3. The **summary** is written by the gateway from the review data (tool title
   and arguments), never by the model. Show arguments as a readable list.
4. Dashboard: an **Approvals** badge and page for the person concerned (and
   operators see all). Each card: app, tool, readable arguments, who asked,
   time left, **Approve** / **Decline**. Phone-friendly. Mobile push is
   future work; note it in `notes/future.md`.
5. Audit: every decision recorded (who, when, what), with arguments redacted
   by `ctl.jobs.redact`.
6. Tests: gateway unit tests for approve, deny, timeout, wrong person,
   expired, replay (an approval id is used once); API tests; dashboard tests.

## 5. Task 3-3: Open WebUI as a chat app

App folder `apps/open-webui/`. `tier: core` but **not installed unless
selected** (§6). VERIFY how core apps are chosen by core setup and add a
"selectable" path rather than a special case.

### 5.1 VERIFY list

- Latest stable release and image `ghcr.io/open-webui/open-webui:<tag>`
  (the non-CUDA image: models run elsewhere). Digest. Licence: Open WebUI's
  licence since v0.6.6 adds a **branding clause** (do not remove "Open WebUI"
  branding). Record the exact terms. If it forbids anything Mu3Lab does,
  STOP.
- Env names for OIDC: `ENABLE_OAUTH_SIGNUP`, `OAUTH_CLIENT_ID`,
  `OAUTH_CLIENT_SECRET`, `OPENID_PROVIDER_URL`, `OAUTH_PROVIDER_NAME`,
  `OAUTH_SCOPES`, `OAUTH_MERGE_ACCOUNTS_BY_EMAIL`,
  `ENABLE_OAUTH_ROLE_MANAGEMENT`, `OAUTH_ROLES_CLAIM`, `OAUTH_ADMIN_ROLES`,
  `OAUTH_ALLOWED_ROLES`, `ENABLE_OAUTH_GROUP_MANAGEMENT`, `OAUTH_GROUP_CLAIM`,
  `ENABLE_LOGIN_FORM`, `ENABLE_SIGNUP`, `WEBUI_URL`, `WEBUI_SECRET_KEY`.
  The OIDC redirect path (`/oauth/oidc/callback`) and login start path
  (`/oauth/oidc/login`).
- **`ENABLE_PERSISTENT_CONFIG`.** Open WebUI saves many settings in its
  database on first start and later **ignores the environment** for them.
  Decide: set `ENABLE_PERSISTENT_CONFIG=false` so Mu3Lab's env always wins
  (preferred, if supported for all settings Mu3Lab manages), or manage those
  settings through the admin API after start. Test by changing one env value
  and restarting.
- Database: Postgres via `DATABASE_URL` (preferred over SQLite for
  concurrent household use). Postgres major version pinned.
- Model connections: `OPENAI_API_BASE_URL(S)`, `OPENAI_API_KEY(S)`,
  `ENABLE_OLLAMA_API=false` (**no chat models from Ollama**).
- Embeddings: `RAG_EMBEDDING_ENGINE=openai`, `RAG_OPENAI_API_BASE_URL`,
  `RAG_OPENAI_API_KEY`, `RAG_EMBEDDING_MODEL` (LiteLLM's embedding alias).
- Audio: `AUDIO_STT_ENGINE=openai`, `AUDIO_STT_OPENAI_API_BASE_URL`,
  `AUDIO_STT_MODEL`, `AUDIO_TTS_ENGINE=openai`, `AUDIO_TTS_OPENAI_API_BASE_URL`,
  `AUDIO_TTS_MODEL`, `AUDIO_TTS_VOICE` (see [08-speech.md](08-speech.md)).
- Images: `ENABLE_IMAGE_GENERATION`, `IMAGE_GENERATION_ENGINE=comfyui`,
  `COMFYUI_BASE_URL`, and how the ComfyUI workflow JSON and node mapping are
  supplied (env or admin API).
- MCP tool servers: how to register Streamable-HTTP MCP servers with bearer
  tokens (admin API, or `TOOL_SERVER_CONNECTIONS` env), and their access
  control.
- Workspace "Models" (custom assistants): admin API to create/update/delete
  with system prompt, base model, attached tool IDs and access control by
  group.
- API keys: `ENABLE_API_KEY` and the endpoint to create a user's key.

### 5.2 Design

- **Services:** `open-webui` (port 8080 → loopback 3212) and `postgres`
  (internal network). Data: `/app/backend/data` → `data/open-webui/app`;
  Postgres → `data/open-webui/postgres`.
- **Route:** `access: open`, OIDC launch like LobeChat's (VERIFY the launcher
  options in `Oidc.launch`), `redirects: [{path: /auth*, to: <oidc login>}]`
  so the local login page is never shown (VERIFY the login page path).
- **Service account for Mu3Lab (no human password):** use the existing
  `staged_first_start` rule (read `ctl/rules/staged_first_start.py`). In
  stage 1, signup and the login form are enabled but only loopback-reachable
  (route not yet live); `api_bootstrap` creates `mu3lab-service@localhost.test`
  (admin) through Open WebUI's signup API and creates its API key; store it
  in `SecretStore` (scope `open-webui`). In the final start, set
  `ENABLE_SIGNUP=false` and `ENABLE_LOGIN_FORM=false`. Because the service
  account is the first user, the "first sign-up becomes admin" rule can never
  hand admin to a stranger. Verify the service account cannot sign in through
  the UI afterwards.
- **People:** OIDC with `ENABLE_OAUTH_ROLE_MANAGEMENT=true`,
  `OAUTH_ROLES_CLAIM=groups`, `OAUTH_ADMIN_ROLES=mu3lab-operators`,
  `OAUTH_ALLOWED_ROLES=mu3lab-users,mu3lab-operators`, and group management
  so a `mu3lab-users` group exists in Open WebUI for access control.
- **Models:** one LiteLLM virtual key for Open WebUI (VERIFY how
  `provider_routing` issues keys to LobeChat and mirror it). The default
  model list comes from LiteLLM.
- **Assistants:** for each desired assistant, a workspace Model
  `mu3lab-<app>`: base model = the household default chat model; system
  prompt = assistant instructions plus the gateway category map (as LobeChat
  gets); tools = that app's gateway MCP server only; access = group
  `mu3lab-users`. People's own edits: Open WebUI models are shared, so
  personal edits are not supported. Say so in the Chat page help.
- **Tool registration:** each app's gateway endpoint registered once as an
  MCP tool server (admin), token = the app's gateway token. Since tool
  servers are visible to admins only and attached through models, household
  members cannot attach a tool server to arbitrary chats (VERIFY; if they
  can, restrict with access control).
- **Approvals:** if 3-2 found no native approval, register every app with
  the gateway flag `approval_via_mu3lab=true` (3-2 design).
- **Soft dependencies** (from [05-platform.md](05-platform.md) §6):
  `speech` → audio env; `image_generation` → ComfyUI env plus the default
  workflow file `apps/open-webui/comfyui/default-workflow.json` matching the
  ComfyUI starter model (Q4).
- **Phone:** PWA (the Open WebUI site added to the home screen), plus the
  community native app **Conduit** (VERIFY name, licence, OIDC support,
  store links). If Conduit cannot do OIDC through Authentik, list only the PWA.
- **Health:** `/health` (VERIFY).
- **Backups:** data and Postgres folders; exclude `cache/` (VERIFY path for
  embedding and model caches).

## 6. Task 3-4: Choosing and switching

### 6.1 Selection record

- Control state stores `chat.selected_app` (an app ID, written by the
  installer and the switch job). `by_capability("chat")` returns the
  **selected** app when several apps declare `chat`. Catalog validation
  allows several providers of a capability only when that capability is
  listed in a new `SELECTABLE_CAPABILITIES = {"chat"}` constant.
- Fresh installs: the installer (which asks every question up front; see
  commit 7140921) asks "Which chat app do you want?" with two short
  descriptions. Default **LobeChat** (the tested path). Only the selected one
  is installed by core setup.

### 6.2 Switch job (Settings → AI → Chat app)

1. The dialog explains, in plain words: conversations stay in the old app
   (each person can export them first; `export_help()` gives the steps);
   assistants are recreated; with LobeChat each person approves chat once
   more; the old app is stopped, not deleted.
2. Job steps (durable, resumable, each with a progress line):
   1. Install or start the new provider (core install path).
   2. Verify health and sign-in (`signin_check`).
   3. Register tools and sync assistants for everyone.
   4. Set `chat.selected_app` (one atomic write).
   5. Stop the old provider (keep data). Mark it "previous chat app".
   6. Re-render dependents (soft dependencies may point at chat).
3. If any step before 4 fails: the new app is stopped, the selection is
   unchanged, and the old app keeps working. Report the error with its code.
4. **Switch back** uses the same job. The old app's data is still there.
   LobeChat per-person keys may have been wiped; people reconnect once.
5. The old app's card shows **Remove previous chat app and its
   conversations** (typed confirmation with the app name). Only then is its
   data deleted.
6. Tests: switch success; failure at each step leaves the old app working;
   switch back; removal.

## 7. Task 3-5: Hermes Agent (owner add-on)

### 7.1 VERIFY list

- Image: official `nousresearch/hermes-agent` (VERIFY the registry and
  whether versioned tags exist; pin a version tag plus digest, **never**
  `latest`). Licence (MIT reported).
- Process: `gateway run` (API server on 8642) plus dashboard (9119), both in
  one container via `HERMES_DASHBOARD=1`, or two services. VERIFY.
- Data/home directory inside the container (memory, skills, config) and the
  env var that sets it.
- Model configuration: OpenAI-compatible base URL and key env names (point
  at LiteLLM with an **owner-only** LiteLLM key).
- Dashboard authentication: token, and OIDC (`HERMES_DASHBOARD_OIDC_ISSUER`,
  `_CLIENT_ID`, …). Redirect path. Whether it restricts by user or group.
- MCP client configuration (config file keys) and whether Streamable HTTP
  with bearer headers is supported.
- Command-execution backends (local, docker, ssh, …) and **approval**
  settings for dangerous commands.
- Messaging channels: allowed-user lists for Telegram/Signal/Discord.
- API server authentication (an API key setting for port 8642).

### 7.2 Design

- App folder `apps/hermes-agent/`, `tier: optional`, `group: ai`,
  `depends_on: [litellm]`, capability `personal_agent`.
- **Owner only.** Route `access: gate` with a new audience value **`owner`**
  (Authentik policy binding to the installing owner's user, the way
  `initial_owner` works for OIDC apps; read `_absent_owner_policy` in
  `ctl/authentik_blueprints.py`). If Hermes's own dashboard OIDC is verified,
  also enable it with a provider restricted to the owner. Defence in depth.
- **Isolation:** no Docker socket, no host mounts except its data folder,
  `cap_drop: ALL`, `no-new-privileges`, its own network with internet egress
  (web tools need it) plus the network that reaches the tool gateway and
  LiteLLM (VERIFY which network LobeChat uses to reach the gateway and use
  the same). Command execution uses the **local backend inside its own
  container**, never the host. Set its dangerous-command approval to
  "always ask" (VERIFY the setting name).
- **App tools:** the Hermes page in the dashboard lists apps with reviewed
  connectors. The owner ticks which apps Hermes may use; Mu3Lab writes the
  gateway endpoints and tokens into Hermes's MCP config (config file
  template rendered by the engine; restart on change). Write tools go through
  **gateway approvals** (§4) **always**, because Hermes may act while nobody
  is watching (scheduled tasks, messages).
- **Messaging channels:** configured by the owner in Hermes's dashboard.
  Mu3Lab's Hermes page shows a warning: "Anyone who can message your bot can
  talk to your agent. Set Hermes's allowed users before connecting a
  channel." Link to the Hermes docs section (VERIFY URL).
- **In the chat app (optional, last step):** register Hermes's
  OpenAI-compatible API (with its API key) as a model visible **only to the
  owner**: Open WebUI connection with access control; LobeChat custom
  provider for the owner only (VERIFY that LobeHub's API can scope a provider
  to one user; if not, skip for LobeChat and say so).
- **Backups:** the data folder (memory and skills are valuable). No media.
- **Updates:** Hermes releases often. Approve deliberately; read the release
  notes for config migrations.

Acceptance: install in the VM; the owner can open the dashboard, a household
member cannot (403 from the gate); a chat completion through LiteLLM works;
an app read tool works; a write tool creates a pending approval that the
owner approves in Mu3Lab; the command tool cannot see host files.

## 8. Edge cases

| # | Case | Handling |
|---|---|---|
| C1 | Switch while people are mid-conversation | The switch dialog warns; the old app stops at step 5, after the new one is ready. |
| C2 | Person never connected LobeChat, then a switch to Open WebUI | No action needed (managed provider). |
| C3 | Open WebUI env changes ignored after first start | `ENABLE_PERSISTENT_CONFIG` decision (§5.1); a test changes a value and checks it applied. |
| C4 | Open WebUI's first OIDC user is not the owner | Impossible: the service account is the first user, and roles come from groups. |
| C5 | Approval request for a person without dashboard access | Every person has dashboard access via Authentik (VERIFY household role can see Approvals); otherwise route to operators. |
| C6 | Gateway restarts while a change awaits approval | The pending change persists in the control plane; the model's call times out with "nothing was changed"; a later retry finds the approval and runs once. |
| C7 | Hermes model key leaked through a channel | The LiteLLM key is owner-only with a budget limit (VERIFY LiteLLM budget settings); revocable from the Hermes page. |
| C8 | LiteLLM down | Both chat apps show the provider error; the dashboard Chat page explains it. |
| C9 | Both chat apps installed and both running (a mid-switch crash) | On worker start, if the selection is set and the non-selected one is running with no job, stop it and log it. |
