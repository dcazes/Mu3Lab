"""The canonical settings of one app; its ``.env`` is only ever an output (R16).

Two kinds of value are kept, encrypted, in the secret store under the app's
scope ``app:<id>``:

- ``config:<KEY>``: the operator's typed configuration, one row per field,
  stored as the value the app reads. Only the configuration form writes it.
- ``env:<NAME>``: everything else Mu3Lab generated or manages for the app
  (database passwords, sign-in secrets, values rules add, the last rendered
  value of each templated setting). Rendering replaces this set, so it
  mirrors the managed part of the last ``.env``.

Precedence when rendering: deployment defaults, then operator configuration,
then managed values (rules, integrations, the manifest's templated ``env``).
A configuration field whose variable Mu3Lab manages is not editable.

Existing installations kept these values only in ``.env``. The first time an
app's settings are read or rendered, ``ensure_imported`` copies them in once:
configuration fields into ``config:`` rows, every other value that is not a
fact recomputed on each render into ``env:`` rows. Nothing is discarded, and a
marker keeps it from running twice. From then on a fresh render with
``.env`` deleted reproduces it.

Read-modify-write sequences run under the app's resource lock
(``resource_locks.hold("app:<id>")``), which a job on the app already holds.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ctl.manifest.models import AppManifest
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store.secrets import SecretStore

ENV = "env:"
CONFIG = "config:"
IMPORTED = "meta:env-imported"


def fixed_names(manifest: AppManifest) -> set[str]:
    """Variables recomputed from facts on every render; never stored."""
    names = {"MU3LAB_DATA_ROOT", "TZ", *(folder.env for folder in manifest.media)}
    oidc = manifest.sign_in.oidc
    if oidc:
        names.add(oidc.env.client_id)
        if oidc.env.discovery_url:
            names.add(oidc.env.discovery_url)
    return names


def managed_names(manifest: AppManifest) -> set[str]:
    """Variables Mu3Lab sets itself, so the configuration form cannot own them."""
    names = fixed_names(manifest) | set(manifest.env)
    for integration in manifest.integrates_with:
        names |= set(integration.env) | set(integration.absent_env)
    return names


class AppSettings:
    def __init__(self, app_id: str, paths: RuntimePaths | None = None) -> None:
        self.app_id = app_id
        self.paths = paths or RuntimePaths()
        self.scope = f"app:{app_id}"
        self.store = SecretStore(self.paths)

    def _values(self, prefix: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for name in self.store.list_names(self.scope):
            if name.startswith(prefix):
                value = self.store.get(self.scope, name)
                if isinstance(value, str):
                    values[name.removeprefix(prefix)] = value
        return values

    # --- managed and generated values -------------------------------------------

    def generated(self) -> dict[str, str]:
        return self._values(ENV)

    def set_generated(self, values: dict[str, str]) -> None:
        current = self.generated()
        for name, value in values.items():
            if current.get(name) != value:
                self.store.put(self.scope, ENV + name, value)

    def remove_generated(self, names: Iterable[str]) -> None:
        for name in names:
            self.store.delete(self.scope, ENV + name)

    def replace_generated(self, values: dict[str, str]) -> None:
        """Make the stored managed values exactly ``values``."""
        self.set_generated(values)
        self.remove_generated(set(self.generated()) - set(values))

    # --- operator configuration --------------------------------------------------

    def config(self) -> dict[str, str]:
        return self._values(CONFIG)

    def set_config(self, values: dict[str, str]) -> None:
        current = self.config()
        for key, value in values.items():
            if current.get(key) != value:
                self.store.put(self.scope, CONFIG + key, value)

    # --- one-time import of an installation's existing .env ------------------------

    def imported(self) -> bool:
        return self.store.get(self.scope, IMPORTED) is not None

    def ensure_imported(self, manifest: AppManifest, env_path: Path) -> int:
        """Copy an existing ``.env`` into the store once; return how many values were taken."""
        if self.imported():
            return 0
        existing = read_runtime_env(env_path)
        fields = {field.env: field.key for field in manifest.configuration}
        managed = managed_names(manifest)
        config = self.config()
        generated = self.generated()
        taken = 0
        for name, value in existing.items():
            if name in fields and name not in managed:
                if fields[name] not in config:
                    self.store.put(self.scope, CONFIG + fields[name], value)
                    taken += 1
            elif name not in fixed_names(manifest) and name not in generated:
                # Keeps the last rendered templated values too, so a fact that is
                # briefly unknown (Tailscale not joined) cannot blank a setting.
                self.store.put(self.scope, ENV + name, value)
                taken += 1
        self.store.put(self.scope, IMPORTED, True)
        return taken
