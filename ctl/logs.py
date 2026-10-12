"""How Mu3Lab's long-running services log (reviews A-L7 and B-L6).

Under systemd each line starts with its syslog priority (``<4>`` for a
warning), which journald reads as the entry's level, so
``journalctl --user -u mu3lab-worker -p warning`` shows only what went wrong.
Run by hand, lines are plain.
"""

from __future__ import annotations

import logging
import os

# Python's levels as syslog priorities.
_PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}

# Successful requests the dashboard repeats every few seconds; they would bury everything else.
POLLING = ("/api/health", "/api/v1/snapshot")


class _Journal(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        priority = _PRIORITY.get(record.levelno, 6)
        return "\n".join(f"<{priority}>{line}" for line in super().format(record).splitlines() or [""])


def configure() -> None:
    """Send this process's log records to standard error, with levels journald understands."""
    handler = logging.StreamHandler()
    under_systemd = bool(os.environ.get("JOURNAL_STREAM"))
    # journald stamps the time itself.
    text = "%(name)s: %(message)s" if under_systemd else "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(_Journal(text) if under_systemd else logging.Formatter(text))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


class QuietPolling(logging.Filter):
    """Drop uvicorn's access lines for successful dashboard polling; failures still show."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        _client, _method, path, _version, status = args[:5]
        return not (str(path).split("?", 1)[0].startswith(POLLING) and isinstance(status, int) and status < 400)
