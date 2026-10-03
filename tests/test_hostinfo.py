from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ctl import hostinfo


def _no_timedatectl(*_args, **_kwargs):
    raise OSError("not installed")


class TimezoneTests(unittest.TestCase):
    def test_reads_etc_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "timezone").write_text("Europe/Paris\n", encoding="utf-8")
            self.assertEqual(hostinfo.timezone(Path(tmp), run=_no_timedatectl), "Europe/Paris")

    def test_falls_back_to_timedatectl(self):
        def run(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout="Asia/Tokyo\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(hostinfo.timezone(Path(tmp), run=run), "Asia/Tokyo")

    def test_invalid_names_become_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "timezone").write_text("Not/AZone\n", encoding="utf-8")
            self.assertEqual(hostinfo.timezone(Path(tmp), run=_no_timedatectl), "UTC")


if __name__ == "__main__":
    unittest.main()
