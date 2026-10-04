# Developing and releasing Mu3Lab

The dashboard uses the same pinned Node container for development and release
builds. Node.js is not installed or removed on the host. Docker must already be
available to the current user.

From an isolated development checkout:

| Command | Purpose |
| --- | --- |
| `make dev-setup` | Prepare pinned Python/Bitwarden tools and build the UI |
| `make api-schema` | Dump Python OpenAPI and generate UI types/aliases; run after API model changes |
| `make ui-dev` | Preview the UI at `http://127.0.0.1:5173` with automatic refresh |
| `.venv/bin/python -m tools.dashboard check` | UI lint, formatting, type checks and tests |
| `.venv/bin/python -m tools.dashboard build` | Build `dashboard/dist` for this checkout |
| `make format` | Format Python and UI sources |
| `make verify` | Run Python/UI checks and build the UI |

The preview container uses the Linux host network, binds only loopback, and
forwards API requests to the backend at `127.0.0.1:8787`. Run an isolated backend
for development that changes data; a preview connected to a live backend can
change that backend's data. Previewing or building never installs the checkout
onto another running system. Development checkout updates stay manual.

Normal releases use `vMAJOR.MINOR.PATCH` tags. The release workflow first runs
CI, builds all seven local connector/gateway images for amd64 and arm64, and
publishes them to GHCR. It then builds the dashboard in the pinned container
and publishes the dashboard archive, `release-images.json` and `SHA256SUMS`.
The immutable image manifest records each Compose service's published digest.
Tagged runtime projects use those digests and remove local image builds.

The installed updater accepts normal version tags, ignores preview tags and
untagged branch commits, verifies release assets before applying the install
steps, and restarts the control plane after success. A development checkout or
edited checkout cannot update automatically. Missing or incorrect checksums
prevent installation. Dashboard assets must also match the source fingerprint
of the checked-out release.

First-release acceptance still requires publishing a real tag, checking GHCR
package access, and exercising installation and update in disposable machines.
Local tests do not prove GitHub publishing permissions. Tasks A–J are
implemented; full real-device and published-release acceptance remains pending. See [acceptance.md](acceptance.md).

API changes start in `ctl/api/models.py`. Request models forbid unknown fields;
response models validate public data, including custom JSON responses. Errors
have `code`, `message` and `recommended_action` without echoing rejected inputs.
Run `make api-schema` and commit `dashboard/openapi.json`, `schema.ts` and
`models.ts` together. CI checks Python → OpenAPI and OpenAPI → TypeScript, so a
model change cannot silently leave stale UI types. Schema generation does not
start an API server or touch runtime state.

For backend development, set `MU3LAB_RUNTIME_ROOT` to a disposable directory and
use a separate port/process configuration. The UI proxy in `dashboard/vite.config.ts`
must target that backend. An empty runtime shows pending status until its worker
runs; page loads never run Docker to fill it. Do not start another worker against
the household runtime. Lifecycle testing belongs in the disposable VM.

Feature CSS lives next to its feature, with shared tokens, base, components and
responsive rules in `styles/`. `src/index.css` imports them in the original order
so eager and lazy page loading cannot change the cascade. The Playwright visual
check and its test environment are described in [acceptance.md](acceptance.md).
