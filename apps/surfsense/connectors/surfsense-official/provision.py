# Prints a SurfSense personal access token for one person, found by email, and enables API access on their workspaces.
import asyncio
import sys

from app.db import PersonalAccessToken, User, Workspace, async_session_maker
from app.utils.pat import generate_pat, hash_pat, token_prefix
from sqlalchemy import delete, func, select, update


async def main():
    email = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if not email:
        raise SystemExit("MU3LAB_ERROR the person's email is missing")
    async with async_session_maker() as db:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == email, User.is_active == True))  # noqa: E712
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit("MU3LAB_ERROR this person has no active SurfSense account yet")
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
