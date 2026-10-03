# Prints a SurfSense personal access token for the one active user and enables API access on their workspaces.
import asyncio

from app.db import PersonalAccessToken, User, Workspace, async_session_maker
from app.utils.pat import generate_pat, hash_pat, token_prefix
from sqlalchemy import delete, select, update


async def main():
    async with async_session_maker() as db:
        users = list((await db.execute(select(User).where(User.is_active == True).limit(2))).scalars())  # noqa: E712
        if len(users) != 1:
            raise SystemExit("MU3LAB_ERROR expected exactly one active user")
        user = users[0]
        await db.execute(
            delete(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user.id, PersonalAccessToken.label == "Mu3Lab MCP"
            )
        )
        token = generate_pat()
        db.add(
            PersonalAccessToken(
                user_id=user.id,
                token_hash=hash_pat(token),
                token_prefix=token_prefix(token),
                label="Mu3Lab MCP",
                expires_at=None,
            )
        )
        await db.execute(update(Workspace).where(Workspace.user_id == user.id).values(api_access_enabled=True))
        await db.commit()
        print("MU3LAB_OUTPUT api_token=" + token)


asyncio.run(main())
