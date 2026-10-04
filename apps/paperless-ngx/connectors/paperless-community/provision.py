# Prints a Paperless API token for the one active superuser (Django shell, on stdin).
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

users = list(get_user_model().objects.filter(is_active=True, is_superuser=True)[:2])
if len(users) != 1:
    raise SystemExit("MU3LAB_ERROR expected exactly one active superuser")
token, _ = Token.objects.get_or_create(user=users[0])
print("MU3LAB_OUTPUT api_key=" + token.key)
