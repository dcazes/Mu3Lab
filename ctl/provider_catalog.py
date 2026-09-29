"""Curated external inference providers accepted by Mu3Lab."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    key_hint: str
    # Distinctive key prefixes; providers that changed key formats list each one.
    prefixes: tuple[str, ...] = ()
    instructions: str = ""
    signup_url: str = ""
    keys_url: str = ""
    # "email" providers get a pre-generated Vaultwarden sign-up entry;
    # "google" providers use the owner's existing Google account instead.
    account: str = "email"
    recommended: bool = False
    free_tier: str = ""
    # The sign-up page offers "Continue with Google" (checked 2026-09-29).
    google_sign_in: bool = False
    # Mu3Lab can fetch a key through the provider's own sign-in (OAuth) page.
    oauth: bool = False
    # For keys without a distinctive prefix: a best-guess shape, JS-compatible.
    key_pattern: str = ""

    def public(self) -> dict:
        value = asdict(self)
        value["prefixes"] = list(self.prefixes)
        return value


PROVIDERS = (
    Provider(
        "cerebras",
        "Cerebras",
        "csk-…",
        ("csk-",),
        "Create a key in Cerebras Cloud.",
        signup_url="https://cloud.cerebras.ai/",
        keys_url="https://cloud.cerebras.ai/platform/",
        recommended=True,
        free_tier="Very high daily token allowance on fast open models.",
        google_sign_in=True,
    ),
    Provider(
        "google",
        "Google AI Studio",
        "AIza… or AQ.…",
        ("AIza", "AQ."),
        "Create a Gemini API key in Google AI Studio.",
        signup_url="https://aistudio.google.com/",
        keys_url="https://aistudio.google.com/app/apikey",
        account="google",
        recommended=True,
        free_tier="Gemini free tier; uses your existing Google account.",
        google_sign_in=True,
    ),
    Provider(
        "groq",
        "Groq",
        "gsk_…",
        ("gsk_",),
        "Create a key in the Groq console.",
        signup_url="https://console.groq.com/",
        keys_url="https://console.groq.com/keys",
        recommended=True,
        free_tier="Generous per-day request limits across many open models.",
        google_sign_in=True,
    ),
    Provider(
        "huggingface",
        "Hugging Face",
        "hf_…",
        ("hf_",),
        "Create a fine-grained access token in Hugging Face settings.",
        signup_url="https://huggingface.co/join",
        keys_url="https://huggingface.co/settings/tokens",
        free_tier="Small monthly inference credit.",
        google_sign_in=True,
    ),
    Provider(
        "nvidia",
        "NVIDIA",
        "nvapi-…",
        ("nvapi-",),
        "Create a key in the NVIDIA API Catalog.",
        signup_url="https://build.nvidia.com/",
        keys_url="https://build.nvidia.com/settings/api-keys",
        free_tier="Rate-limited trial access to NVIDIA-hosted models.",
    ),
    Provider(
        "openrouter",
        "OpenRouter",
        "sk-or-v1-…",
        ("sk-or-v1-",),
        "Create a key in OpenRouter settings.",
        signup_url="https://openrouter.ai/",
        keys_url="https://openrouter.ai/settings/keys",
        free_tier="Free model variants with a low daily request cap.",
        google_sign_in=True,
        oauth=True,
    ),
    Provider(
        "mistral",
        "Mistral",
        "Paste the key from Mistral Console",
        ("mstrl",),
        "Create a key in Mistral La Plateforme.",
        signup_url="https://console.mistral.ai/",
        keys_url="https://console.mistral.ai/api-keys",
        free_tier="Free experiment plan; requires phone verification.",
        google_sign_in=True,
        key_pattern=r"^[A-Za-z0-9]{32}$",
    ),
    Provider(
        "zhipu",
        "Z.ai",
        "Paste the key from Z.ai",
        (),
        "Create an API key in the Z.ai developer console.",
        signup_url="https://z.ai/",
        keys_url="https://z.ai/manage-apikey/apikey-list",
        free_tier="Free GLM Flash models.",
        key_pattern=r"^[0-9a-f]{32}\.[A-Za-z0-9]{16}$",
    ),
)

BY_ID = {provider.id: provider for provider in PROVIDERS}
# One verified provider completes setup; this many recommended providers give
# chat enough free capacity to fail over when one tier is rate-limited.
RECOMMENDED_MINIMUM = 2
ALIASES = {"zai": "zhipu", "z-ai": "zhipu", "z.ai": "zhipu", "google-studio": "google"}


def canonical_id(value: str) -> str:
    normalized = value.strip().lower()
    return ALIASES.get(normalized, normalized)


def get(provider_id: str) -> Provider:
    canonical = canonical_id(provider_id)
    try:
        return BY_ID[canonical]
    except KeyError as exc:
        raise ValueError("provider is not in the Mu3Lab curated catalog") from exc


def catalog() -> list[dict]:
    return [provider.public() for provider in PROVIDERS]


def detect(api_key: str) -> Provider | None:
    """Guess the provider from the key itself: a distinctive prefix first, then a shape."""
    key = api_key.strip()
    matches = [
        (len(prefix), provider) for provider in PROVIDERS for prefix in provider.prefixes if key.startswith(prefix)
    ]
    if matches:
        return max(matches, key=lambda match: match[0])[1]
    by_shape = [provider for provider in PROVIDERS if provider.key_pattern and re.match(provider.key_pattern, key)]
    return by_shape[0] if len(by_shape) == 1 else None


def prefix_warning(provider_id: str, api_key: str) -> str:
    provider = get(provider_id)
    if provider.prefixes and not api_key.startswith(provider.prefixes):
        return f"This key does not use the usual {provider.key_hint} format. Mu3Lab will still verify it live."
    return ""


MAX_KEY_LENGTH = 256


def key_problem(api_key: str) -> str:
    """Catch pastes that cannot be an API key (mirrors the dashboard's check).

    A wrong clipboard is the usual cause: a sentence, a link or several lines.
    """
    key = api_key.strip()
    if not key:
        return "Paste an API key."
    if any(char.isspace() for char in key) or "://" in key or len(key) > MAX_KEY_LENGTH:
        return "That doesn't look like an API key. Copy the key itself from the provider's keys page and paste again."
    return ""


def setup_progress(connections: list[dict]) -> dict:
    """Summarize provider setup: one verified provider is enough to finish,
    but Mu3Lab keeps recommending the most generous free tiers until
    RECOMMENDED_MINIMUM of them are verified."""
    verified = {
        canonical_id(str(item.get("provider_id", "")))
        for item in connections
        if item.get("enabled") and item.get("state") == "verified"
    }
    recommended = [provider.id for provider in PROVIDERS if provider.recommended]
    recommended_verified = [provider_id for provider_id in recommended if provider_id in verified]
    return {
        "verified": len(verified),
        "complete": bool(verified),
        "recommended": recommended,
        "recommended_verified": recommended_verified,
        "recommended_minimum": RECOMMENDED_MINIMUM,
        "recommendation_met": len(recommended_verified) >= RECOMMENDED_MINIMUM,
    }
