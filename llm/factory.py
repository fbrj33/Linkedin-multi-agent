from __future__ import annotations

"""
Builds and caches LLMProvider instances from environment configuration.

Why this module exists: agents need a model per role (a strong model for
planning, a cheap one for scoring) without hardcoding a provider SDK into
agent code, and without paying the cost of a fresh HTTP client per call.
Instances are cached per (provider, model) for that reason — see reset_cache()
if a test needs to flip WIMBEE_LLM_PROVIDER mid-run.
"""

import os

from .base import LLMProvider
from .gemini import GeminiProvider
from .openrouter import OpenRouterProvider

_PROVIDERS = {
    "openrouter": OpenRouterProvider,
    "gemini": GeminiProvider,
}

_cache: dict[tuple[str, str], LLMProvider] = {}


def get_llm(role: str | None = None) -> LLMProvider:
    provider_name = os.getenv("WIMBEE_LLM_PROVIDER", "openrouter").strip().lower()
    if provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown WIMBEE_LLM_PROVIDER '{provider_name}', "
            f"expected one of {sorted(_PROVIDERS)}"
        )

    model = None
    if role:
        model = os.getenv(f"WIMBEE_LLM_MODEL_{role.upper()}")
    model = model or os.getenv("WIMBEE_LLM_MODEL")

    cache_key = (provider_name, model or "")
    if cache_key not in _cache:
        _cache[cache_key] = _PROVIDERS[provider_name](model=model)
    return _cache[cache_key]


def reset_cache() -> None:
    """Test-only: drop cached provider instances so env-var changes take effect."""
    _cache.clear()
