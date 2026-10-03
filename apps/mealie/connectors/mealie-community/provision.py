# Prints a five-year Mealie API token for the one administrator.
from datetime import timedelta

from mealie.core.security import create_access_token
from mealie.db.db_setup import session_context
from mealie.db.models.users.users import LongLiveToken, User
from sqlalchemy import select

with session_context() as db:
    users = list(db.execute(select(User).where(User.admin == True).limit(2)).scalars())  # noqa: E712
    if len(users) != 1:
        raise SystemExit("MU3LAB_ERROR expected exactly one administrator")
    user = users[0]
    db.query(LongLiveToken).filter(LongLiveToken.user_id == user.id, LongLiveToken.name == "Mu3Lab MCP").delete()
    token = create_access_token({"long_token": True, "id": str(user.id), "name": "Mu3Lab MCP"}, timedelta(days=1825))
    db.add(LongLiveToken(name="Mu3Lab MCP", token=token, user_id=user.id))
    db.commit()
    print("MU3LAB_OUTPUT api_token=" + token)
