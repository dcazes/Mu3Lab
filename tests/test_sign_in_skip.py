"""Sign-in imports Authentik already holds are skipped: each one takes tens of seconds."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctl.identity import _Unchanged, forget_applied
from ctl.runtime import RuntimePaths

BLUEPRINT = """entries:
  - model: authentik_core.application
    id: app
    state: present
    identifiers:
      slug: mu3lab-immich
    attrs:
      name: Immich
"""


class FakeAuthentik:
    def __init__(self) -> None:
        self.release = "2026.10.1"
        self.slugs = {"mu3lab-immich"}
        self.applied: list[str] = []

    def version(self) -> str:
        return self.release

    def application_slugs(self) -> set[str]:
        return set(self.slugs)

    def apply_blueprint(self, name: str, content: str) -> None:
        self.applied.append(name)


class SkipTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = RuntimePaths(Path(folder.name))
        self.authentik = FakeAuthentik()

    def apply(self, content: str = BLUEPRINT, *, force: bool = False) -> None:
        _Unchanged(self.authentik, self.paths, force).apply("Mu3Lab Immich sign-in", content)  # type: ignore[arg-type]

    def test_the_same_import_is_sent_once(self):
        self.apply()
        self.apply()
        self.assertEqual(len(self.authentik.applied), 1)

    def test_changed_content_an_authentik_update_or_a_missing_app_import_again(self):
        self.apply()
        self.apply(BLUEPRINT.replace("name: Immich", "name: Immich photos"))
        self.assertEqual(len(self.authentik.applied), 2)
        self.authentik.release = "2026.12.0"
        self.apply(BLUEPRINT.replace("name: Immich", "name: Immich photos"))
        self.assertEqual(len(self.authentik.applied), 3)
        # Someone deleted the application in Authentik: the fingerprint alone is not trusted.
        self.authentik.slugs.clear()
        self.apply(BLUEPRINT.replace("name: Immich", "name: Immich photos"))
        self.assertEqual(len(self.authentik.applied), 4)

    def test_forgetting_or_forcing_imports_again(self):
        self.apply()
        forget_applied("Mu3Lab Immich sign-in", self.paths)
        self.apply()
        self.apply(force=True)
        self.assertEqual(len(self.authentik.applied), 3)


if __name__ == "__main__":
    unittest.main()
