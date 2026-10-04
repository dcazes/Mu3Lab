"""Vaultwarden's supported admin interface for account invitations."""

from __future__ import annotations

import httpx

from ctl.integrations.vaultwarden.bootstrap import VaultError


def invite(base_url: str, password: str, email: str) -> None:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30, follow_redirects=False) as client:
        try:
            response = client.post("/admin/", data={"token": password})
            if "VW_ADMIN" not in client.cookies:
                raise VaultError("The vault admin invitation service did not accept its credentials.", "admin_login")
            response = client.post("/admin/invite", json={"email": email.lower()})
            if response.status_code not in (200, 409):
                raise VaultError("The vault could not invite this account.", "admin_invite")
        except httpx.HTTPError:
            raise VaultError("The vault invitation service is unavailable.", "admin_invite") from None
