# Prints the owner's Beaver Habits API token, creating one if they have none. An existing token is
# reused because a phone or script may already use it.
import asyncio
import sys

from beaverhabits.app.auth import user_get_by_email
from beaverhabits.app.crud import create_user_api_token, get_user_api_token


async def main(email: str) -> None:
    user = await user_get_by_email(email)
    if user is None:
        raise RuntimeError("the owner has no Beaver Habits account yet")
    token = await get_user_api_token(user) or await create_user_api_token(user)
    print("MU3LAB_OUTPUT api_token=" + token)


try:
    asyncio.run(main((sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()))
except Exception as error:
    print("MU3LAB_ERROR " + str(error).replace("\n", " "))
    sys.exit(1)
