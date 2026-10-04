# Make Mealie's seeded administrator the owner's Authentik account (Mealie's Python; owner JSON in argv[1]).
#
# Mealie seeds changeme@example.com and shows a "first login" screen while it
# exists. Its OIDC sign-in adopts an account whose email matches the verified
# Authentik email, so giving the seeded administrator the owner's email (and a
# password nobody knows) makes their first sign-in land on it with no setup.
import json
import secrets
import sys

from mealie.core.security.hasher import get_hasher
from mealie.db.db_setup import session_context
from mealie.db.models.users.users import AuthMethod, User

owner = json.loads(sys.argv[1])
email = owner["email"].strip().lower()
with session_context() as session:
    current = session.query(User).filter(User.email == email).first()
    seeded = session.query(User).filter(User.email == "changeme@example.com").first()
    if seeded is not None:
        seeded.password = get_hasher().hash(secrets.token_urlsafe(48))
        if current is None:
            seeded.email = email
            seeded.username = owner["username"] or email.split("@", 1)[0]
            seeded.full_name = owner["display_name"] or owner["username"] or email
            seeded.auth_method, seeded.admin = AuthMethod.OIDC, True
            current = seeded
        else:
            seeded.admin = False
            seeded.email = "retired-" + secrets.token_hex(6) + "@mu3lab.invalid"
    session.commit()
    print(
        "MU3LAB_MEALIE_OWNER_OK"
        if current is not None and current.admin
        else "MU3LAB_ERROR Mealie has no administrator"
    )
