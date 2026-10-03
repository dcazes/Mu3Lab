# Prints MU3LAB_ACCOUNT_OK when the expected Django superuser exists (run by `manage.py shell`).
from django.contrib.auth import get_user_model

user = get_user_model().objects.filter(username=USERNAME).first()  # noqa: F821 - prepended by Mu3Lab
print("MU3LAB_ACCOUNT_OK" if user and user.is_superuser else "MU3LAB_ERROR the administrator account was not created")
