# Prints a Paperless API token for one person's account, found by email (Python, on stdin).
# Nobody is a Paperless superuser (scripts/roles.py), so the token sees what that person sees.
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mu3lab_settings")

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from rest_framework.authtoken.models import Token  # noqa: E402

email = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not email:
    raise SystemExit("MU3LAB_ERROR the owner's email is missing")
users = list(get_user_model().objects.filter(is_active=True, email__iexact=email)[:2])
if len(users) != 1:
    raise SystemExit("MU3LAB_ERROR expected exactly one active Paperless account for the owner")
token, _ = Token.objects.get_or_create(user=users[0])
print("MU3LAB_OUTPUT api_key=" + token.key)
