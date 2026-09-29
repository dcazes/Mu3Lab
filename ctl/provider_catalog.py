"""Curated external inference providers accepted by Mu3Lab."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    key_hint: str
    prefix: str = ""
    instructions: str = ""
    probe_models: tuple[str, ...] = ()
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
        value["probe_models"] = list(self.probe_models)
        # Compatibility alias for dashboards that predate routed probes.
        value["example_models"] = list(self.probe_models)
        return value

    @property
    def example_models(self) -> tuple[str, ...]:
        """Compatibility alias used by an already-running dashboard backend."""
        return self.probe_models


PROVIDERS = (
    Provider(
        "cerebras",
        "Cerebras",
        "csk-…",
        "csk-",
        "Create a key in Cerebras Cloud.",
        ("llama-3.3-70b", "qwen-3-32b", "gpt-oss-120b"),
        signup_url="https://cloud.cerebras.ai/",
        keys_url="https://cloud.cerebras.ai/platform/",
        recommended=True,
        free_tier="Very high daily token allowance on fast open models.",
        google_sign_in=True,
    ),
    Provider(
        "google",
        "Google AI Studio",
        "AIza…",
        "AIza",
        "Create a Gemini API key in Google AI Studio.",
        ("gemini-2.5-flash", "gemini-2.5-pro", "gemma-3-27b-it"),
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
        "gsk_",
        "Create a key in the Groq console.",
        ("compound-mini", "compound", "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b"),
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
        "hf_",
        "Create a fine-grained access token in Hugging Face settings.",
        ("meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen3-32B", "openai/gpt-oss-120b"),
        signup_url="https://huggingface.co/join",
        keys_url="https://huggingface.co/settings/tokens",
        free_tier="Small monthly inference credit.",
        google_sign_in=True,
    ),
    Provider(
        "nvidia",
        "NVIDIA",
        "nvapi-…",
        "nvapi-",
        "Create a key in the NVIDIA API Catalog.",
        (
            "nemotron-3-super-120b",
            "nemotron-3-ultra-550b",
            "nemotron-3.5-lightning-30b-a3b",
            "meta/llama-3.3-70b-instruct",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        ),
        signup_url="https://build.nvidia.com/",
        keys_url="https://build.nvidia.com/settings/api-keys",
        free_tier="Rate-limited trial access to NVIDIA-hosted models.",
    ),
    Provider(
        "openrouter",
        "OpenRouter",
        "sk-or-v1-…",
        "sk-or-v1-",
        "Create a key in OpenRouter settings.",
        ("openrouter/free", "meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-coder:free"),
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
        "",
        "Create a key in Mistral La Plateforme.",
        ("mistral-small-latest", "open-mistral-nemo", "codestral-latest"),
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
        "",
        "Create an API key in the Z.ai developer console.",
        ("glm-4.5-flash", "glm-4.5", "glm-4.5-air"),
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
    by_prefix = [provider for provider in PROVIDERS if provider.prefix and key.startswith(provider.prefix)]
    if by_prefix:
        return max(by_prefix, key=lambda provider: len(provider.prefix))
    by_shape = [provider for provider in PROVIDERS if provider.key_pattern and re.match(provider.key_pattern, key)]
    return by_shape[0] if len(by_shape) == 1 else None


def prefix_warning(provider_id: str, api_key: str) -> str:
    provider = get(provider_id)
    if provider.prefix and not api_key.startswith(provider.prefix):
        return f"This key does not use the usual {provider.key_hint} format. Mu3Lab will still verify it live."
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
