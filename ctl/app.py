"""ASGI entrypoint for the control plane.

Production: `uvicorn ctl.app:app --host 127.0.0.1 --port 8787` (mu3lab-ctl.service).
Health check: `curl -f http://127.0.0.1:8787/api/health`.
"""

from ctl.api import create_app

app = create_app()
