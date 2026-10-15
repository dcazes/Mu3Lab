# Milestone B3 implementation (R11, R16) — 2026-10-08

This records B3 of the [project review](2026-10-07-project-review.md): immutable deployment identities (R11) and canonical configuration with safe, private publication (R16). Status: **implemented; release acceptance pending**. Nothing here was run on a real host.

Branch: `fix/review-milestone-b`, after [B1](2026-10-08-milestone-b1-implementation.md) and [B2](2026-10-08-milestone-b2-implementation.md). Owner: Claude Code, authorized by the owner on 2026-10-08.

## Owner decision

Existing installations keep their settings only in each app's `.env`. The owner chose to **import the existing `.env` settings once** into the canonical store, rather than keep reading `.env` as a fallback.

## R11 — every deployment has an immutable identity

[`ctl/lifecycle/app_releases.py`](../../ctl/lifecycle/app_releases.py):

- **Release ID.** `Release.id` is a content hash of every service image, the compute variant (`cpu`, `nvidia`, `amd`) and a definition digest. The digest covers the Compose file, the variant's override, and the app's configuration and integration schema. The version is only a label: two releases with the same version compare equal for display, but their IDs differ when anything that deploys differs.
- **What the record stores.** `docker-compose.digest.yml` records the version, ID, variant and definition. Install pinning records the variant and its override images.
- **Approved release.** `approved()` resolves the variant's override, so an update is evaluated against the full set of files that will run.
- **History.** `releases` is keyed by ID and never rewritten (`remember`). Records written before IDs existed are still read: each legacy `version → images` entry becomes a CPU release.
- **Lookups.** `from_history(..., release_id=)` finds a release exactly. A lookup by version only is used for older backups, and raises `AmbiguousRelease` when two recorded releases share that version.
- **Backups and restore.** Backups are tagged `release:<id>`, and the release is remembered first. Restore switches whenever the backup's release ID differs from the installed one, even at the same version, and downloads exactly those images. An older backup whose version matches two deployments is refused with `release_ambiguous` rather than guessed.
- **Update plan.** `status()` compares the union of old and new services, and reports `added_services` and `removed_services`. The dashboard's update card lists them.
- **Atomic writes.** The release record is written atomically and privately.

## R16 — one source of truth for settings

[`ctl/app_settings.py`](../../ctl/app_settings.py) keeps each app's settings encrypted in the secret store, under scope `app:<id>`:

- `config:<KEY>`: the operator's typed configuration. Only the configuration form writes it.
- `env:<NAME>`: everything Mu3Lab generated or manages, including database passwords, sign-in secrets, values that rules add, and the last rendered value of each templated setting. Rendering replaces this set.

Rendering ([`ctl/engine/project.py`](../../ctl/engine/project.py)):

- **Input.** It reads only the store; `.env` is never read back as input. Deleting `.env` and rendering again reproduces it byte for byte, keeping the existing line order and sorting new names.
- **Precedence.** Deployment defaults, then operator configuration, then managed values (rules, integrations, the manifest's templated `env`).
- **Managed fields.** A configuration field whose variable Mu3Lab sets itself is hidden and rejected with 422.
- **Staging.** Every output (`.env` and each `.tmpl`) is computed before any is written, so a render failure publishes nothing. A template whose facts are not known yet keeps its previous output, and a templated setting keeps its last value while a fact is briefly unknown (for example, Tailscale not yet joined).
- **Publication.** Each file goes through `secret_file.write_atomic`: a unique 0600 temporary file, fsync, rename, then fsync of the directory. Predictable `.tmp` names are gone.
- **Locking.** Rendering holds the `app:<id>` resource lock from B2. A job already holding it re-enters for free.

**Import.** The first read or render of an app copies its existing `.env` into the store once. Configuration fields become `config:` rows, and every other value except facts recomputed on each render becomes an `env:` row. Values already in the store win, nothing is discarded, and a marker stops it running twice.

**Configuration writes** ([`ctl/service_config.py`](../../ctl/service_config.py)):

- They take the app lock, waiting up to 10 s; otherwise they return 409 and change nothing.
- They check `expected_revision` against the revision the form loaded (`ControlState.bump_config_revision(expected=)`, checked inside an immediate transaction). A stale form gets 409 `configuration_changed`, and nothing changes.
- They save to the store, then publish into `.env`.
- The dashboard form sends the revision it loaded and keeps the new one returned.

**Other writers now save to the store first:**

- `HookContext.set_env`, which already did;
- Authentik's first-start values in `secrets.ensure_authentik_env` / `clear_authentik_bootstrap`;
- the model proxy's gateway credentials in `core_wiring.write_routing`;
- the bootstrap clean-up in `reset_failed_application`.

## Tests

`tests/test_app_settings.py`:

- the one-time import;
- reproduction after deleting `.env`;
- `.env` is not read as input;
- precedence;
- a briefly unknown fact keeps the last rendered setting;
- 0600 files with no temporary files left behind;
- a failed render publishes nothing;
- a stale form is rejected;
- a saved value survives the next render;
- a write waits for a job holding the app;
- managed fields are rejected.

`tests/test_app_releases.py` (`ReleaseIdentityTests`): same version with different images, variant and definition identity, GPU install, removed services, and legacy history.

`tests/test_maintenance.py` (`ReleaseIdentityRestoreTests`): restoring another deployment at the same version, the ambiguous older backup, and backup tagging.

## Remaining acceptance and limits

- **VM:** install, a GPU-variant install, update, and restore at the same version with a changed database image. Also upgrade a real installation and confirm the one-time import keeps every setting and secret.
- **Scope:** the generated non-secret configuration schema and the integration revision enter the definition digest only through the manifest schema. Provider configuration (core wiring) is not yet versioned per release.
- **Not covered:** MCP connector projects (`mcp-<id>`, `ctl/mcp_config.py`) still write their own `.env`; they are not rendered by `engine/project.py`.
- **Partial publication:** several files are replaced one after another. A crash between them leaves a mix, which the next render, deterministic from the store, repairs. There is no on-disk detection marker yet.
- **API process:** the API still writes configuration (through the store, under the app lock). R18 decides whether that moves to the worker.
