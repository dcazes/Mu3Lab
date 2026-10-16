# Chat connectors

How Mu3Lab gives each app's chat assistant its tools.

## In one picture

```text
Open WebUI / LobeChat ──► ContextForge ──► connector (one per app) ──► app
  one assistant per app     one virtual server     prebuilt or self-built,
                            per connector          pinned container
```

- **ContextForge** (IBM, `apps/contextforge`) is the only way chat reaches an app's tools. Operators
  open its screens to see every connector, choose which tools each app's assistant gets, and see who
  called what.
- **Connectors** stay Mu3Lab's own pinned containers in `apps/<app>/connectors/<id>/`, started after
  their app is healthy and stopped before it stops. They share a network (`mu3lab_mcp_upstream`)
  with ContextForge only; chat apps reach ContextForge on `mu3lab_mcp`.
- **Everyone in the household gets the same assistants.** Tools that change data run without a
  separate approval; switch off any you don't want in ContextForge.

## Connectors and virtual servers

When a connector goes live, Mu3Lab (`ctl/contextforge_ops.py`):

1. registers it in ContextForge as a gateway named after the connector, with its bearer token;
2. the first time only, creates its virtual server, "<App> (<Connector>)", visible to everyone signed
   in. It starts with:
   - the tools in its `review.yaml`, in categories that start on (blocked tools are never added); or
   - for a connector without a review, every tool it offers.

After that, the virtual server belongs to the operators: Mu3Lab never changes its tools. Tools a
connector adds later appear in ContextForge (it re-reads connectors every few minutes) but stay out
of the virtual server until an operator adds them.

When a connector stops, its gateway is switched off in ContextForge, so its tools leave chat until it
is back. Switching an app to another connector gives chat that connector's own virtual server.

## Who is calling

| Chat app | How ContextForge knows the person |
|---|---|
| Open WebUI | Each tool connection passes the person's own Authentik sign-in (`system_oauth`). ContextForge trusts sign-ins Authentik issued to Open WebUI (the `mu3lab-chat` provider, audience-checked). Open WebUI asks Authentik for refresh tokens (`offline_access`) to keep this working. |
| LobeChat | It cannot pass a sign-in on, so Mu3Lab creates a ContextForge key per person and virtual server, acting as that person. |

Deactivating someone withdraws their LobeChat keys; their Authentik account, and so Open WebUI's
sign-ins, end with it.

## Signing in to ContextForge

Only operators can sign in to ContextForge's screens, through Authentik (the "Mu3Lab" button).
Operators become ContextForge administrators. The second sign-in button, "Mu3Lab chat", exists only
so ContextForge trusts Open WebUI's sign-ins, and it is not for signing in. Mu3Lab's own administrator
account (`mu3lab@contextforge.example.com`) has a password that only Mu3Lab holds.

## Adding a connector for an app without one

1. If the app's REST API covers the requests, register it as a REST tool in ContextForge and
   write no code.
2. Otherwise add a small MCP server under `apps/<app>/connectors/<id>/` (see `immich-control`). It is
   registered like any other connector.

## Later

- **A parent assistant** that hands work to the app assistants. Open WebUI's sub-agents
  (`delegate_task`) copy the current model and tools, so they cannot route work to the app
  assistants; ContextForge's A2A support would need a small bridge.
- **Approval for changes**, once ContextForge's human-approval hook ships.
