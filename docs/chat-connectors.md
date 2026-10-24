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
- **Shared apps give everyone the same assistant; private apps give each person their own.** Tools
  that change data run without a separate approval; switch off any you don't want in ContextForge.

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

## The main assistant

Every new Open WebUI chat starts with **Mu3Lab** (`mu3lab-assistant`), set through Open WebUI's
`DEFAULT_MODELS`, `DEFAULT_PINNED_MODELS` and `MODEL_ORDER_LIST`. It has no app tools. Its instructions
list the app assistants that are live, and it tells people to switch with `@` and the assistant's name,
or with the model menu. With no apps installed, it explains how to add them.

It does not hand work to the app assistants itself. Open WebUI's sub-agents (`delegate_task`) run with
the parent chat's own model and tools, so they cannot reach another assistant's tools.

## Kinds of connector

| Kind | Example | What runs |
|---|---|---|
| Upstream package in a pinned container | `mealie-community`, `babybuddy-official` | Our Compose project, built from a pinned release or commit |
| Built into the app (`builtin: true`) | `outline-builtin`, `dawarich-builtin` | Nothing extra: the app joins `mu3lab_mcp_upstream` and ContextForge calls its own MCP endpoint |
| Mu3Lab adapter | `adventurelog`, `beaver-adapter`, `audiobookshelf-adapter`, `navidrome-adapter`, `romm-adapter` | `platform/mcp-adapter/server.py`, a few fixed tools over the app's REST API |

A connector can also declare fixed `headers` that ContextForge sends with its credential (Dawarich
needs `X-Forwarded-Proto: https`). A credential script can ask for the owner's username
(`owner_username`) or email (`owner_email`).

When no script inside the app can make the credential, `provision_hook: true` has Mu3Lab make it
instead, through `connector_credentials` in the app's `hooks.py`. Navidrome uses this: its connector
signs in as Mu3Lab's own administrator (`mu3lab-service`), so no person's login is shared.

Apps that sign people in through Authentik only (Audiobookshelf, RomM) have no password Mu3Lab could
use, so their scripts run inside the app and make a dedicated "Mu3Lab MCP" key with the app's own code.
RomM's token can only read the library.

## Private apps: one set of tools per person

Some apps keep each person's data private: Paperless-ngx, Immich, Nextcloud, Outline, AdventureLog,
Actual Budget, SurfSense, Dawarich and Beaver Habits. One connector holding the owner's key would show
the owner's data to anyone who asked chat. Their connectors say `per_person: true`, and
`ctl/person_connectors.py` gives everyone their own:

- **A key minted for them in the app.** The provision script takes their username or email
  (`owner_username` / `owner_email`), never "the only administrator".
- **Their own copy of the connector**, named `mcp-<connector>--p<hash>`. The copy gets its own Compose
  project and service names and no host port. A built-in MCP server needs no copy.
- **Their own gateway and virtual server in ContextForge**, kept to a team with only them in it
  (`Mu3Lab <username>`). Other household members can neither list nor call it.

The owner keeps the connector Mu3Lab already runs, and its gateway and server are kept to the owner's
team too. Open WebUI gets one assistant per person, granted to their account only, and named for the
app as usual. LobeChat gets the person's own server, with a key made in their team.

Someone who has not opened the app yet has no account there, so their copy waits; it is retried every
ten minutes. The app stopping stops the copies. Someone leaving the household has every copy, key and
team removed, as part of revoking their access. Switching the connector off removes them all.

Actual Budget's key is typed in by hand, so only its owner has an assistant. ContextForge's
administrators (Mu3Lab's operators) can still reach every team's tools in ContextForge's own screens.

ContextForge requires keys to expire, so Mu3Lab's keys last 90 days and are replaced when they lapse.
Revoked keys keep their names, so each new key gets a fresh suffix.

## Platform services: read-only, administrators only

Infrastructure apps (`group: infrastructure`) may have a connector only if its assistant is
`audience: operators` and every tool in its `review.yaml` only reads; the catalog refuses anything
else. Both start switched off (`preferred: false`).

| App | Tools | Credential |
|---|---|---|
| Authentik (`authentik-reader`) | People, groups, recent sign-in events | A `mu3lab-mcp` service account whose only role views users, groups and events (blueprint in `ctl/authentik_blueprints.py`) |
| Kopia (`kopia-reader`) | Each app's backup status, the backup storage | Mu3Lab's `mu3lab-control` login; the adapter calls only the two read routes |

Open WebUI keeps them to its administrators: a tool connection with no grants is admin-only, and the
assistant is granted to each administrator by ID (administrators do not see everything there). The
main assistant does not list them, and LobeChat gives them to operators only. Foundation services
such as Authentik have no install record, so connector code treats them as installed and running
(`ControlState.app_installation`).

Kopia's server listens on loopback only, so its adapter runs on the host network and listens on
loopback and on `docker0` (`MCP_LISTEN`); ContextForge reaches it as `host.docker.internal`.

Caddy (ingress) has none: its admin API is switched off, so there is nothing to read. Vaultwarden is
not offered either.

Where only some tools touch one person's data, the connector is on but those tools start off
(`default_on: false` in its `review.yaml`): Audiobookshelf's listening progress and listening time.

## Adding a connector for an app without one

1. If the app has its own MCP server, use it: built in (`builtin: true`) or as a pinned container.
2. Otherwise, if a few REST calls cover it, add a mode to `platform/mcp-adapter/server.py`.
3. Otherwise add a small MCP server under `apps/<app>/connectors/<id>/` (see `immich-control`).

## Later

- **Approval for changes**, once ContextForge's human-approval hook ships.
