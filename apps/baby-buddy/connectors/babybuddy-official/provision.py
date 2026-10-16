# Prints the owner's Baby Buddy API key. The owner's user is created (as the trusted sign-in would)
# if they have not opened Baby Buddy yet; an existing key is reused because a phone may use it.
import os
import sys

os.chdir("/app/www/public")
sys.path.insert(0, "/app/www/public")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "babybuddy.settings.base")

import django  # noqa: E402

django.setup()

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.authtoken.models import Token  # noqa: E402

username = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not username:
    raise SystemExit("MU3LAB_ERROR the owner's username is missing")
user, _ = User.objects.get_or_create(username=username)
if not (user.is_staff and user.is_superuser):
    user.is_staff = user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
token, _ = Token.objects.get_or_create(user=user)
print("MU3LAB_OUTPUT api_token=" + token.key)
