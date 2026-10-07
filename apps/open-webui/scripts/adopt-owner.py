"""Create the installing owner's Open WebUI administrator before anyone signs in.

The account is created through Open WebUI's own account service, already
linked to the owner's Authentik sign-in ("oidc" provider, sub = the
Authentik user ID), with a random password nobody uses (password sign-in is
off). Because the owner is the first user, Open WebUI's "first user becomes
administrator" rule can never hand that role to anyone else.
"""

import asyncio
import json
import secrets
import sys

from open_webui.models.auths import Auths
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash


async def main(owner: dict) -> None:
    email = owner["email"].strip().lower()
    sub = owner["owner_uid"].strip()
    if not email or not sub:
        raise ValueError("the owner identity is incomplete")
    user = await Users.get_user_by_oauth_sub("oidc", sub) or await Users.get_user_by_email(email)
    if user is None:
        user = await Auths.insert_new_auth(
            email=email,
            password=await get_password_hash(secrets.token_urlsafe(32)),
            name=owner.get("display_name") or owner["username"],
            role="admin",
            oauth={"oidc": {"sub": sub}},
        )
        if user is None:
            raise RuntimeError("Open WebUI did not create the account")
    else:
        linked = (user.oauth or {}).get("oidc", {}).get("sub")
        if linked and linked != sub:
            raise RuntimeError(f"{email} is already linked to a different sign-in")
        await Users.update_user_oauth_by_id(user.id, "oidc", sub)
        if user.role != "admin":
            await Users.update_user_role_by_id(user.id, "admin")
    print("MU3LAB_OPEN_WEBUI_OWNER_OK")


try:
    asyncio.run(main(json.loads(sys.argv[1])))
except Exception as error:  # one plain line the engine understands
    print("MU3LAB_ERROR " + str(error).replace("\n", " "))
    sys.exit(1)
