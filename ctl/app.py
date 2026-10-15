"""ASGI entrypoint for the control plane.

Production: `uvicorn ctl.app:app --host 127.0.0.1 --port 8787` (mu3lab-ctl.service).
Health check: `curl -f http://127.0.0.1:8787/api/health`.
"""

import logging

from ctl import logs
from ctl.api import create_app

# The dashboard's 10-second polling would otherwise be nearly all of the access log.
logging.getLogger("uvicorn.access").addFilter(logs.QuietPolling())

app = create_app()
