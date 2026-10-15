# Chat connectors

How Mu3Lab gives each app's chat assistant a small, reliable set of tools.

## Goals

- **Small models stay accurate.** Tool selection degrades past roughly 20 visible tools and
  collapses near 100. An assistant never sees more than about 10 tools at once.
- **Nothing is hidden from the assistant.** It always knows every category its connector
  offers, including the ones switched off, so it searches instead of giving up and can tell
  the owner which switch to flip.
- **One app, one assistant, one connector.** Each app's assistant can reach only its own app.
  The main assistant has no app tools; it delegates to app assistants.
- **Every tool is reviewed.** Mu3Lab decides what each tool does (reads or changes data), which
  category it belongs to, and whether it starts on. New, unreviewed tools stay unreachable.
- **Writes fail closed.** Chat tools that change data are unavailable until the gateway can verify
  human approval for every call. Saved switches cannot bypass this restriction. The dashboard's
  operator test console has its own confirmation path; it does not establish chat approval.

## Architecture

```text
LobeChat app assistant ──► Mu3Lab tool gateway ──► active connector ──► app
   (one per app)             /apps/<app>/mcp         (one per app)
                             policy.json ◄── control plane (switches, reviews)
```

### Tool gateway (`platform/tool-gateway`)

One small MCP server in front of every connector. Each app has its own path and its own bearer
token, so an assistant's credential reaches only its app. For each app the assistant sees:

| Tool | Purpose | Approval |
|---|---|---|
| Up to 6 **everyday tools** | The app's most common requests, passed straight through | Reads: none. Writes are not listed |
| `find_tools` | Lists every category (on and off) or the tools in one category, with inputs | None |
| `use_tool` | Runs a switched-on tool that only reads | None |
| `change_with_tool` | Currently not listed; calls are rejected even if the saved write switch is on | Unavailable |

The gateway enforces the owner's switches and the reviewed read/change split on every call; the
model cannot reach a switched-off tool, an unreviewed tool, or a changing tool through
`use_tool`. Direct and wrapped writes are rejected before connector dispatch. Write discovery
shows them as off with an approval-unavailable explanation. It also returns the category map as the MCP `instructions`, and Mu3Lab writes the
same map into the assistant's instructions.

This follows the pattern of Docker's MCP Gateway and IBM ContextForge (a policy gateway in front
of connectors) and of GitHub's dynamic toolsets and Anthropic's tool search (a small always-on
set plus on-demand discovery). LobeChat stores a connector's tool list when it connects and
does not follow `tools/list_changed`, so on-demand loading has to happen through fixed tools.

Connectors sit on a network that only the gateway shares with them (`mu3lab_mcp_upstream`);
LobeChat reaches the gateway only.

### Reviews (`apps/<app>/connectors/<connector>/review.yaml`)

One checked-in file per connector lists its categories, and for each tool its category, whether
it reads or changes data, and whether it is an everyday tool. Tools that must never be offered
(credentials, server administration) are listed under `blocked` with the reason. A tool the
connector offers that the review does not list is treated as unreviewed and never exposed.

Defaults: a category starts on unless its review says otherwise; within it, reading tools start
on and changing tools start off.

### One connector per app

An app may have several reviewed connectors (for example Immich has two). Exactly one is the
default and at most one is enabled. Switching is one job: the old connector is stopped and
detached before the new one starts. Each connector keeps its own switches, so switching back
restores them. The assistant and its chats stay the same, because LobeChat is always connected
to the gateway, not to the connector behind it.

### Lifecycle

- Installing an app enables its default connector, creates its credential where the app allows
  it, and attaches it to the app's assistant through the gateway.
- Stopping an app stops its connector; uninstalling removes it and its assistant (an assistant
  with chats is kept).

## Later phases

- **Code as action.** A sandboxed `run_script` tool that lets an assistant chain several reads in
  one step (after Anthropic's "code execution with MCP" and Cloudflare's Code Mode). The sandbox
  holds no app credentials and can only call the gateway with the assistant's own token; scripts
  that change data are shown in full for approval.
- **Skills.** Checked-in, per-app procedures (for example "plan this week's dinners and build the
  shopping list") attached to each app assistant.
- **Home group.** A LobeChat agent group whose supervisor has no app tools and delegates to the
  app assistants for requests that span apps.
