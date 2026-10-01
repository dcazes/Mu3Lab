"""Start allauth through a CSRF-protected POST without its confirmation screen."""

import base64
import hashlib

from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.html import escape

SCRIPT = "document.getElementById('login').submit();"


class LoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != "/__mu3lab/login" or request.method != "GET":
            return self.get_response(request)
        if request.user.is_authenticated:
            return HttpResponseRedirect("/")
        token = escape(get_token(request))
        response = HttpResponse(
            '<!doctype html><html><head><meta charset="utf-8"><title>Opening Paperless</title></head>'
            '<body><form id="login" method="post" action="/accounts/oidc/authentik/login/">'
            f'<input type="hidden" name="csrfmiddlewaretoken" value="{token}">'
            '<button type="submit">Continue to Paperless</button></form>'
            f"<script>{SCRIPT}</script></body></html>",
        )
        digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
        response["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'sha256-{digest}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response["Cache-Control"] = "no-store"
        # Not "no-referrer": browsers then send "Origin: null" with the form
        # POST and Django's CSRF check answers 403 Forbidden.
        response["Referrer-Policy"] = "same-origin"
        return response
