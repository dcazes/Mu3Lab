"""Keep Paperless defaults, adding verified provider linking and a POST launcher."""

from paperless.settings import *  # noqa: F403

# Authentication by email remains opt-in per provider in SOCIALACCOUNT_PROVIDERS.
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
MIDDLEWARE = [*MIDDLEWARE, "mu3lab_onboarding.LoginMiddleware"]  # noqa: F405
