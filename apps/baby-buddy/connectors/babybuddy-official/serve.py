"""Start Baby Buddy's MCP server so it answers only callers holding Mu3Lab's connector token.

The server has no sign-in of its own, and it holds the owner's Baby Buddy key.
"""

import os

from babybuddy_mcp.__main__ import main
from babybuddy_mcp.server import mcp
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

token = os.environ.get("MCP_AUTH_TOKEN", "")
if not token:
    raise SystemExit("MCP_AUTH_TOKEN is required")
mcp.auth = StaticTokenVerifier(tokens={token: {"client_id": "contextforge", "scopes": []}})
main()
