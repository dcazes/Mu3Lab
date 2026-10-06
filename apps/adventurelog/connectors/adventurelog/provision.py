# Prints a new AdventureLog API key for the owner's account (Django shell, on stdin).
from allauth.socialaccount.models import SocialAccount
from users.models import APIKey, CustomUser

superusers = CustomUser.objects.filter(is_active=True, is_superuser=True)
# The bootstrap administrator and the owner's Authentik sign-in are separate superusers;
# the owner's trips belong to the one Authentik signs in, so prefer it.
signed_in = list(superusers.filter(id__in=SocialAccount.objects.values("user_id")).distinct()[:2])
users = signed_in if signed_in else list(superusers[:2])
if len(users) != 1:
    raise SystemExit("MU3LAB_ERROR expected exactly one active superuser")
APIKey.objects.filter(user=users[0], name="Mu3Lab MCP").delete()
_, raw = APIKey.generate(users[0], "Mu3Lab MCP")
print("MU3LAB_OUTPUT api_key=" + raw)
