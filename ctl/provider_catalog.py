"""Curated external inference providers accepted by Mu3Lab."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    key_hint: str
    prefix: str = ""
    instructions: str = ""
    probe_models: tuple[str, ...] = ()

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
    Provider("cerebras", "Cerebras", "csk-…", "csk-", "Create a key in Cerebras Cloud.",
             ("llama-3.3-70b", "qwen-3-32b", "gpt-oss-120b")),
    Provider("google", "Google AI Studio", "AIza…", "AIza", "Create a Gemini API key in Google AI Studio.",
             ("gemini-2.5-flash", "gemini-2.5-pro", "gemma-3-27b-it")),
    Provider("groq", "Groq", "gsk_…", "gsk_", "Create a key in the Groq console.",
             ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b")),
    Provider("huggingface", "Hugging Face", "hf_…", "hf_", "Create a fine-grained access token in Hugging Face settings.",
             ("meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen3-32B", "openai/gpt-oss-120b")),
    Provider("nvidia", "NVIDIA", "nvapi-…", "nvapi-", "Create a key in the NVIDIA API Catalog.",
             ("meta/llama-3.3-70b-instruct", "nvidia/llama-3.1-nemotron-ultra-253b-v1", "qwen/qwen3-235b-a22b")),
    Provider("openrouter", "OpenRouter", "sk-or-v1-…", "sk-or-v1-", "Create a key in OpenRouter settings.",
             ("openrouter/free", "meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-coder:free")),
    Provider("mistral", "Mistral", "Paste the key from Mistral Console", "", "Create a key in Mistral La Plateforme.",
             ("mistral-small-latest", "open-mistral-nemo", "codestral-latest")),
    Provider("zhipu", "Z.ai", "Paste the key from Z.ai", "", "Create an API key in the Z.ai developer console.",
             ("glm-4.5-flash", "glm-4.5", "glm-4.5-air")),
)

BY_ID = {provider.id: provider for provider in PROVIDERS}
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


def prefix_warning(provider_id: str, api_key: str) -> str:
    provider = get(provider_id)
    if provider.prefix and not api_key.startswith(provider.prefix):
        return f"This key does not use the usual {provider.key_hint} format. Mu3Lab will still verify it live."
    return ""
