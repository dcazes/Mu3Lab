"""Keep AdventureLog defaults, but scope its login cookie to this host.

Upstream derives the cookie domain from the last two labels of SITE_URL, which
for a Tailscale name is ".ts.net": a public suffix every browser refuses. The
Authentik sign-in then loses its state and ends in "Third-Party Login Failure".
A host-only cookie is what a single private address needs.
"""

from main.settings import *  # noqa: F403

SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
