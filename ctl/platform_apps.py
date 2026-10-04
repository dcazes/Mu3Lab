"""Discover platform services by declared capability, never by app ID."""

from ctl.manifest.catalog import App, Catalog, cached


def by_capability(name: str, catalog: Catalog | None = None) -> App:
    matches = [app for app in (catalog or cached()).apps if name in app.manifest.capabilities]
    if len(matches) != 1:
        raise ValueError(f"Exactly one app must provide {name}.")
    return matches[0]
