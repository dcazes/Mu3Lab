# Prints a new AdventureLog API key for the one active superuser (Django shell, on stdin).
from users.models import APIKey, CustomUser

users = list(CustomUser.objects.filter(is_active=True, is_superuser=True)[:2])
if len(users) != 1:
    raise SystemExit("MU3LAB_ERROR expected exactly one active superuser")
APIKey.objects.filter(user=users[0], name="Mu3Lab MCP").delete()
_, raw = APIKey.generate(users[0], "Mu3Lab MCP")
print("MU3LAB_OUTPUT api_key=" + raw)
