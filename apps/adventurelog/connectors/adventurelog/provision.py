# Prints a new AdventureLog API key for one person's account, found by email (Python, on stdin).
import os
import sys

os.chdir("/code")
sys.path.insert(0, "/code")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.mu3lab_settings")

import django  # noqa: E402

django.setup()

from allauth.socialaccount.models import SocialAccount  # noqa: E402
from users.models import APIKey, CustomUser  # noqa: E402

email = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not email:
    raise SystemExit("MU3LAB_ERROR the person's email is missing")
accounts = CustomUser.objects.filter(is_active=True, email__iexact=email)
# The owner also has the bootstrap administrator under the same email; their trips belong to the
# account Authentik signs in, so prefer it.
signed_in = list(accounts.filter(id__in=SocialAccount.objects.values("user_id")).distinct()[:2])
users = signed_in or list(accounts[:2])
if not users:
    raise SystemExit("MU3LAB_ERROR Open AdventureLog and sign in once; chat connects after your account exists")
if len(users) != 1:
    raise SystemExit("MU3LAB_ERROR more than one AdventureLog account has this email")
APIKey.objects.filter(user=users[0], name="Mu3Lab MCP").delete()
_, raw = APIKey.generate(users[0], "Mu3Lab MCP")
print("MU3LAB_OUTPUT api_key=" + raw)
