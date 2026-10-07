"""Give every household member a Beaver Habits account, through Beaver's own user service.

Beaver signs people in from the trusted email header Mu3Lab sets, but only
accounts that already exist; it never creates them on first visit. Mu3Lab
therefore creates one per person. The password is random and discarded:
nobody signs in with it. Removed people are shut out by Authentik's gate.
"""

import asyncio
import json
import secrets
import sys

from beaverhabits.app.auth import user_create, user_get_by_email
from beaverhabits.storage.dict import DictHabitList
from beaverhabits.views import get_or_create_user_habit_list


async def main(people: list[dict]) -> None:
    created = 0
    for person in people:
        email = str(person.get("email") or "").strip().lower()
        if not email or not person.get("active", True):
            continue
        user = await user_get_by_email(email)
        if user is None:
            await user_create(email=email, password=secrets.token_urlsafe(32))
            user = await user_get_by_email(email)
            if user is None:
                raise RuntimeError(f"account for {person.get('username', '?')} was not created")
            created += 1
        # Beaver's own sign-up also starts an (empty, here) habit list; without one every page fails.
        await get_or_create_user_habit_list(user, DictHabitList({"habits": []}))
    print(f"MU3LAB_BEAVER_PEOPLE_OK created={created}")


try:
    asyncio.run(main(json.loads(sys.argv[1])))
except Exception as error:  # report every failure as one plain line the engine understands
    print("MU3LAB_ERROR " + str(error).replace("\n", " "))
    sys.exit(1)
