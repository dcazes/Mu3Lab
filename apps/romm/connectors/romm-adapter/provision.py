# Prints a new read-only RomM client API token ("Mu3Lab MCP") for the owner's RomM account.
# RomM signs people in through Authentik only, so this runs inside the container and uses
# RomM's own token code, exactly as Settings, Client API tokens would. An earlier Mu3Lab MCP
# token is deleted first.
import sys

from handler.auth import auth_handler
from handler.database import db_client_token_handler, db_user_handler
from models.client_token import ClientToken

NAME = "Mu3Lab MCP"
SCOPES = ("me.read", "platforms.read", "roms.read", "collections.read")

try:
    username = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    user = db_user_handler.get_user_by_username(username) if username else None
    if user is None or not user.enabled:
        raise RuntimeError("the owner has no RomM account yet; open RomM once to create it, then retry")
    allowed = {str(scope) for scope in user.oauth_scopes}
    if not set(SCOPES) <= allowed:
        raise RuntimeError("the owner's RomM account cannot read the library")
    for token in db_client_token_handler.get_tokens_by_user(user.id):
        if token.name == NAME:
            db_client_token_handler.delete_token(token.id, user_id=user.id)
    raw = auth_handler.generate_client_token()
    db_client_token_handler.add_token(
        ClientToken(
            user_id=user.id,
            name=NAME,
            hashed_token=auth_handler.hash_client_token(raw),
            scopes=" ".join(SCOPES),
            expires_at=None,
        )
    )
    print("MU3LAB_OUTPUT api_token=" + raw)
except Exception as error:
    print("MU3LAB_ERROR " + str(error).replace("\n", " "))
    sys.exit(1)
