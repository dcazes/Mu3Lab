"""Backup status must reflect actual repository and verification facts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.backups import readiness
from ctl.runtime import RuntimePaths


class BackupReadinessTests(unittest.TestCase):
    def test_empty_backup_directory_is_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            result = readiness(paths)
        self.assertEqual(result["state"], "not_configured")
        self.assertFalse(result["repository_present"])

    def test_repository_without_successful_check_is_local_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            (paths.backups / "config").write_text("restic", encoding="utf-8")
            result = readiness(paths)
        self.assertEqual(result["state"], "local_only")

    def test_snapshot_and_integrity_metadata_are_required_for_verified(self):
        with tempfile.TemporaryDirectory() as tmp, patch("ctl.backups.shutil.which", return_value="/usr/bin/restic"):
            paths = RuntimePaths(Path(tmp))
            paths.backups.mkdir()
            paths.runtime.mkdir()
            (paths.backups / "config").write_text("restic", encoding="utf-8")
            (paths.runtime / "backup-verification.json").write_text(json.dumps({
                "snapshot_id": "abc", "integrity_checked_at": "2026-01-01T00:00:00Z",
            }), encoding="utf-8")
            result = readiness(paths)
        self.assertEqual(result["state"], "verified")
