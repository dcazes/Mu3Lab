"""Read-only: has the installing owner's first sign-in made them Open WebUI's administrator?

Open WebUI makes its first user the administrator, and until then Authentik
admits only the owner, so any administrator is the owner.
"""

import sqlite3

with sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True, timeout=10) as connection:
    if connection.execute("SELECT 1 FROM user WHERE role = 'admin' LIMIT 1").fetchone():
        print("MU3LAB_OPEN_WEBUI_OWNER_OK")
