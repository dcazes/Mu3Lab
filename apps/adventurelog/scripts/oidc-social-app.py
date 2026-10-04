# Register Authentik as AdventureLog's django-allauth sign-in provider (run by `manage.py shell`).
import os

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

client_id = os.environ["ADVENTURELOG_OIDC_CLIENT_ID"]
app, _ = SocialApp.objects.update_or_create(
    provider="openid_connect",
    provider_id=client_id,
    defaults={
        "name": "Authentik",
        "client_id": client_id,
        "secret": os.environ["ADVENTURELOG_OIDC_CLIENT_SECRET"],
        "settings": {"server_url": os.environ["ADVENTURELOG_OIDC_DISCOVERY_URL"]},
    },
)
app.sites.set(Site.objects.all())
print("MU3LAB_OIDC_APP_OK")
