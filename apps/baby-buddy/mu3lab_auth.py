"""Authorize identities admitted by the private Authentik application."""

from django.contrib.auth.backends import RemoteUserBackend


class AuthentikRemoteUserBackend(RemoteUserBackend):
    """Create distinct Baby Buddy users with the app's full caregiver role."""

    def configure_user(self, request, user, created=True):
        user = super().configure_user(request, user, created)
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
        return user
